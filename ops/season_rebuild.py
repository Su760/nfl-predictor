"""Reproducible, Grade-C historical rebuild and matched 2025 challenger evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import nflreadpy  # type: ignore[import-untyped]
import numpy as np
import polars as pl

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.lineage import CaptureManifest, NormalizedFact
from nfl_predictor.evaluation.metrics import (
    multiclass_brier,
    multinomial_log_loss,
    straight_up_accuracy,
)
from nfl_predictor.evaluation.splits import SeasonFold, outer_fold
from nfl_predictor.features.builder import FeatureBuilder
from nfl_predictor.features.policy import load_feature_policy
from nfl_predictor.features.schema import FEATURE_NAMES_V1
from nfl_predictor.features.team_strength import PointInTimeRatingService
from nfl_predictor.models.baselines import load_model_policy, rating_baselines_v1
from nfl_predictor.models.calibration import CALIBRATOR_REGISTRY
from nfl_predictor.models.epa_logistic import EpaLogisticModel
from nfl_predictor.models.tie import TieLayer, to_three_way
from nfl_predictor.runtime.capture import NflverseFootballNormalizer

CODE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RebuildRow:
    event_id: str
    season: int
    week: int
    kickoff_at_utc: datetime
    result: str
    features: tuple[float, ...]
    snapshot_id: str
    input_manifest_sha256s: tuple[str, ...]
    provenance_grade: ProvenanceGrade


class _UnusedFactStore:
    def for_event_features(self, event: object, cutoff: datetime) -> list[NormalizedFact]:
        raise AssertionError("rebuild always supplies its explicit reconstructed facts")


class _ReconstructionVenueStore:
    def __init__(self, target: Mapping[str, object], altitude_by_stadium: Mapping[str, float]) -> None:
        stadium_id = str(target["stadium_id"])
        if stadium_id not in altitude_by_stadium:
            raise ValueError(f"historical venue altitude is missing for {stadium_id}")
        self.event_id = str(target["game_id"])
        self.altitude = float(altitude_by_stadium[stadium_id])
        self.surface = str(target["surface"] or "").lower()
        self.roof = str(target["roof"] or "").lower()
        identity = json.dumps(
            [self.event_id, self.altitude, self.surface, self.roof], separators=(",", ":")
        ).encode()
        self.csv_sha256 = hashlib.sha256(identity).hexdigest()

    def features(self, event: Any, cutoff: datetime) -> dict[str, float]:
        if event.source_event_ids.get("nflverse") != self.event_id:
            raise ValueError("reconstruction venue does not match event")
        return {
            "venue_altitude_m": self.altitude,
            "surface_turf": float("turf" in self.surface or "synthetic" in self.surface),
            "roof_capable": float(self.roof in {"dome", "closed", "retractable"}),
        }


def chronological_fold(holdout_season: int, available_seasons: Iterable[int]) -> SeasonFold:
    return outer_fold(holdout_season, available_seasons)


def _parse_time(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("kickoff timestamps must include an offset")
    return parsed.astimezone(UTC)


def eligible_history_ids(
    rows: Sequence[Mapping[str, object]], target_id: str
) -> set[str]:
    matches = [row for row in rows if row.get("game_id") == target_id]
    if len(matches) != 1:
        raise ValueError("target game must occur exactly once")
    target_time = _parse_time(matches[0]["kickoff_at_utc"])
    return {
        str(row["game_id"])
        for row in rows
        if str(row["game_id"]) != target_id
        and _parse_time(row["kickoff_at_utc"]).date() < target_time.date()
    }


def _matrix(rows: Sequence[RebuildRow]) -> np.ndarray:
    return np.asarray([row.features for row in rows], dtype=np.float64)


def _row_bytes(row: RebuildRow) -> bytes:
    return json.dumps(
        {
            **asdict(row),
            "kickoff_at_utc": row.kickoff_at_utc.isoformat(),
            "provenance_grade": row.provenance_grade.value,
        },
        sort_keys=True,
    ).encode()


def _row_from_json(payload: str | bytes) -> RebuildRow:
    value = json.loads(payload)
    return RebuildRow(
        **{
            **value,
            "kickoff_at_utc": datetime.fromisoformat(value["kickoff_at_utc"]),
            "features": tuple(value["features"]),
            "input_manifest_sha256s": tuple(value["input_manifest_sha256s"]),
            "provenance_grade": ProvenanceGrade(value["provenance_grade"]),
        }
    )


def _model(name: str, policy: Any) -> Any:
    if name == "elo":
        return rating_baselines_v1(policy)["elo"]
    return EpaLogisticModel((11, 14, 17, 20), policy.logistic.c, policy.logistic.max_iter, policy.random_seed)


def _non_ties(rows: Sequence[RebuildRow]) -> list[RebuildRow]:
    return [row for row in rows if row.result != "tie"]


def choose_calibrator_family(
    rows: Sequence[RebuildRow], holdout_season: int, model_name: str = "epa_logistic"
) -> tuple[str, tuple[int, ...]]:
    policy = load_model_policy(CODE_ROOT / "configs/model_policy_v1.toml")
    available = sorted({row.season for row in rows if row.season < holdout_season})
    target_seasons = [season for season in available if season >= available[0] + 2]
    scores: dict[str, list[float]] = {family: [] for family in CALIBRATOR_REGISTRY}
    used: list[int] = []
    for target in target_seasons:
        fold = outer_fold(target, available)
        estimator = [row for row in rows if row.season in fold.estimator_seasons]
        calibration = _non_ties([row for row in rows if row.season == fold.calibration_season])
        target_rows = _non_ties([row for row in rows if row.season == target])
        if not estimator or len({row.result for row in estimator if row.result != "tie"}) < 2:
            continue
        if len({row.result for row in calibration}) < 2 or not target_rows:
            continue
        model = _model(model_name, policy).fit(_matrix(estimator), [row.result for row in estimator])
        calibration_raw = model.predict_r_home(_matrix(calibration)).tolist()
        target_raw = model.predict_r_home(_matrix(target_rows)).tolist()
        calibration_labels = [int(row.result == "home") for row in calibration]
        target_labels = [int(row.result == "home") for row in target_rows]
        for family, calibrator_type in CALIBRATOR_REGISTRY.items():
            calibrator = calibrator_type.from_policy(policy).fit(calibration_raw, calibration_labels)
            probabilities = calibrator.transform(target_raw).tolist()
            epsilon = policy.calibration.logit_epsilon
            loss = -sum(
                label * np.log(max(epsilon, value))
                + (1 - label) * np.log(max(epsilon, 1 - value))
                for value, label in zip(probabilities, target_labels, strict=True)
            ) / len(target_labels)
            scores[family].append(float(loss))
        used.extend((fold.calibration_season, fold.test_season))
    eligible = [(sum(values) / len(values), family) for family, values in scores.items() if values]
    return (min(eligible)[1] if eligible else "identity", tuple(sorted(set(used))))


def evaluation_report(rows: Sequence[RebuildRow], holdout_season: int) -> dict[str, Any]:
    policy = load_model_policy(CODE_ROOT / "configs/model_policy_v1.toml")
    fold = chronological_fold(holdout_season, {row.season for row in rows})
    estimator = [row for row in rows if row.season in fold.estimator_seasons]
    calibration = _non_ties([row for row in rows if row.season == fold.calibration_season])
    holdout = [row for row in rows if row.season == holdout_season]
    tie = TieLayer.fit([row.result for row in (*estimator, *calibration)])
    reports: dict[str, Any] = {}
    families: dict[str, str] = {}
    selected_seasons: set[int] = set()
    for name in ("elo", "epa_logistic"):
        family, model_selection_seasons = choose_calibrator_family(
            rows, holdout_season, model_name=name
        )
        families[name] = family
        selected_seasons.update(model_selection_seasons)
        model = _model(name, policy).fit(_matrix(estimator), [row.result for row in estimator])
        calibration_raw = model.predict_r_home(_matrix(calibration)).tolist()
        calibrator = CALIBRATOR_REGISTRY[family].from_policy(policy).fit(
            calibration_raw, [int(row.result == "home") for row in calibration]
        )
        conditional = calibrator.transform(model.predict_r_home(_matrix(holdout)).tolist()).tolist()
        probabilities = [to_three_way(value, tie.p_tie) for value in conditional]
        outcomes = [row.result for row in holdout]
        reports[name] = {
            "multinomial_log_loss": multinomial_log_loss(probabilities, outcomes),
            "multiclass_brier": multiclass_brier(probabilities, outcomes),
            "straight_up_accuracy": straight_up_accuracy(probabilities, outcomes),
            "event_ids": [row.event_id for row in holdout],
        }
    return {
        "holdout_season": holdout_season,
        "matched_games": len(holdout),
        "fold": asdict(fold),
        "calibrator_family_by_model": families,
        "calibrator_selection_seasons": tuple(sorted(selected_seasons)),
        "evidence_basis": "historical_reconstruction",
        "evidence_grade": "C",
        "promotion_eligible": False,
        "promotion_blockers": ["HISTORICAL_RECONSTRUCTION_GRADE_C"],
        "models": reports,
    }


def _bytes(frame: pl.DataFrame) -> bytes:
    stream = BytesIO()
    frame.write_ipc(stream)
    return stream.getvalue()


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable cache conflict: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def capture(config: Mapping[str, Any], root: Path) -> None:
    loaders = {"schedules": nflreadpy.load_schedules, "pbp": nflreadpy.load_pbp}
    captured_seasons = 0
    for season in range(config["capture_start_season"], config["end_season"] + 1):
        missing = any(
            not (root / "raw" / dataset / f"season={season}" / "data.arrow").exists()
            for dataset in config["datasets"]
        )
        if not missing:
            continue
        if captured_seasons >= int(config["capture_seasons_per_run"]):
            break
        for dataset in config["datasets"]:
            destination = root / "raw" / dataset / f"season={season}" / "data.arrow"
            if destination.exists():
                continue
            started = datetime.now(UTC)
            frame = loaders[dataset](seasons=[season])
            payload = _bytes(frame)
            received = datetime.now(UTC)
            _write_once(destination, payload)
            receipt = {
                "source": "nflverse",
                "dataset": dataset,
                "season": season,
                "request_started_at_utc": started.isoformat(),
                "response_received_at_utc": received.isoformat(),
                "raw_sha256": hashlib.sha256(payload).hexdigest(),
                "evidence_grade": "C",
                "reason": "CURRENT_RETRIEVAL_WITHOUT_HISTORICAL_PUBLICATION_PROOF",
            }
            _write_once(destination.with_name("receipt.json"), json.dumps(receipt, sort_keys=True).encode())
        captured_seasons += 1


def _kickoffs(frame: pl.DataFrame) -> dict[str, datetime]:
    from zoneinfo import ZoneInfo

    return {
        row["game_id"]: datetime.fromisoformat(f"{row['gameday']}T{row['gametime']}")
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .astimezone(UTC)
        for row in frame.to_dicts()
    }


def _manifest(path: Path, root: Path, received: datetime, run_id: str) -> CaptureManifest:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    return CaptureManifest(
        capture_id=digest,
        run_id=run_id,
        source="nflverse-reconstruction",
        request_fingerprint=hashlib.sha256(run_id.encode()).hexdigest(),
        request_started_at_utc=received - timedelta(microseconds=1),
        response_received_at_utc=received,
        http_status=200,
        raw_path=str(path.relative_to(root)),
        raw_payload_sha256=digest,
        response_headers_allowlisted={},
        code_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=CODE_ROOT, text=True
        ).strip(),
        dependency_lock_sha256=hashlib.sha256((CODE_ROOT / "uv.lock").read_bytes()).hexdigest(),
        schema_version="historical-reconstruction-v1",
    )


def _persist_manifest(path: Path, manifest: CaptureManifest) -> str:
    payload = manifest.model_dump_json(indent=2).encode()
    _write_once(path, payload)
    return hashlib.sha256(payload).hexdigest()


def _load_or_create_manifest(
    manifest_path: Path, raw_path: Path, root: Path, received: datetime, run_id: str
) -> CaptureManifest:
    if manifest_path.exists():
        return CaptureManifest.model_validate_json(manifest_path.read_bytes())
    return _manifest(raw_path, root, received, run_id)


def _selection_payload(
    dataset: str,
    target_id: str,
    eligible_ids: set[str],
    derived_payload: bytes,
    parent_paths: Sequence[Path],
) -> bytes:
    value = {
        "schema_version": "historical-selection-v1",
        "dataset": dataset,
        "target_event_id": target_id,
        "eligible_game_ids": sorted(eligible_ids),
        "derived_payload_sha256": hashlib.sha256(derived_payload).hexdigest(),
        "parent_raw_sha256s": sorted(
            hashlib.sha256(path.read_bytes()).hexdigest() for path in parent_paths
        ),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _write_coverage(root: Path, config: Mapping[str, Any], rows: Sequence[RebuildRow], exclusions: list[dict[str, Any]]) -> None:
    by_reason: dict[str, int] = {}
    for item in exclusions:
        reason = str(item["reason"])
        by_reason[reason] = by_reason.get(reason, 0) + 1
    payload = {
        "included_games": len(rows),
        "excluded_games": len(exclusions),
        "excluded_by_reason": by_reason,
        "exclusions": exclusions,
    }
    destination = root / config["coverage_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build(config: Mapping[str, Any], root: Path) -> list[RebuildRow]:
    feature_policy = load_feature_policy(CODE_ROOT / config["feature_policy"])
    normalizer = NflverseFootballNormalizer(feature_policy)
    complete_seasons = {
        season
        for season in range(config["capture_start_season"], config["end_season"] + 1)
        if all(
            (root / "raw" / dataset / f"season={season}" / "data.arrow").is_file()
            for dataset in config["datasets"]
        )
    }
    frames = {
        dataset: {
            season: pl.read_ipc(root / "raw" / dataset / f"season={season}" / "data.arrow")
            for season in complete_seasons
        }
        for dataset in config["datasets"]
    }
    received = datetime.now(UTC)
    rows: list[RebuildRow] = []
    exclusions: list[dict[str, Any]] = []
    limit = int(config["max_games"])
    for season in range(config["start_season"], config["end_season"] + 1):
        if season - 1 not in complete_seasons or season not in complete_seasons:
            break
        schedule_columns = [
            "game_id", "season", "game_type", "week", "gameday", "gametime", "away_team",
            "home_team", "away_score", "home_score", "location", "div_game", "stadium_id",
            "roof", "surface",
        ]
        schedule_frames = [
            frames["schedules"][value].select(schedule_columns).with_columns(
                pl.col("season").cast(pl.Int64), pl.col("week").cast(pl.Int64),
                pl.col("away_score").cast(pl.Int64), pl.col("home_score").cast(pl.Int64),
                pl.col("div_game").cast(pl.Int64),
            )
            for value in (season - 1, season)
        ]
        pbp_columns = [
            "game_id", "play_id", "posteam", "defteam", "passer_player_id", "pass_attempt",
            "rush_attempt", "epa", "qb_epa", "cpoe",
        ]
        pbp_frames = [
            frames["pbp"][value].select(pbp_columns).with_columns(
                pl.col("play_id").cast(pl.Float64), pl.col("pass_attempt").cast(pl.Float64),
                pl.col("rush_attempt").cast(pl.Float64), pl.col("epa").cast(pl.Float64),
                pl.col("qb_epa").cast(pl.Float64), pl.col("cpoe").cast(pl.Float64),
            )
            for value in (season - 1, season)
        ]
        schedules = pl.concat(schedule_frames)
        pbp = pl.concat(pbp_frames)
        kickoffs = _kickoffs(schedules)
        targets = schedules.filter(pl.col("season") == season, pl.col("game_type") == "REG")
        for target in targets.sort(["week", "gameday", "gametime", "game_id"]).to_dicts():
            target_id = target["game_id"]
            derived = root / "selections-v2" / f"season={season}" / target_id
            row_path = derived / "row.json"
            if row_path.exists():
                rows.append(_row_from_json(row_path.read_bytes()))
                if limit and len(rows) >= limit:
                    _write_coverage(root, config, rows, exclusions)
                    return rows
                continue
            stadium_id = str(target["stadium_id"] or "")
            if str(target["location"]).lower() == "neutral" or stadium_id not in config["altitude_m_by_stadium_id"]:
                exclusions.append(
                    {
                        "event_id": target_id,
                        "season": season,
                        "week": int(target["week"]),
                        "reason": "UNSUPPORTED_HISTORICAL_VENUE",
                        "stadium_id": stadium_id,
                    }
                )
                continue
            target_time = kickoffs[target_id]
            history = {
                game_id
                for game_id, kickoff in kickoffs.items()
                if kickoff.date() < target_time.date()
            }
            selected = schedules.filter(pl.col("game_id").is_in(sorted(history | {target_id})))
            if selected.filter(~pl.col("div_game").is_in([0, 1])).height:
                raise ValueError("div_game contains values outside 0/1")
            selected = selected.with_columns(
                pl.when(pl.col("game_id") == target_id).then(None).otherwise(pl.col("home_score")).alias("home_score"),
                pl.when(pl.col("game_id") == target_id).then(None).otherwise(pl.col("away_score")).alias("away_score"),
                pl.col("div_game").cast(pl.Boolean),
            )
            selected_pbp = pbp.filter(
                pl.col("game_id").is_in(sorted(history)),
                pl.col("posteam").is_not_null(), pl.col("defteam").is_not_null(),
                pl.col("epa").is_not_null(), pl.col("qb_epa").is_not_null(), pl.col("cpoe").is_not_null(),
                pl.col("pass_attempt").is_not_null(), pl.col("rush_attempt").is_not_null(),
                (pl.col("pass_attempt") == 0) | pl.col("passer_player_id").is_not_null(),
            )
            for column in ("play_id", "pass_attempt", "rush_attempt"):
                nonintegral = selected_pbp.filter(pl.col(column) != pl.col(column).floor()).height
                if nonintegral:
                    raise ValueError(f"{column} contains non-integral provider values")
            selected_pbp = selected_pbp.with_columns(
                pl.col("play_id").cast(pl.Int64),
                pl.col("pass_attempt").cast(pl.Int64),
                pl.col("rush_attempt").cast(pl.Int64),
            )
            schedule_payload, pbp_payload = _bytes(selected), _bytes(selected_pbp)
            schedule_path, pbp_path = derived / "schedules.json", derived / "pbp.json"
            schedule_parents = tuple(
                root / "raw" / "schedules" / f"season={value}" / "data.arrow"
                for value in (season - 1, season)
            )
            pbp_parents = tuple(
                root / "raw" / "pbp" / f"season={value}" / "data.arrow"
                for value in (season - 1, season)
            )
            _write_once(
                schedule_path,
                _selection_payload("schedules", target_id, history | {target_id}, schedule_payload, schedule_parents),
            )
            _write_once(
                pbp_path,
                _selection_payload("pbp", target_id, history, pbp_payload, pbp_parents),
            )
            schedule_manifest_path = derived / "schedules.manifest.json"
            pbp_manifest_path = derived / "pbp.manifest.json"
            schedule_manifest = _load_or_create_manifest(
                schedule_manifest_path,
                schedule_path,
                root,
                received,
                f"rebuild-{target_id}-schedules",
            )
            pbp_manifest = _load_or_create_manifest(
                pbp_manifest_path, pbp_path, root, received, f"rebuild-{target_id}-pbp"
            )
            schedule_manifest_sha = _persist_manifest(
                schedule_manifest_path, schedule_manifest
            )
            pbp_manifest_sha = _persist_manifest(pbp_manifest_path, pbp_manifest)
            events = normalizer.normalize_events(schedule_payload, schedule_manifest)
            event = next(item for item in events if item.source_event_ids["nflverse"] == target_id)
            facts = normalizer.normalize_capture(
                schedule_payload, schedule_manifest, pbp_payload, pbp_manifest, event
            )
            facts = tuple(fact.model_copy(update={"provenance_grade": ProvenanceGrade.C}) for fact in facts)
            builder = FeatureBuilder(
                _UnusedFactStore(),
                feature_policy,
                _ReconstructionVenueStore(target, config["altitude_m_by_stadium_id"]),
                PointInTimeRatingService(policy=feature_policy),
            )
            reconstruction_at = max(
                schedule_manifest.response_received_at_utc,
                pbp_manifest.response_received_at_utc,
            ) + timedelta(seconds=1)
            snapshot = builder.build(event, Origin.T72, reconstruction_at, "replay", facts)
            result = "tie" if target["home_score"] == target["away_score"] else "home" if target["home_score"] > target["away_score"] else "away"
            rebuilt = RebuildRow(
                target_id, season, int(target["week"]), target_time, result,
                tuple(float(snapshot.values[name]) for name in FEATURE_NAMES_V1), snapshot.snapshot_id,
                tuple(sorted({schedule_manifest_sha, pbp_manifest_sha})),
                ProvenanceGrade.C,
            )
            _write_once(row_path, _row_bytes(rebuilt))
            rows.append(rebuilt)
            if limit and len(rows) >= limit:
                _write_coverage(root, config, rows, exclusions)
                return rows
    _write_coverage(root, config, rows, exclusions)
    return rows


def _save_rows(path: Path, rows: Sequence[RebuildRow]) -> None:
    payload = b"".join(_row_bytes(row) + b"\n" for row in rows)
    object_path = path.parent / "objects" / (hashlib.sha256(payload).hexdigest() + ".jsonl")
    _write_once(object_path, payload)
    _replace(path, payload)


def _replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _evaluation_code_sha256(config: Mapping[str, Any]) -> str:
    paths = (
        Path(__file__),
        CODE_ROOT / "uv.lock",
        CODE_ROOT / config["model_policy"],
        CODE_ROOT / "src/nfl_predictor/evaluation/metrics.py",
        CODE_ROOT / "src/nfl_predictor/evaluation/splits.py",
        CODE_ROOT / "src/nfl_predictor/models/baselines.py",
        CODE_ROOT / "src/nfl_predictor/models/calibration.py",
        CODE_ROOT / "src/nfl_predictor/models/epa_logistic.py",
        CODE_ROOT / "src/nfl_predictor/models/tie.py",
        CODE_ROOT / "src/nfl_predictor/ratings/elo.py",
    )
    return hashlib.sha256(
        json.dumps(
            {str(path.relative_to(CODE_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def evaluate_if_changed(
    config: Mapping[str, Any], root: Path, config_payload: bytes
) -> tuple[dict[str, Any], bool]:
    """Create one immutable evaluation for each exact dataset/coverage/code/config identity."""
    dataset_path = root / config["dataset_path"]
    dataset_payload = dataset_path.read_bytes()
    dataset_sha256 = hashlib.sha256(dataset_payload).hexdigest()
    _write_once(dataset_path.parent / "objects" / (dataset_sha256 + ".jsonl"), dataset_payload)
    coverage_payload = (root / config["coverage_path"]).read_bytes()
    coverage_sha256 = hashlib.sha256(coverage_payload).hexdigest()
    coverage = json.loads(coverage_payload)
    identity = {
        "schema_version": "v2-challenger-experiment-v2",
        "dataset_sha256": dataset_sha256,
        "coverage_sha256": coverage_sha256,
        "evaluation_code_sha256": _evaluation_code_sha256(config),
        "config_sha256": hashlib.sha256(config_payload).hexdigest(),
    }
    signature = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    index = root / config["evaluation_index_path"] / (signature + ".json")
    if index.exists():
        reference = json.loads(index.read_text())
        record_path = root / config["evaluation_records_path"] / (reference["record_sha256"] + ".json")
        record_payload = record_path.read_bytes()
        if hashlib.sha256(record_payload).hexdigest() != reference["record_sha256"]:
            raise ValueError("EVALUATION_RECORD_HASH_MISMATCH")
        record = json.loads(record_payload)
        if record["experiment"] != {**identity, "signature": signature}:
            raise ValueError("EVALUATION_IDENTITY_MISMATCH")
        _replace(root / config["report_path"], json.dumps(record, indent=2, sort_keys=True).encode() + b"\n")
        return record, True
    rows = [_row_from_json(line) for line in dataset_payload.splitlines()]
    report = evaluation_report(rows, int(config["holdout_season"]))
    report["coverage"] = coverage
    record = {**report, "experiment": {**identity, "signature": signature}}
    record_payload = json.dumps(record, sort_keys=True, separators=(",", ":"), default=list).encode()
    record_sha = hashlib.sha256(record_payload).hexdigest()
    _write_once(root / config["evaluation_records_path"] / (record_sha + ".json"), record_payload)
    _write_once(index, json.dumps({"record_sha256": record_sha}, sort_keys=True).encode())
    _replace(root / config["report_path"], json.dumps(record, indent=2, sort_keys=True).encode() + b"\n")
    return record, False


def _load_config(path: Path) -> tuple[dict[str, Any], Path]:
    config = tomllib.loads(path.read_text())
    root = Path(config["data_root"]).expanduser().resolve()
    if not root.is_absolute() or CODE_ROOT == root or CODE_ROOT in root.parents:
        raise ValueError("research data root must be absolute and outside the code checkout")
    return config, root


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("capture", "build", "evaluate", "evaluate-if-changed", "all")
    )
    parser.add_argument("--config", type=Path, default=CODE_ROOT / "configs/season_rebuild.toml")
    parser.add_argument("--max-games", type=int)
    args = parser.parse_args(argv)
    config, root = _load_config(args.config)
    if args.max_games is not None:
        if args.max_games < 1:
            raise ValueError("--max-games must be positive")
        config["max_games"] = args.max_games
    root.mkdir(parents=True, exist_ok=True)
    dataset_path = root / config["dataset_path"]
    if args.command in {"capture", "all"}:
        capture(config, root)
    if args.command in {"build", "all"}:
        rows = build(config, root)
        _save_rows(dataset_path, rows)
    if args.command in {"evaluate", "evaluate-if-changed", "all"}:
        report, reused = evaluate_if_changed(config, root, args.config.read_bytes())
        print(json.dumps({**report, "experiment_reused": reused}, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
