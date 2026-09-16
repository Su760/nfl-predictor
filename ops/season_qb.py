"""Research-only quarterback residual challenger with point-in-time evidence guards."""

from __future__ import annotations

import copy
import io
import itertools
import json
import math
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import polars as pl
import season_sources
import week1_live


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("TIMESTAMP_MUST_BE_UTC")
    return parsed.astimezone(UTC)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("CLOCK_MUST_BE_UTC")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sigmoid(value: float) -> float:
    if value >= 0:
        term = math.exp(-value)
        return 1.0 / (1.0 + term)
    term = math.exp(value)
    return term / (1.0 + term)


def _row_feature(row: dict[str, Any]) -> float:
    kickoff = _time(row["kickoff"])
    if _time(row["feature_as_of"]) >= kickoff:
        raise ValueError("QB_FEATURE_NOT_PREGAME")
    values = [
        row["home_qb_value"],
        row["home_team_reference"],
        row["away_qb_value"],
        row["away_team_reference"],
    ]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("QB_FEATURE_INVALID")
    return (float(values[0]) - float(values[1])) - (float(values[2]) - float(values[3]))


def _baseline_logit(row: dict[str, Any]) -> float:
    home = float(row["baseline_p_home"])
    away = float(row["baseline_p_away"])
    if not 0 < home < 1 or not 0 < away < 1:
        raise ValueError("BASELINE_PROBABILITY_INVALID")
    return math.log(home / away)


def _fit_coefficient(
    rows: list[dict[str, Any]], lower: float, upper: float, iterations: int
) -> float:
    decisive = []
    for row in rows:
        target = float(row["result_home"])
        if target not in {0.0, 0.5, 1.0}:
            raise ValueError("QB_RESULT_INVALID")
        _row_feature(row)
        _baseline_logit(row)
        if target != 0.5:
            decisive.append(row)
    if not decisive:
        raise ValueError("QB_DEVELOPMENT_DECISIVE_EMPTY")
    coefficient = 0.0
    for _ in range(iterations):
        gradient = 0.0
        curvature = 0.0
        for row in decisive:
            target = float(row["result_home"])
            if target not in {0.0, 0.5, 1.0}:
                raise ValueError("QB_RESULT_INVALID")
            feature = _row_feature(row)
            probability = _sigmoid(_baseline_logit(row) + coefficient * feature)
            gradient += feature * (target - probability)
            curvature -= feature * feature * probability * (1.0 - probability)
        if curvature == 0:
            break
        updated = min(upper, max(lower, coefficient - gradient / curvature))
        if abs(updated - coefficient) < 1e-12:
            coefficient = updated
            break
        coefficient = updated
    return coefficient


def _score(rows: list[dict[str, Any]], coefficient: float) -> dict[str, Any]:
    baseline_losses, adjusted_losses, baseline_brier, adjusted_brier = [], [], [], []
    correct_baseline = correct_adjusted = decisive_count = 0
    for row in rows:
        target = float(row["result_home"])
        tie = float(row.get("baseline_p_tie", 0.0))
        baseline = (float(row["baseline_p_away"]), tie, float(row["baseline_p_home"]))
        home_share = _sigmoid(_baseline_logit(row) + coefficient * _row_feature(row))
        adjusted = ((1.0 - tie) * (1.0 - home_share), tie, (1.0 - tie) * home_share)
        actual = (
            (1.0, 0.0, 0.0)
            if target == 0
            else (0.0, 1.0, 0.0)
            if target == 0.5
            else (0.0, 0.0, 1.0)
        )
        index = actual.index(1.0)
        baseline_losses.append(-math.log(max(baseline[index], 1e-15)))
        adjusted_losses.append(-math.log(max(adjusted[index], 1e-15)))
        baseline_brier.append(sum((p - y) ** 2 for p, y in zip(baseline, actual, strict=True)))
        adjusted_brier.append(sum((p - y) ** 2 for p, y in zip(adjusted, actual, strict=True)))
        if target != 0.5:
            decisive_count += 1
            correct_baseline += (baseline[2] >= baseline[0]) == (target == 1.0)
            correct_adjusted += (adjusted[2] >= adjusted[0]) == (target == 1.0)
    count = len(rows)
    mean = lambda values: sum(values) / count if count else None
    return {
        "games": count,
        "baseline_log_loss": mean(baseline_losses),
        "qb_log_loss": mean(adjusted_losses),
        "delta_log_loss": mean(adjusted_losses) - mean(baseline_losses) if count else None,
        "baseline_brier": mean(baseline_brier),
        "qb_brier": mean(adjusted_brier),
        "delta_brier": mean(adjusted_brier) - mean(baseline_brier) if count else None,
        "accuracy_sample_size": decisive_count,
        "ties": count - decisive_count,
        "baseline_accuracy": correct_baseline / decisive_count if decisive_count else None,
        "qb_accuracy": correct_adjusted / decisive_count if decisive_count else None,
    }


