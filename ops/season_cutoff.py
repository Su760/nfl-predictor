"""Cutoff-native, research-only reconstruction of the existing four EPA features.

Actual historical publication timestamps are unavailable. Every game uses the
explicit kickoff+24h final-availability proxy; current corrected PBP stays grade C.
No forecast, production policy, source input or old research row is overwritten.
"""

from __future__ import annotations

import hashlib
import json
import math
import tomllib
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import polars as pl
from season_rebuild import RebuildRow, _row_bytes, _row_from_json
from week1_live import canonical, digest, stamp, write_once

from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.features.policy import load_feature_policy
from nfl_predictor.features.team_strength import PointInTimeRatingService, regressed_prior
from nfl_predictor.ratings.base import TeamGameEpa
from nfl_predictor.ratings.epa import fit_opponent_adjusted_epa
from nfl_predictor.runtime.capture import NflverseFootballNormalizer

CODE_ROOT = Path(__file__).resolve().parents[1]
FEATURE_INDICES = (11, 14, 17, 20)
FEATURE_NAMES = ("off_epa_diff", "def_epa_diff", "pass_epa_diff", "rush_epa_diff")
BUILD_DEPENDENCIES = (
    "ops/season_cutoff.py",
    "ops/season_rebuild.py",
    "ops/week1_live.py",
    "src/nfl_predictor/contracts/enums.py",
    "src/nfl_predictor/features/policy.py",
    "src/nfl_predictor/ratings/epa.py",
    "src/nfl_predictor/ratings/base.py",
    "src/nfl_predictor/features/team_strength.py",
    "src/nfl_predictor/runtime/capture.py",
    "src/nfl_predictor/models/epa_logistic.py",
    "src/nfl_predictor/models/calibration.py",
    "configs/model_policy_v1.toml",
    "configs/season_probability.toml",
)


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def select_history(games, target, cutoff):
    """Strict final proxy before cutoff; never use target or future-season rows."""
    if cutoff >= datetime.fromisoformat(target["kickoff"]):
        raise ValueError("PREGAME_CUTOFF_REQUIRED")
    selected = [
        g
        for g in games
        if g["game_id"] != target["game_id"]
        and g["season"] in (target["season"] - 1, target["season"])
        and datetime.fromisoformat(g["available_at"]) < cutoff
    ]
    return sorted(selected, key=lambda g: (g["available_at"], g["game_id"]))


def aggregate_plays(frame, games, aliases):
    """Reuse exact original EPA aggregation semantics, independently of QB fields."""
    by_id = {g["game_id"]: g for g in games}
    if len(by_id) != len(games):
        raise ValueError("DUPLICATE_SCHEDULE_GAME")
    columns = ["game_id", "play_id", "posteam", "defteam", "pass_attempt", "rush_attempt", "epa"]
    selected = frame.select(columns).filter(
        pl.col("game_id").is_in(list(by_id)),
        pl.col("posteam").is_not_null(),
        pl.col("defteam").is_not_null(),
        pl.col("epa").is_not_null(),
        pl.col("pass_attempt").is_not_null(),
        pl.col("rush_attempt").is_not_null(),
    )
    groups = defaultdict(list)
    seen = set()
    for row in selected.to_dicts():
        for key in ("play_id", "pass_attempt", "rush_attempt"):
            value = row[key]
            if value is None or not math.isfinite(value) or value != int(value):
                raise ValueError("NONINTEGRAL_PLAY_FIELD")
            row[key] = int(value)
        key = (row["game_id"], row["play_id"])
        if key in seen:
            raise ValueError("DUPLICATE_GAME_PLAY")
        seen.add(key)
        if not math.isfinite(row["epa"]):
            raise ValueError("NONFINITE_EPA")
        offense, defense = (aliases.get(row[k], row[k]) for k in ("posteam", "defteam"))
        game = by_id[row["game_id"]]
        if offense == defense or {offense, defense} != {game["home"], game["away"]}:
            raise ValueError("PBP_TEAM_SCHEDULE_MISMATCH")
        groups[(row["game_id"], offense, defense)].append(row)
    result = []
    for (gid, offense, defense), plays in sorted(groups.items()):
        values = NflverseFootballNormalizer._epa_payload(plays)
        result.append(
            TeamGameEpa(
                gid,
                offense,
                defense,
                values["offense_epa_per_play"],
                values["pass_epa_per_play"],
                values["rush_epa_per_play"],
                values["plays"],
                datetime.fromisoformat(by_id[gid]["available_at"]),
            )
        )
    directions = defaultdict(set)
    for r in result:
        directions[r.canonical_event_id].add((r.offense_team, r.defense_team))
    missing = []
    for gid, game in by_id.items():
        expected = {(game["home"], game["away"]), (game["away"], game["home"])}
        if directions[gid] != expected:
            missing.append(gid)
    return result, sorted(missing)


