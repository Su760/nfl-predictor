"""Immutable, research-only QB participation evidence captured in the live T60 window."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import season_live
import week1_live
from season_scoring import _latest_outcome, _prediction_time, _valid_prediction

# The worker helpers predate typing; these are their actual callable contracts here.
canonical = cast(Callable[[object], bytes], week1_live.canonical)
digest = cast(Callable[[bytes], str], week1_live.digest)
stamp = cast(Callable[[datetime], str], week1_live.stamp)
write_once = cast(Callable[[Path, object], bool], week1_live.write_once)
all_records = cast(Callable[[Path, str], list[dict[str, Any]]], season_live.all_records)
journal = cast(Callable[[Path, str, object], str], season_live.journal)
origin_state = cast(
    Callable[
        [datetime, str, datetime, dict[str, Any]],
        tuple[Literal["MISSED", "SCHEDULED", "DUE"], datetime, datetime],
    ],
    season_live.origin_state,
)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("QB_EVIDENCE_TIMEZONE_REQUIRED")
    return parsed


def _input_evidence(value: dict[str, Any], at: datetime, kickoff: datetime) -> dict[str, Any]:
    rows = value.get("data")
    published = {row.get("published_at") for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()
    result = {
        "status": value.get("status", "MISSING"),
        "reason": value.get("reason"),
        "source_check_status": value.get("source_check_status"),
        "source_check_reason": value.get("source_check_reason"),
        "captured_at": value.get("captured_at"),
        "source_published_at": value.get("published_at") or (published.pop() if len(published) == 1 else None),
        "source_updated_at": value.get("source_updated_at"),
        "source_url": value.get("source_url"),
        "raw_sha256": value.get("raw_sha256"),
        "fetch_failed": value.get("fetch_failed", False),
        "data": value.get("data"),
    }
    capture = result["captured_at"]
    if not isinstance(capture, str) or not _time(capture) <= at or _time(capture) >= kickoff:
        result.update(status="MISSING", reason="CAPTURE_OUTSIDE_CUTOFF", data=None)
        return result
    for name in ("source_published_at", "source_updated_at"):
        source_time = result[name]
        if source_time is not None and (
            not isinstance(source_time, str)
            or _time(source_time) > _time(capture)
            or _time(source_time) >= kickoff
        ):
            result.update(status="MISSING", reason="SOURCE_TIME_OUTSIDE_CUTOFF", data=None)
            return result
    if result["fetch_failed"]:
        result.update(status="MISSING", reason=result["reason"] or "SOURCE_FETCH_FAILED")
    if result["status"] != "AVAILABLE":
        result["data"] = None
    return result


def _source_rows(
    root: Path, model: dict[str, Any], at: datetime, season: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    artifact_id = model.get("artifact_id")
    if not isinstance(artifact_id, str):
        return [], {"status": "MISSING", "reason": model.get("reason") or "QB_STATE_UNAVAILABLE"}
    path = root / "shadow" / "artifacts" / f"{artifact_id}.json"
    if not path.exists():
        return [], {"status": "MISSING", "reason": "QB_STATE_ARTIFACT_MISSING"}
    artifact = json.loads(path.read_text())
    if digest(canonical(artifact)) != artifact_id:
        raise ValueError("QB_EVIDENCE_ARTIFACT_HASH_MISMATCH")
    source_hash = artifact.get("state_raw_sha256")
    components = artifact.get("state_source_components", [])
    if (
        not isinstance(source_hash, str)
        or not components
        or any(_time(component["captured_at"]) > at for component in components)
        or _time(artifact["data_as_of"]) > at
    ):
        return [], {"status": "MISSING", "reason": "QB_STATS_NOT_CAPTURED_BY_CUTOFF"}
    if any(
        component.get("source_last_modified")
        and parsedate_to_datetime(component["source_last_modified"]) > _time(component["captured_at"])
        for component in components
    ):
        return [], {"status": "MISSING", "reason": "QB_STATS_PROVIDER_TIME_AFTER_CAPTURE"}
    raw_path = root / "raw" / f"{source_hash}.parquet"
    if not raw_path.exists():
        return [], {"status": "MISSING", "reason": "QB_STATS_RAW_MISSING"}
    raw = raw_path.read_bytes()
    if digest(raw) != source_hash:
        raise ValueError("QB_EVIDENCE_STATS_HASH_MISMATCH")
    rows = pl.read_parquet(io.BytesIO(raw)).filter(pl.col("season") == season).select(
        "season", "week", "recent_team", "opponent_team", "position",
        "season_type", "player_id", "player_name", "attempts",
    ).to_dicts()
    return rows, {
        "status": "AVAILABLE",
        "state_artifact_id": artifact_id,
        "raw_sha256": source_hash,
        "captured_at": artifact["data_as_of"],
        "components": components,
        "provider_publication_at": None,
        "publication_reason": "WEEKLY_STATS_PUBLICATION_TIME_UNAVAILABLE",
    }


def _prior_games(
    game: dict[str, Any], team: str, games: list[dict[str, Any]],
    rows: list[dict[str, Any]], source: dict[str, Any], at: datetime,
    aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    target = _time(game["kickoff"])
    previous = sorted(
        (
            prior for prior in games
            if prior["game_id"] != game["game_id"]
            and prior.get("season") == game["season"]
            and team in (prior.get("home"), prior.get("away"))
            and prior.get("kickoff")
            and _time(prior["kickoff"]) < target
        ),
        key=lambda item: _time(item["kickoff"]),
    )
    output, reasons = [], []
    if not previous:
        reasons.append("NO_PRIOR_CURRENT_SEASON_GAME")
    for prior in previous:
        outcome = _latest_outcome(prior, at)
        if not outcome or outcome.get("status") != "FINAL":
            reasons.append(f"PRIOR_FINAL_UNAVAILABLE:{prior['game_id']}")
            continue
        opponent = prior["away"] if prior["home"] == team else prior["home"]
        stats = [
            row for row in rows
            if row["season"] == prior["season"] and row["week"] == prior["week"]
            and aliases.get(row["recent_team"], row["recent_team"]) == team
            and aliases.get(row["opponent_team"], row["opponent_team"]) == opponent
            and row["position"] == "QB" and row["season_type"] == "REG"
            and row["attempts"] is not None and row["attempts"] > 0
        ]
        if source["status"] != "AVAILABLE" or not stats:
            reasons.append(f"QB_STATS_UNAVAILABLE:{prior['game_id']}")
            continue
        identities = [row["player_id"] for row in stats]
        if any(not identity for identity in identities) or len(set(identities)) != len(identities):
            reasons.append(f"QB_STATS_AMBIGUOUS:{prior['game_id']}")
            continue
        total = sum(int(row["attempts"]) for row in stats)
        output.append({
            "game_id": prior["game_id"],
            "game_date": prior["kickoff"][:10],
            "kickoff": prior["kickoff"],
            "final_observed_at": outcome["observed_at"],
            "outcome_version": outcome["version"],
            "team": team,
            "team_qb_pass_attempts": total,
            "quarterbacks": sorted(
                ({"player_id": row["player_id"], "player_name": row["player_name"],
                  "pass_attempts": int(row["attempts"])} for row in stats),
                key=lambda row: row["player_id"],
            ),
        })
    return output, reasons


def _forecast_ref(
    root: Path, game: dict[str, Any], at: datetime, model: str | None
) -> dict[str, Any]:
    location = root if model is None else root / "shadow" / model
    records = [
        row for row in all_records(location, game["game_id"])
        if row.get("origin") == "T60"
        and _valid_prediction(
            row, game, _time(game["kickoff"]), at, allow_challenger=model is not None
        )
    ]
    if not records:
        return {"status": "MISSING", "reason": "NO_VALID_SAVED_T60_FORECAST"}
    selected = max(records, key=_prediction_time)
    return {
        "status": "AVAILABLE",
        "revision_id": selected["revision_id"],
        "origin": selected["origin"],
        "published_at": selected["published_at"],
        "model_version": selected["model_version"],
        "model_id": selected.get("model_id"),
        "model_definition_sha256": selected.get("model_definition_sha256"),
        "paired_baseline_revision_id": selected.get("paired_baseline", {}).get("revision_id"),
    }


def archive_t60(
    view: dict[str, Any], root: Path, cfg: dict[str, Any], at: datetime,
    model_states: dict[str, Any],
) -> dict[str, Any]:
    """Append evidence only while the real T60 origin is open; never replay old games."""
    policy_path = root / "shadow" / "qb-change-policy.json"
    if policy_path.exists():
        policy = json.loads(policy_path.read_text())
    else:
        policy = {
            "schema_version": "prospective-qb-change-v1",
            "collection_start": stamp(at),
            "origin": "T60",
            "change_definition": (
                "At least one expected QB ID differs from that team's highest-attempt QB "
                "over its three most recent completed current-season games"
            ),
            "eligibility": (
                "T60 official Elo and existing QB shadow have valid immutable forecasts "
                "with the same paired Elo revision; both teams have three prior finalized "
                "current-season games with captured QB attempts, verified expected QB IDs, "
                "and available injury/inactive evidence without expected-QB conflict"
            ),
            "primary_metric": (
                "Difference between changed and unchanged groups in mean paired "
                "three-outcome log loss (QB shadow minus Elo); negative favors QB information"
            ),
            "coverage": (
                "Report every scheduled game, T60 delivery, source and availability missing "
                "reasons, eligible counts by group, settled counts, ties, and paired uncertainty"
            ),
            "double_counting_guard": (
                "No new coefficient now; any later adjustment must use the expected QB's "
                "deviation from the team's recent QB participation and fit only the residual "
                "outcome after the frozen Elo logit in chronological training data"
            ),
            "promotion": "NONE",
        }
        write_once(policy_path, policy)
    started = _time(policy["collection_start"])
    due = [
        game for game in view["games"]
        if game.get("season") == cfg["season"]
        and game.get("kickoff")
        and _time(game["kickoff"]) > started
        and game.get("status") == "STATUS_SCHEDULED"
        and origin_state(_time(game["kickoff"]), "T60", at, cfg)[0] == "DUE"
    ]
    if not due:
        return {"status": "COLLECTING", "saved": []}
    qb_models = [spec["name"] for spec in cfg.get("shadow_models", []) if spec["kind"] == "qb"]
    qb_model = qb_models[0] if qb_models else None
    qb_state = model_states.get(qb_model, {}) if qb_model is not None else {}
    rows, source = _source_rows(root, qb_state, at, cfg["season"])
    saved = []
    for game in due:
        kickoff = _time(game["kickoff"])
        inputs = game.get("inputs", {})
        expected = _input_evidence(inputs.get("expected_qb", {}), at, kickoff)
        injuries = _input_evidence(inputs.get("injuries", {}), at, kickoff)
        inactives = _input_evidence(inputs.get("inactives", {}), at, kickoff)
        teams = {}
        for team in (game["home"], game["away"]):
            qb = expected["data"].get(team) if isinstance(expected["data"], dict) else None
            player_id = qb.get("gsis_id") if isinstance(qb, dict) else None
            name = qb.get("player_name") if isinstance(qb, dict) else None
            injury_rows = [row for row in injuries["data"] or [] if row.get("team") == team and row.get("player") == name]
            inactive_rows = [row for row in inactives["data"] or [] if row.get("team") == team and (row.get("player") == name or row.get("gsis_id") == player_id)]
            prior, missing = _prior_games(
                game, team, view["games"], rows, source, at, cfg.get("team_aliases", {})
            )
            uncertainty = ["EXPECTED_QB_ID_MISSING"] if not player_id else []
            uncertainty += [f"INJURIES_{injuries['status']}"] if injuries["status"] != "AVAILABLE" else []
            uncertainty += [f"INACTIVES_{inactives['status']}"] if inactives["status"] != "AVAILABLE" else []
            if any(str(row.get("game_status", "")).upper() in {"QUESTIONABLE", "DOUBTFUL", "OUT"} for row in injury_rows):
                uncertainty.append("EXPECTED_QB_INJURY_UNCERTAIN_OR_OUT")
            if inactive_rows:
                uncertainty.append("EXPECTED_QB_LISTED_INACTIVE")
            teams[team] = {
                "expected_qb_id": player_id,
                "expected_qb_name": name,
                "expected_starter_source": "depth_chart_rank_1" if player_id else None,
                "confirmed_starter_id": None,
                "injury_rows": injury_rows,
                "inactive_rows": inactive_rows,
                "prior_games": prior,
                "missing_data_reasons": missing,
                "uncertainty": uncertainty,
            }
        refs = {"official": _forecast_ref(root, game, at, None)}
        refs.update({spec["name"]: _forecast_ref(root, game, at, spec["name"])
                     for spec in cfg.get("shadow_models", [])})
        record = {
            "schema_version": "prospective-qb-change-evidence-v1",
            "game_id": game["game_id"], "season": game["season"],
            "kickoff": game["kickoff"], "schedule_version": game["schedule_version"],
            "origin": "T60", "captured_at": stamp(at),
            "policy_sha256": digest(canonical(policy)),
            "expected_qb_source": expected,
            "injury_source": injuries,
            "inactives_source": inactives,
            "qb_stats_source": source,
            "teams": teams,
            "forecasts": refs,
            "production_model_state_sha256": view["model"].get("model_state_sha256"),
            "production_policy_sha256": view["model"].get("policy_sha256"),
        }
        saved.append(journal(root / "shadow", "qb-change-evidence", record))
    return {"status": "COLLECTING", "saved": saved}