def _validate_stat(row: dict[str, Any]) -> None:
    epa = row.get("passing_epa")
    if epa is not None and (
        isinstance(epa, bool) or not isinstance(epa, (int, float)) or not math.isfinite(epa)
    ):
        raise ValueError("QB_EPA_INVALID")
    attempts = row.get("attempts")
    if attempts is not None and (
        isinstance(attempts, bool)
        or not isinstance(attempts, (int, float))
        or not math.isfinite(attempts)
        or attempts < 0
        or int(attempts) != attempts
    ):
        raise ValueError("QB_ATTEMPTS_INVALID")


def prepare_training_rows(
    baselines: list[dict[str, Any]],
    player_rows: list[dict[str, Any]],
    historical_games: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create lagged research rows from finals available strictly before each cutoff."""
    aliases = cfg.get("team_aliases", {})
    games_by_key = {}
    for game in historical_games:
        if int(game["season"]) < int(cfg.get("history_start", 2016)):
            continue
        if game.get("status") != "FINAL" and game.get("result") not in {"home", "away", "tie"}:
            continue
        home = aliases.get(game["home"], game["home"])
        away = aliases.get(game["away"], game["away"])
        for team, opponent in ((home, away), (away, home)):
            key = (int(game["season"]), int(game["week"]), team, opponent)
            if key in games_by_key:
                raise ValueError("QB_HISTORY_GAME_KEY_AMBIGUOUS")
            games_by_key[key] = game
    stats_by_game: dict[str, list[dict[str, Any]]] = {}
    availability = {}
    seen_player_games = set()
    for row in player_rows:
        if row.get("position") != "QB" or row.get("season_type") != "REG":
            continue
        team = aliases.get(row.get("recent_team"), row.get("recent_team"))
        opponent = aliases.get(row.get("opponent_team"), row.get("opponent_team"))
        game = games_by_key.get((int(row["season"]), int(row["week"]), team, opponent))
        if game is None:
            continue
        identity = (game["game_id"], row.get("player_id"))
        if identity in seen_player_games:
            raise ValueError("QB_PLAYER_GAME_DUPLICATE")
        seen_player_games.add(identity)
        _validate_stat(row)
        stats_by_game.setdefault(game["game_id"], []).append({**row, "recent_team": team})
        availability[game["game_id"]] = _time(game["available_at"])
    events = sorted(stats_by_game, key=lambda game_id: (availability[game_id], game_id))
    primary = sorted(
        (row for row in baselines if row.get("horizon") == cfg["primary_horizon"]),
        key=lambda row: (_time(row["cutoff"]), row["game_id"]),
    )
    player: dict[str, dict[str, float]] = {}
    team: dict[str, dict[str, float]] = {}
    prepared, event_index = [], 0
    for base in primary:
        cutoff = _time(base["cutoff"])
        while event_index < len(events) and availability[events[event_index]] < cutoff:
            for stat in stats_by_game[events[event_index]]:
                attempts, epa = int(stat.get("attempts") or 0), stat.get("passing_epa")
                if (
                    isinstance(stat.get("player_id"), str)
                    and attempts > 0
                    and isinstance(epa, (int, float))
                ):
                    for store, identity in (
                        (player, stat["player_id"]),
                        (team, stat["recent_team"]),
                    ):
                        values = store.setdefault(
                            identity, {"attempts": 0.0, "epa": 0.0, "games": 0.0}
                        )
                        values["attempts"] += attempts
                        values["epa"] += float(epa)
                        values["games"] += 1
            event_index += 1
        home_id, away_id = base.get("home_qb_id"), base.get("away_qb_id")
        home_team, away_team = base["home"], base["away"]
        if (
            home_id not in player
            or away_id not in player
            or home_team not in team
            or away_team not in team
        ):
            continue
        support = [player[home_id], player[away_id]]
        if any(
            value["attempts"] < int(cfg["minimum_qb_attempts"])
            or value["games"] < int(cfg["minimum_qb_games"])
            for value in support
        ):
            continue
        prepared.append(
            {
                "game_id": base["game_id"],
                "season": int(base["season"]),
                "kickoff": base["kickoff"],
                "feature_as_of": base["cutoff"],
                "baseline_p_home": base["p_home"],
                "baseline_p_away": base["p_away"],
                "baseline_p_tie": base["p_tie"],
                "result_home": 1.0
                if base["result"] == "home"
                else 0.0
                if base["result"] == "away"
                else 0.5,
                "home_qb_epa": player[home_id]["epa"],
                "home_qb_attempts": player[home_id]["attempts"],
                "away_qb_epa": player[away_id]["epa"],
                "away_qb_attempts": player[away_id]["attempts"],
                "home_team_reference": team[home_team]["epa"] / team[home_team]["attempts"],
                "away_team_reference": team[away_team]["epa"] / team[away_team]["attempts"],
            }
        )
    development_attempts = sorted(
        value
        for row in prepared
        if int(row["season"]) <= int(cfg["development_end_season"])
        for value in (row["home_qb_attempts"], row["away_qb_attempts"])
    )
    if not development_attempts:
        raise ValueError("QB_DEVELOPMENT_SUPPORT_EMPTY")
    middle = len(development_attempts) // 2
    shrinkage = (
        development_attempts[middle]
        if len(development_attempts) % 2
        else (development_attempts[middle - 1] + development_attempts[middle]) / 2
    )
    for row in prepared:
        row["home_qb_value"] = (row.pop("home_qb_epa") + shrinkage * row["home_team_reference"]) / (
            row.pop("home_qb_attempts") + shrinkage
        )
        row["away_qb_value"] = (row.pop("away_qb_epa") + shrinkage * row["away_team_reference"]) / (
            row.pop("away_qb_attempts") + shrinkage
        )
    return prepared, {
        "expected_starter_evidence_grade": "C",
        "historical_qb_identity": "nflverse games actual starter ID; retrospective, not expected pregame starter",
        "availability_rule": "source final available_at strictly before forecast cutoff",
        "shrinkage_method": "median positive lagged QB attempts in development only",
        "shrinkage_attempts": shrinkage,
    }


def fit_qb(
    rows: list[dict[str, Any]], cfg: dict[str, Any], lineage: dict[str, Any], code_sha: str, clock
) -> dict[str, Any]:
    """Fit one QB residual coefficient on frozen development rows only."""
    freeze = int(cfg["experiment_freeze_season"])
    if freeze != 2026:
        raise ValueError("QB_EXPERIMENT_FREEZE_INVALID")
    ordered = sorted(rows, key=lambda row: (_time(row["kickoff"]), row["game_id"]))
    if len({row["game_id"] for row in ordered}) != len(ordered):
        raise ValueError("QB_DUPLICATE_GAME")
    development = [
        row for row in ordered if int(row["season"]) <= int(cfg["development_end_season"])
    ]
    validation = [row for row in ordered if int(row["season"]) == int(cfg["validation_season"])]
    benchmark = [row for row in ordered if int(row["season"]) == int(cfg["known_benchmark_season"])]
    if not development:
        raise ValueError("QB_DEVELOPMENT_EMPTY")
    coefficient = _fit_coefficient(
        development,
        float(cfg["qb_coefficient_min"]),
        float(cfg["qb_coefficient_max"]),
        int(cfg["qb_fit_iterations"]),
    )
    grade = str(lineage.get("expected_starter_evidence_grade", "MISSING"))
    eligibility = (
        "INELIGIBLE_FOR_PROMOTION" if grade != "A" else "SHADOW_ONLY_PENDING_PROSPECTIVE_EVALUATION"
    )
    artifact = {
        "schema_version": "qb-residual-v1",
        "model_version": cfg["qb_model_version"],
        "production_policy_sha256": cfg["production_policy_sha256"],
        "created_at": _stamp(clock()),
        "data_as_of": cfg["qb_state_as_of"],
        "through_season": int(cfg["qb_state_through_season"]),
        "experiment_freeze_season": freeze,
        "splits": {
            "development": f"<={cfg['development_end_season']}",
            "validation": str(cfg["validation_season"]),
            "known_benchmark": str(cfg["known_benchmark_season"]),
            "prospective": f">={freeze}",
        },
        "coefficient": coefficient,
        "tie_fit_policy": "fixed tie mass; decisive-game conditional likelihood only",
        "player_values": cfg.get("player_values", {}),
        "team_references": cfg.get("team_references", {}),
        "minimum_qb_games": int(cfg["minimum_qb_games"]),
        "minimum_qb_attempts": int(cfg["minimum_qb_attempts"]),
        "qb_shrinkage_attempts": float(lineage["shrinkage_attempts"]),
        "metrics": {
            "development": _score(development, coefficient),
            "validation": _score(validation, coefficient),
            "known_benchmark": _score(benchmark, coefficient),
        },
        "lineage": lineage,
        "eligibility": eligibility,
        "code_sha": code_sha,
        "data_sha256": _hash(ordered),
        "config_sha256": _hash(cfg),
    }
    artifact["artifact_id"] = _hash(artifact)
    return artifact


def verify_artifact(artifact: dict[str, Any], production_policy_sha256: str) -> None:
    identity = {key: value for key, value in artifact.items() if key != "artifact_id"}
    if artifact.get("artifact_id") != _hash(identity):
        raise ValueError("QB_ARTIFACT_ID_INVALID")
    if artifact.get("production_policy_sha256") != production_policy_sha256:
        raise ValueError("QB_PRODUCTION_POLICY_MISMATCH")


def load_artifact(path: Path, production_policy_sha256: str) -> dict[str, Any]:
    artifact = json.loads(path.read_bytes())
    if not isinstance(artifact, dict):
        raise TypeError("QB_ARTIFACT_INVALID")
    verify_artifact(artifact, production_policy_sha256)
    return artifact


def write_artifact(root: Path, artifact: dict[str, Any]) -> Path:
    payload = _canonical(artifact)
    verify_artifact(artifact, artifact["production_policy_sha256"])
    path = root / "research" / "qb" / "artifacts" / f"{artifact['artifact_id']}.json"
    week1_live.write_once(path, payload)
    return path


def refresh_state(
    artifact: dict[str, Any],
    cfg: dict[str, Any],
    root: Path,
    finalized_games: list[dict[str, Any]],
    clock,
) -> dict[str, Any]:
    """Refresh lagged QB state without refitting the frozen coefficient."""
    body, receipt = season_sources._fetch(cfg["qb_player_stats_url"], cfg, root, clock, "parquet")
    frame = pl.read_parquet(io.BytesIO(body))
    required = {
        "player_id",
        "player_name",
        "recent_team",
        "opponent_team",
        "position",
        "season",
        "week",
        "season_type",
        "attempts",
        "passing_epa",
    }
    if not required.issubset(frame.columns):
        raise ValueError("QB_PLAYER_STATS_SCHEMA_INVALID")
    components = [receipt]
    frames = [frame.select(sorted(required))]
    for source in cfg.get("qb_state_supplements", []):
        raw, captured = season_sources._fetch(source["url"], cfg, root, clock, "parquet")
        current = pl.read_parquet(io.BytesIO(raw))
        if "recent_team" not in current.columns and "team" in current.columns:
            current = current.rename({"team": "recent_team"})
        if not required.issubset(current.columns):
            raise ValueError("QB_PLAYER_STATS_SCHEMA_INVALID")
        if set(current["season"].unique().to_list()) != {source["season"]}:
            raise ValueError("QB_SUPPLEMENT_SEASON_MISMATCH")
        # Never silently replace overlapping legacy rows; duplicate checks below fail closed.
        frames.append(current.select(sorted(required)))
        components.append(captured)
    if any(_time(c["captured_at"]) > clock() for c in components):
        raise ValueError("QB_STATE_CAPTURE_FROM_FUTURE")
    frame = pl.concat(frames, how="vertical_relaxed")
    if len(components) > 1:
        buffer = io.BytesIO()
        frame.write_parquet(buffer)
        body = buffer.getvalue()
        receipt = {
            "source_url": cfg["qb_player_stats_url"],
            "captured_at": max(c["captured_at"] for c in components),
            "source_last_modified": None,
            "raw_sha256": sha256(body).hexdigest(),
            "component_captures": components,
            "state_source_policy": "legacy-plus-explicit-weekly-seasons-v1",
        }
        week1_live.write_once(root / "raw" / f"{receipt['raw_sha256']}.parquet", body)
        week1_live.write_once(root / "captures" / f"{_hash(receipt)}.json", receipt)
    through = int(cfg["qb_state_through_season"])
    aliases = cfg.get("team_aliases", {})
    final_keys = set()
    included_game_ids = []
    for game in finalized_games:
        if game.get("status") != "FINAL" or int(game["season"]) > through:
            continue
        final_keys.add(
            (
                int(game["season"]),
                int(game["week"]),
                aliases.get(game["home"], game["home"]),
                aliases.get(game["away"], game["away"]),
            )
        )
        final_keys.add(
            (
                int(game["season"]),
                int(game["week"]),
                aliases.get(game["away"], game["away"]),
                aliases.get(game["home"], game["home"]),
            )
        )
    rows = frame.filter(
        (pl.col("position") == "QB")
        & (pl.col("season_type") == "REG")
        & (pl.col("season") <= through)
        & (pl.col("season") >= int(cfg.get("history_start", 2016)))
        & (pl.col("attempts") > 0)
        & pl.col("passing_epa").is_not_null()
    ).with_columns(
        pl.col("recent_team").replace(aliases),
        pl.col("opponent_team").replace(aliases),
    )
    rows = rows.filter(
        pl.struct(["season", "week", "recent_team", "opponent_team"]).map_elements(
            lambda value: (
                (
                    int(value["season"]),
                    int(value["week"]),
                    value["recent_team"],
                    value["opponent_team"],
                )
                in final_keys
            ),
            return_dtype=pl.Boolean,
        )
    )
    seen = set()
    for row in rows.to_dicts():
        identity = (int(row["season"]), int(row["week"]), row["player_id"])
        if identity in seen:
            raise ValueError("QB_PLAYER_GAME_DUPLICATE")
        seen.add(identity)
        _validate_stat(row)
    present = {
        (int(row["season"]), int(row["week"]), row["recent_team"], row["opponent_team"])
        for row in rows.select(["season", "week", "recent_team", "opponent_team"])
        .unique()
        .to_dicts()
    }
    included_game_ids = sorted(
        game["game_id"]
        for game in finalized_games
        if game.get("status") == "FINAL"
        and int(game["season"]) <= through
        and (
            int(game["season"]),
            int(game["week"]),
            aliases.get(game["home"], game["home"]),
            aliases.get(game["away"], game["away"]),
        )
        in present
        and (
            int(game["season"]),
            int(game["week"]),
            aliases.get(game["away"], game["away"]),
            aliases.get(game["home"], game["home"]),
        )
        in present
    )
    team_totals = rows.group_by("recent_team").agg(
        pl.col("passing_epa").sum(), pl.col("attempts").sum()
    )
    team_references = {
        item["recent_team"]: float(item["passing_epa"]) / int(item["attempts"])
        for item in team_totals.to_dicts()
        if item["recent_team"] is not None and int(item["attempts"]) > 0
    }
    player_totals = rows.group_by("player_id").agg(
        pl.col("player_name").drop_nulls().last(),
        pl.col("recent_team").drop_nulls().last(),
        pl.col("passing_epa").sum(),
        pl.col("attempts").sum(),
        pl.len().alias("games"),
    )
    player_values = {}
    for item in player_totals.to_dicts():
        team = item["recent_team"]
        reference = team_references.get(team)
        attempts = int(item["attempts"])
        if not isinstance(reference, float) or attempts <= 0:
            continue
        player_values[item["player_id"]] = {
            "player_name": item["player_name"],
            "epa": float(item["passing_epa"]),
            "games": int(item["games"]),
            "attempts": attempts,
            "as_of": receipt["captured_at"],
        }
    updated = {key: value for key, value in artifact.items() if key != "artifact_id"}
    updated.update(
        {
            "player_values": player_values,
            "team_references": team_references,
            "data_as_of": receipt["captured_at"],
            "through_season": through,
            "state_source_url": receipt["source_url"],
            "state_raw_sha256": receipt["raw_sha256"],
            "state_source_last_modified": receipt.get("source_last_modified"),
            "state_source_components": components,
            "state_source_policy": receipt.get("state_source_policy", "legacy-single-source"),
            "included_game_ids": sorted(included_game_ids),
        }
    )
    updated["artifact_id"] = _hash(updated)
    write_artifact(root, updated)
    return updated


def _fallback(
    baseline: dict[str, Any], reason: str, evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "status": "FALLBACK",
        "p_home": baseline["p_home"],
        "p_away": baseline["p_away"],
        "p_tie": baseline["p_tie"],
        "adjustment_logit": 0.0,
        "evidence": evidence or {},
        "limitations": [reason],
    }


def _out_conflict(game: dict[str, Any], team: str, player: dict[str, Any]) -> bool:
    injuries = game.get("inputs", {}).get("injuries", {})
    if injuries.get("status") != "AVAILABLE":
        return False
    name = player.get("player_name")
    for row in injuries.get("data", []):
        if not isinstance(row, dict):
            continue
        row_team = row.get("team") or row.get("team_abbreviation")
        status = str(row.get("game_status") or row.get("GameStatus") or "").upper()
        row_name = row.get("player") or row.get("player_name") or row.get("Player")
        if row_team == team and row_name == name and status == "OUT":
            return True
    return False


def _predict_verified(
    baseline: dict[str, Any], game: dict[str, Any], artifact: dict[str, Any], clock
) -> dict[str, Any]:
    """Apply a fitted residual only when both prospective QB identities are verified."""
    now = clock().astimezone(UTC)
    if now >= _time(game["kickoff"]):
        return _fallback(baseline, "KICKOFF_PASSED")
    expected = game.get("inputs", {}).get("expected_qb", {})
    if expected.get("fetch_failed") is True:
        return _fallback(baseline, "EXPECTED_QB_SOURCE_FETCH_FAILED", expected)
    if expected.get("status") != "AVAILABLE" or expected.get("source_check_status") not in {
        None,
        "AVAILABLE",
    }:
        return _fallback(baseline, "EXPECTED_QB_UNAVAILABLE", expected)
    captured_at = expected.get("captured_at")
    source_updated_at = expected.get("source_updated_at")
    kickoff = _time(game["kickoff"])
    if (
        not isinstance(captured_at, str)
        or _time(captured_at) > now
        or _time(captured_at) >= kickoff
    ):
        return _fallback(baseline, "EXPECTED_QB_CAPTURE_TIME_INVALID", expected)
    if (
        not isinstance(source_updated_at, str)
        or _time(source_updated_at) > now
        or _time(source_updated_at) >= kickoff
    ):
        return _fallback(baseline, "EXPECTED_QB_SOURCE_TIME_INVALID", expected)
    required_game_ids = game.get("qb_state_required_game_ids", [])
    if not isinstance(required_game_ids, list) or not set(required_game_ids).issubset(
        artifact.get("included_game_ids", [])
    ):
        return _fallback(baseline, "QB_STATE_STALE", expected)
    required_after = game.get("qb_state_required_after")
    if required_after is not None and (
        int(artifact["through_season"]) < int(game["season"])
        or _time(artifact["data_as_of"]) < _time(required_after)
    ):
        return _fallback(baseline, "QB_STATE_STALE", expected)
    data = expected.get("data")
    teams = (game.get("home"), game.get("away"))
    if not isinstance(data, dict) or any(
        team not in data or not isinstance(data[team], dict) for team in teams
    ):
        return _fallback(baseline, "EXPECTED_QB_AMBIGUOUS", expected)
    injury_rows = game.get("inputs", {}).get("injuries", {}).get("data", [])
    uncertain = {"QUESTIONABLE", "DOUBTFUL", "OUT"}
    expected_names = {data[team].get("player_name") for team in teams}
    for row in injury_rows if isinstance(injury_rows, list) else []:
        if (
            isinstance(row, dict)
            and (row.get("player") or row.get("player_name") or row.get("Player")) in expected_names
        ):
            status = str(row.get("game_status") or row.get("GameStatus") or "").upper()
            if status in uncertain:
                return _fallback(baseline, f"EXPECTED_QB_INJURY_STATUS_{status}", expected)
    injuries = game.get("inputs", {}).get("injuries", {})
    if injuries.get("fetch_failed") is True or injuries.get("status") != "AVAILABLE":
        return _fallback(baseline, "QB_INJURY_EVIDENCE_UNAVAILABLE", expected)
    if any(_out_conflict(game, team, data[team]) for team in teams):
        return _fallback(baseline, "EXPECTED_QB_CONFLICTS_WITH_INJURY_OUT", expected)
    inactives = game.get("inputs", {}).get("inactives", {})
    if inactives.get("fetch_failed") is True:
        return _fallback(baseline, "QB_INACTIVES_FETCH_FAILED", inactives)
    inactive_rows = inactives.get("data", []) if inactives.get("status") == "AVAILABLE" else []
    expected_ids = {data[team].get("gsis_id") for team in teams}
    expected_names = {data[team].get("player_name") for team in teams}
    for row in inactive_rows if isinstance(inactive_rows, list) else []:
        if isinstance(row, dict) and (
            row.get("gsis_id") in expected_ids
            or row.get("player_id") in expected_ids
            or (row.get("team") in teams and row.get("player") in expected_names)
        ):
            return _fallback(baseline, "EXPECTED_QB_LISTED_INACTIVE", expected)
    player_values = artifact.get("player_values", {})
    team_references = artifact.get("team_references", {})
    estimates = {}
    feature_values = {}
    for team in teams:
        player_id = data[team].get("gsis_id")
        estimate = player_values.get(player_id) if isinstance(player_values, dict) else None
        reference = team_references.get(team) if isinstance(team_references, dict) else None
        if (
            not isinstance(estimate, dict)
            or not isinstance(reference, (int, float))
            or not math.isfinite(reference)
        ):
            return _fallback(baseline, "QB_HISTORY_UNAVAILABLE", expected)
        if int(estimate.get("games", 0)) < int(artifact["minimum_qb_games"]) or int(
            estimate.get("attempts", 0)
        ) < int(artifact["minimum_qb_attempts"]):
            return _fallback(baseline, "QB_HISTORY_INSUFFICIENT", expected)
        as_of = estimate.get("as_of")
        if not isinstance(as_of, str) or _time(as_of) > now:
            return _fallback(baseline, "QB_HISTORY_TIME_INVALID", expected)
        if not math.isfinite(float(estimate["epa"])):
            return _fallback(baseline, "QB_HISTORY_INVALID", expected)
        player_rate = (
            float(estimate["epa"]) + float(artifact["qb_shrinkage_attempts"]) * float(reference)
        ) / (int(estimate["attempts"]) + float(artifact["qb_shrinkage_attempts"]))
        estimates[team] = player_rate - float(reference)
        feature_values[team] = {
            "gsis_id": player_id,
            "passing_epa": estimate["epa"],
            "attempts": estimate["attempts"],
            "games": estimate["games"],
            "history_as_of": as_of,
            "team_reference": reference,
            "shrinkage_attempts": artifact["qb_shrinkage_attempts"],
            "shrunk_qb_epa_per_attempt": player_rate,
            "residual": estimates[team],
        }
    home, away = teams
    adjustment = float(artifact["coefficient"]) * (estimates[home] - estimates[away])
    tie = float(baseline["p_tie"])
    non_tie = 1.0 - tie
    home_share = float(baseline["p_home"]) / (float(baseline["p_home"]) + float(baseline["p_away"]))
    updated_share = _sigmoid(math.log(home_share / (1.0 - home_share)) + adjustment)
    return {
        "status": "AVAILABLE",
        "p_home": non_tie * updated_share,
        "p_away": non_tie * (1.0 - updated_share),
        "p_tie": tie,
        "adjustment_logit": adjustment,
        "evidence": {
            "expected_qb": expected,
            "artifact_id": artifact["artifact_id"],
            "coefficient": artifact["coefficient"],
            "feature_values": feature_values,
            "inactives": inactives,
            "reference_interpretation": "Team passing EPA is a proxy to limit double-counting, not an exact decomposition of quarterback contribution in Elo or a causal effect.",
            "home_qb": data[home],
            "away_qb": data[away],
        },
        "limitations": [
            "Research-only QB residual challenger; production Elo is unchanged.",
            artifact["eligibility"],
            *(
                [
                    "Official inactives not yet verified; estimate uses listed starter and injury evidence."
                ]
                if inactives.get("status") != "AVAILABLE"
                else []
            ),
        ],
    }


def predict_qb(
    baseline: dict[str, Any], game: dict[str, Any], artifact: dict[str, Any], clock
) -> dict[str, Any]:
    """Unconditional estimates require clear availability; uncertain starters get no weights."""
    result = _predict_verified(baseline, game, artifact, clock)
    reasons = result.get("limitations", [])
    if not reasons or not reasons[0].startswith("EXPECTED_QB_INJURY_STATUS_"):
        return result
    expected = game["inputs"]["expected_qb"]
    injuries = game["inputs"].get("injuries", {})
    if injuries.get("fetch_failed") or injuries.get("status") != "AVAILABLE":
        return result
    teams = (game["home"], game["away"])
    choices = []
    for team in teams:
        starter = expected["data"][team]
        matching = [
            row
            for row in injuries.get("data", [])
            if isinstance(row, dict)
            and row.get("team") == team
            and (row.get("player") or row.get("player_name")) == starter.get("player_name")
        ]
        uncertain = any(
            str(row.get("game_status", "")).upper() in {"OUT", "QUESTIONABLE", "DOUBTFUL"}
            for row in matching
        )
        candidates = [starter] + starter.get("alternatives", []) if uncertain else [starter]
        choices.append(
            [
                candidate
                for candidate in candidates
                if isinstance(candidate, dict) and not _out_conflict(game, team, candidate)
            ]
        )
    scenarios = []
    for selected in itertools.product(*choices):
        conditional_game = copy.deepcopy(game)
        for team, player in zip(teams, selected, strict=True):
            conditional_game["inputs"]["expected_qb"]["data"][team] = copy.deepcopy(player)
        selected_names = {
            (team, player.get("player_name")) for team, player in zip(teams, selected, strict=True)
        }
        # Only the explicit hypothetical availability condition overrides Q/D; never OUT or inactives.
        conditional_game["inputs"]["injuries"]["data"] = [
            row
            for row in injuries.get("data", [])
            if not (
                isinstance(row, dict)
                and (row.get("team"), row.get("player") or row.get("player_name")) in selected_names
                and str(row.get("game_status", "")).upper() in {"QUESTIONABLE", "DOUBTFUL"}
            )
        ]
        estimate = _predict_verified(baseline, conditional_game, artifact, clock)
        scenario = {
            "assumption": "If "
            + " and ".join(
                f"{team}: {player.get('player_name', 'unknown')} starts and is available"
                for team, player in zip(teams, selected, strict=True)
            ),
            "status": estimate["status"],
            "evidence": {
                "expected_qb": copy.deepcopy(expected),
                "injuries": copy.deepcopy(injuries),
                "selected_players": list(selected),
            },
            "limitations": [
                "Unweighted conditional estimate; no defensible starter probabilities; excluded from forecast scorecards.",
                *estimate.get("limitations", []),
            ],
        }
        if estimate["status"] == "AVAILABLE":
            scenario.update(
                {key: estimate[key] for key in ("p_home", "p_away", "p_tie", "adjustment_logit")}
            )
        scenarios.append(scenario)
    result["conditional_scenarios"] = scenarios
    return result