def fitted_epa(rows, cutoff, policy):
    return tuple(
        fit_opponent_adjusted_epa(rows, cutoff, name, policy.epa_ridge_alpha)
        for name in ("offense_epa_per_play", "pass_epa_per_play", "rush_epa_per_play")
    )


def prior_values(fits, team, policy):
    off, passing, rushing = fits
    # Preserve existing prior semantics, including defensive coefficient direction.
    maps = (off.offense, off.defense, passing.offense, rushing.offense)
    return tuple(
        regressed_prior(
            values.get(team),
            sum(values.values()) / len(values) if values else 0.0,
            policy.offseason_regression_to_mean,
        )
        for values in maps
    )


def feature_values(target, current_rows, prior_fits, cutoff, policy):
    current_rows = [
        r
        for r in current_rows
        if r.finalized_at_utc < cutoff and r.canonical_event_id != target["game_id"]
    ]
    fits = fitted_epa(current_rows, cutoff, policy)
    return values_from_fits(target, current_rows, prior_fits, fits, policy)


def values_from_fits(target, current_rows, prior_fits, fits, policy):
    off, passing, rushing = fits
    blend = PointInTimeRatingService(policy=policy)._blend
    team_values = {}
    for team in (target["home"], target["away"]):
        offense_n = sum(r.offense_team == team for r in current_rows)
        defense_n = sum(r.defense_team == team for r in current_rows)
        prior = prior_values(prior_fits, team, policy)
        raw = (
            off.league_mean + off.offense.get(team, 0.0),
            off.league_mean - off.defense.get(team, 0.0),
            passing.league_mean + passing.offense.get(team, 0.0),
            rushing.league_mean + rushing.offense.get(team, 0.0),
        )
        team_values[team] = tuple(
            blend(value, p, n)
            for value, p, n in zip(
                raw, prior, (offense_n, defense_n, offense_n, offense_n), strict=True
            )
        )
    return tuple(
        h - a for h, a in zip(team_values[target["home"]], team_values[target["away"]], strict=True)
    )


def build(cfg, games):
    native = cfg["native"]
    root = Path(cfg["output_root"]).expanduser() / "cutoff-native"
    research = Path(cfg["research_root"]).expanduser()
    policy_path = CODE_ROOT / native["feature_policy"]
    policy = load_feature_policy(policy_path)
    frozen_source = json.loads(Path(cfg["probability_freeze"]).expanduser().read_text())
    if file_sha(frozen_source["source_path"]) != frozen_source["source_sha256"]:
        raise ValueError("FROZEN_SCHEDULE_HASH_MISMATCH")
    for g in games:
        expected = datetime.fromisoformat(g["kickoff"]) + timedelta(
            hours=cfg["historical_result_delay_hours"]
        )
        if datetime.fromisoformat(g["available_at"]) != expected:
            raise ValueError("AVAILABILITY_PROXY_MISMATCH")
    raw_paths = [
        research / "raw" / "pbp" / f"season={season}" / "data.arrow"
        for season in range(native["start_season"] - 1, native["end_season"] + 1)
    ]
    sources = {str(p): file_sha(p) for p in raw_paths}
    dependencies = (*BUILD_DEPENDENCIES, native["feature_policy"])
    identity = {
        "schema": "cutoff-native-epa-v1",
        "config": cfg,
        "sources": sources,
        "schedule_sha256": frozen_source["source_sha256"],
        "normalized_games_sha256": digest(canonical(games)),
        "code": {p: file_sha(CODE_ROOT / p) for p in dependencies},
        "packages": {name: version(name) for name in ("numpy", "polars", "scikit-learn")},
        "features": FEATURE_NAMES,
        "original_indices": FEATURE_INDICES,
        "evidence_grade": "C",
        "availability_basis": "kickoff_plus_24h_proxy_not_historical_publication",
        "production_change": "NONE",
    }
    build_id = digest(canonical(identity))
    output = root / build_id
    declaration = output / "declaration.json"
    if not declaration.exists():
        write_once(declaration, {**identity, "declared_at": stamp(datetime.now(UTC))})
    print("Native build declared", build_id, flush=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["datasets"].values():
            if file_sha(item["path"]) != item["sha256"]:
                raise ValueError("NATIVE_DATASET_HASH_MISMATCH")
        return manifest
    aliases = tomllib.loads((CODE_ROOT / "configs/season_probability.toml").read_text())[
        "team_aliases"
    ]
    aggregates = {}
    missing = set()
    for season, path in zip(
        range(native["start_season"] - 1, native["end_season"] + 1), raw_paths, strict=True
    ):
        frame = pl.read_ipc(
            path,
            columns=[
                "game_id",
                "play_id",
                "posteam",
                "defteam",
                "pass_attempt",
                "rush_attempt",
                "epa",
            ],
        )
        aggregates[season], gaps = aggregate_plays(
            frame, [g for g in games if g["season"] == season], aliases
        )
        missing.update(gaps)
        print(
            "Aggregated",
            season,
            len(aggregates[season]),
            "team games; missing",
            len(gaps),
            flush=True,
        )
    all_rows = {h: [] for h in cfg["horizons"]}
    exclusions, lineage_hashes = [], []
    for season in range(native["start_season"], native["end_season"] + 1):
        season_games = [g for g in games if g["season"] in (season - 1, season)]
        targets = [g for g in season_games if g["season"] == season and g["game_type"] == "REG"]
        input_manifest = {
            "schema": "cutoff-native-input-manifest-v1",
            "build_id": build_id,
            "target_season": season,
            "evidence_grade": "C",
            "historical_published_at": None,
            "availability_basis": identity["availability_basis"],
            "raw_inputs": {
                frozen_source["source_path"]: frozen_source["source_sha256"],
                str(raw_paths[season - native["start_season"]]): sources[
                    str(raw_paths[season - native["start_season"]])
                ],
                str(raw_paths[season - native["start_season"] + 1]): sources[
                    str(raw_paths[season - native["start_season"] + 1])
                ],
            },
        }
        input_manifest_sha = digest(canonical(input_manifest))
        write_once(output / "input-manifests" / (input_manifest_sha + ".json"), input_manifest)
        state_cache, prior_cache = {}, {}
        for horizon, seconds in cfg["horizons"].items():
            for target in targets:
                cutoff = datetime.fromisoformat(target["kickoff"]) - timedelta(seconds=seconds)
                history = select_history(season_games, target, cutoff)
                ids = {g["game_id"] for g in history}
                if ids & missing:
                    exclusions.append(
                        {
                            "game_id": target["game_id"],
                            "season": season,
                            "horizon": horizon,
                            "reason": "INCOMPLETE_PBP_HISTORY",
                            "missing_game_ids": sorted(ids & missing),
                        }
                    )
                    continue
                prior_ids = tuple(
                    sorted(g["game_id"] for g in history if g["season"] == season - 1)
                )
                current_ids = tuple(sorted(g["game_id"] for g in history if g["season"] == season))
                if not prior_ids:
                    raise ValueError("PREVIOUS_SEASON_PRIOR_REQUIRED")
                if prior_ids not in prior_cache:
                    prior_cache[prior_ids] = fitted_epa(
                        [r for r in aggregates[season - 1] if r.canonical_event_id in prior_ids],
                        cutoff,
                        policy,
                    )
                if current_ids not in state_cache:
                    rows = [r for r in aggregates[season] if r.canonical_event_id in current_ids]
                    state_cache[current_ids] = (rows, fitted_epa(rows, cutoff, policy))
                rows, fits = state_cache[current_ids]
                values = values_from_fits(target, rows, prior_cache[prior_ids], fits, policy)
                lineage = {
                    "schema": "native-cutoff-selection-v1",
                    "game_id": target["game_id"],
                    "season": season,
                    "horizon": horizon,
                    "cutoff": stamp(cutoff),
                    "prior_game_ids": prior_ids,
                    "current_game_ids": current_ids,
                    "latest_available_at": max(g["available_at"] for g in history),
                    "availability_basis": identity["availability_basis"],
                    "build_id": build_id,
                    "features": dict(zip(FEATURE_NAMES, values, strict=True)),
                }
                sha = digest(canonical(lineage))
                write_once(output / "selections" / (sha + ".json"), lineage)
                lineage_hashes.append(sha)
                rebuilt = RebuildRow(
                    target["game_id"],
                    season,
                    target["week"],
                    datetime.fromisoformat(target["kickoff"]),
                    target["result"],
                    values,
                    sha,
                    (input_manifest_sha,),
                    ProvenanceGrade.C,
                )
                all_rows[horizon].append(rebuilt)
            print(
                "Built",
                season,
                horizon,
                sum(r.season == season for r in all_rows[horizon]),
                "games",
                flush=True,
            )
    datasets = {}
    for horizon, rows in all_rows.items():
        payload = b"".join(_row_bytes(r) + b"\n" for r in rows)
        path = output / (horizon + ".jsonl")
        write_once(path, payload)
        datasets[horizon] = {
            "path": str(path),
            "sha256": digest(payload),
            "rows": len(rows),
            "by_season": dict(Counter(r.season for r in rows)),
        }
    manifest = {
        "build_id": build_id,
        "declaration": str(declaration),
        "datasets": datasets,
        "feature_indices": FEATURE_INDICES,
        "feature_names": FEATURE_NAMES,
        "exclusions": exclusions,
        "lineage_sha256": digest(canonical(sorted(lineage_hashes))),
        "evidence_grade": "C",
        "production_change": "NONE",
    }
    manifest = json.loads(canonical(manifest))
    write_once(manifest_path, manifest)
    return manifest


def load_rows(manifest, horizon):
    item = manifest["datasets"][horizon]
    if file_sha(item["path"]) != item["sha256"]:
        raise ValueError("NATIVE_DATASET_HASH_MISMATCH")
    rows = [_row_from_json(line) for line in Path(item["path"]).read_text().splitlines()]
    if len(rows) != item["rows"] or len({r.event_id for r in rows}) != len(rows):
        raise ValueError("NATIVE_DATASET_COUNT_MISMATCH")
    declaration = json.loads(Path(manifest["declaration"]).read_text())
    seconds = declaration["config"]["horizons"][horizon]
    verified_inputs = set()
    for row in rows:
        for sha in row.input_manifest_sha256s:
            if sha not in verified_inputs:
                source_manifest = (
                    Path(manifest["declaration"]).parent / "input-manifests" / (sha + ".json")
                )
                if file_sha(source_manifest) != sha:
                    raise ValueError("NATIVE_INPUT_MANIFEST_HASH_MISMATCH")
                verified_inputs.add(sha)
        path = Path(manifest["declaration"]).parent / "selections" / (row.snapshot_id + ".json")
        body = path.read_bytes()
        if digest(body) != row.snapshot_id:
            raise ValueError("NATIVE_LINEAGE_HASH_MISMATCH")
        lineage = json.loads(body)
        expected = stamp(row.kickoff_at_utc - timedelta(seconds=seconds))
        if (
            lineage["horizon"] != horizon
            or lineage["game_id"] != row.event_id
            or lineage["cutoff"] != expected
            or lineage["latest_available_at"] >= expected
            or row.event_id in lineage["prior_game_ids"] + lineage["current_game_ids"]
            or tuple(lineage["features"][name] for name in FEATURE_NAMES) != row.features
        ):
            raise ValueError("NATIVE_LINEAGE_CUTOFF_MISMATCH")
    return rows
