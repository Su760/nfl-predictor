"""Archived postgame evidence from the free ESPN game-summary feed."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import season_sources
import week1_live

GAME_ID = re.compile(r"^[0-9]{4}_[0-9]{2}_[A-Z0-9]{2,3}_[A-Z0-9]{2,3}$")


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("CLOCK_MUST_BE_UTC")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("POSTGAME_CAPTURE_TIME_INVALID")
    return parsed.astimezone(UTC)


def _latest_outcome(game: dict[str, Any]) -> dict[str, Any] | None:
    outcomes = game.get("outcomes", [])
    if not isinstance(outcomes, list) or not outcomes:
        return None
    if any(not isinstance(item, dict) for item in outcomes):
        raise TypeError("OUTCOME_INVALID")
    try:
        latest = max(outcomes, key=lambda item: int(item["version"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("OUTCOME_VERSION_INVALID") from error
    version = latest.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise TypeError("OUTCOME_VERSION_INVALID")
    return latest if latest.get("status") == "FINAL" else None


def _competition(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    header = document.get("header")
    if not isinstance(header, dict):
        raise TypeError("ESPN_SUMMARY_HEADER_MISSING")
    competitions = header.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1:
        raise ValueError("ESPN_SUMMARY_COMPETITION_INVALID")
    competition = competitions[0]
    if not isinstance(competition, dict):
        raise TypeError("ESPN_SUMMARY_COMPETITION_INVALID")
    return header, competition


def _side(competition: dict[str, Any], side: str) -> tuple[str, int]:
    competitors = competition.get("competitors")
    if not isinstance(competitors, list) or any(not isinstance(item, dict) for item in competitors):
        raise TypeError("ESPN_SUMMARY_TEAMS_INVALID")
    matches = [item for item in competitors if item.get("homeAway") == side]
    if len(matches) != 1:
        raise ValueError("ESPN_SUMMARY_TEAMS_INVALID")
    item = matches[0]
    team_value = item.get("team")
    abbreviation = team_value.get("abbreviation") if isinstance(team_value, dict) else None
    if not isinstance(abbreviation, str):
        raise TypeError("ESPN_SUMMARY_TEAMS_INVALID")
    try:
        score = int(item["score"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("ESPN_SUMMARY_SCORE_INVALID") from error
    if score < 0:
        raise ValueError("ESPN_SUMMARY_SCORE_INVALID")
    return season_sources._alias(abbreviation), score


def _statistics(document: dict[str, Any], teams: set[str]) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {}
    boxscore = document.get("boxscore")
    if not isinstance(boxscore, dict):
        return normalized
    rows = boxscore.get("teams", [])
    if not isinstance(rows, list):
        return normalized
    for row in rows:
        team_value = row.get("team") if isinstance(row, dict) else None
        abbreviation = team_value.get("abbreviation") if isinstance(team_value, dict) else None
        if not isinstance(abbreviation, str):
            continue
        team = season_sources._alias(abbreviation)
        if team not in teams or team in normalized:
            continue
        values = []
        source_statistics = row.get("statistics")
        if not isinstance(source_statistics, list):
            normalized[team] = values
            continue
        for item in source_statistics:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            values.append({
                "name": item["name"],
                "label": item.get("label") if isinstance(item.get("label"), str) else None,
                "display_value": item.get("displayValue") if isinstance(item.get("displayValue"), str) else None,
            })
        normalized[team] = values
    return normalized


def _plays(document: dict[str, Any], teams: set[str]) -> list[dict[str, Any]]:
    values = document.get("scoringPlays", [])
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        team_value = item.get("team")
        abbreviation = team_value.get("abbreviation") if isinstance(team_value, dict) else None
        team = season_sources._alias(abbreviation) if isinstance(abbreviation, str) else None
        if team is not None and team not in teams:
            raise ValueError("ESPN_SUMMARY_PLAY_TEAM_MISMATCH")
        period = item.get("period")
        clock = item.get("clock")
        play_type = item.get("type")
        result.append({
            "source_play_id": str(item["id"]) if item.get("id") is not None else None,
            "period": period.get("number") if isinstance(period, dict) else None,
            "clock": clock.get("displayValue") if isinstance(clock, dict) else None,
            "team": team,
            "type": play_type.get("text") if isinstance(play_type, dict) else None,
            "text": item.get("text"),
            "home_score": item.get("homeScore"),
            "away_score": item.get("awayScore"),
        })
    return result


def _normalize(document: dict[str, Any], game: dict[str, Any], outcome: dict[str, Any], receipt: dict[str, Any], url: str) -> dict[str, Any]:
    header, competition = _competition(document)
    event_id = str(game["source_event_id"])
    if str(header.get("id")) != event_id or str(competition.get("id")) != event_id:
        raise ValueError("ESPN_SUMMARY_EVENT_MISMATCH")
    status_value = competition.get("status")
    status_type = status_value.get("type") if isinstance(status_value, dict) else None
    status = status_type if isinstance(status_type, dict) else {}
    if not (status.get("name") == "STATUS_FINAL" and status.get("state") == "post" and status.get("completed") is True):
        raise ValueError("ESPN_SUMMARY_NOT_FINAL")
    home, home_score = _side(competition, "home")
    away, away_score = _side(competition, "away")
    if home != game.get("home") or away != game.get("away"):
        raise ValueError("ESPN_SUMMARY_TEAM_MISMATCH")
    if home_score != outcome.get("home_score") or away_score != outcome.get("away_score"):
        raise ValueError("ESPN_SUMMARY_SCORE_MISMATCH")
    teams = {home, away}
    statistics = _statistics(document, teams)
    plays = _plays(document, teams)
    limitations = [
        "Postgame evidence was captured after the final result and was not a pregame model input.",
        "Play evidence contains ESPN scoring plays rather than complete play-by-play.",
    ]
    if set(statistics) != teams or any(not statistics.get(team) for team in teams):
        limitations.append("Complete team statistics were unavailable in this capture.")
    if not plays:
        limitations.append("Scoring-play evidence was unavailable in this capture.")
    completeness = "AVAILABLE" if len(limitations) == 2 else "PARTIAL"
    return {
        "status": completeness,
        "captured_at": receipt["captured_at"],
        "source_url": url,
        "raw_sha256": receipt["raw_sha256"],
        "source_event_id": event_id,
        "outcome_version": outcome["version"],
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "team_statistics": statistics,
        "plays": plays,
        "limitations": limitations,
    }


def _records(root: Path, game_id: str) -> list[dict[str, Any]]:
    folder = root / "postgame" / game_id
    records = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        raw = path.read_bytes()
        if path.stem != sha256(raw).hexdigest():
            raise ValueError("POSTGAME_RECORD_HASH_MISMATCH")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("POSTGAME_RECORD_INVALID")
        records.append(value)
    return records


def _archive(root: Path, game_id: str, evidence: dict[str, Any]) -> None:
    payload = week1_live.canonical(evidence)
    week1_live.write_once(root / "postgame" / game_id / f"{sha256(payload).hexdigest()}.json", payload)


def _evidence_hash(evidence: dict[str, Any]) -> str:
    return sha256(week1_live.canonical(evidence)).hexdigest()


def _check(root: Path, game_id: str) -> dict[str, Any] | None:
    path = root / "postgame_checks" / f"{game_id}.json"
    if not path.exists():
        return None
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError("POSTGAME_CHECK_INVALID")
    return value


def _write_check(root: Path, game_id: str, event_id: str, outcome_version: int, evidence: dict[str, Any], checked_at: str) -> None:
    (root / "postgame_checks").mkdir(parents=True, exist_ok=True)
    week1_live.replace_view(root / "postgame_checks" / f"{game_id}.json", {
        "checked_at": checked_at,
        "source_event_id": event_id,
        "outcome_version": outcome_version,
        "evidence_sha256": _evidence_hash(evidence),
    })


def _unavailable(status: str, game: dict[str, Any], captured_at: str, reason: str, outcome_version: int | None = None, source_url: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "captured_at": captured_at,
        "source_url": source_url,
        "raw_sha256": None,
        "source_event_id": str(game["source_event_id"]) if game.get("source_event_id") is not None else None,
        "outcome_version": outcome_version,
        "team_statistics": {},
        "plays": [],
        "limitations": [reason],
    }


def refresh_postgame(view: dict[str, Any], root: Path, cfg: dict[str, Any], clock) -> dict[str, dict[str, Any]]:
    """Refresh final-game evidence, retaining every immutable correction capture."""
    if cfg.get("zero_dollar_mode") is not True or cfg.get("allow_paid_usage") is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")
    now = clock()
    now_stamp = _stamp(now)
    interval = int(cfg["postgame_refresh_seconds"])
    if interval < 0:
        raise ValueError("POSTGAME_REFRESH_INTERVAL_INVALID")
    template = cfg["postgame_summary_url"]
    if "{event_id}" not in template:
        raise ValueError("POSTGAME_SUMMARY_URL_TEMPLATE_INVALID")
    result: dict[str, dict[str, Any]] = {}
    for game in view.get("games", []):
        game_id = game.get("game_id")
        if not isinstance(game_id, str) or GAME_ID.fullmatch(game_id) is None:
            raise ValueError("GAME_ID_INVALID")
        outcome = _latest_outcome(game)
        if outcome is None:
            result[game_id] = _unavailable("NOT_FINAL", game, now_stamp, "No current final outcome is available.")
            continue
        observed_at = outcome.get("observed_at")
        if not isinstance(observed_at, str):
            result[game_id] = _unavailable("FAILED", game, now_stamp, "OUTCOME_OBSERVED_AT_INVALID", outcome["version"])
            continue
        try:
            if _time(observed_at) > now.astimezone(UTC):
                result[game_id] = _unavailable("FAILED", game, now_stamp, "OUTCOME_OBSERVED_IN_FUTURE", outcome["version"])
                continue
        except ValueError:
            result[game_id] = _unavailable("FAILED", game, now_stamp, "OUTCOME_OBSERVED_AT_INVALID", outcome["version"])
            continue
        event_id = game.get("source_event_id")
        if not isinstance(event_id, (str, int)) or isinstance(event_id, bool):
            result[game_id] = _unavailable("MISSING", game, now_stamp, "The ESPN source event identifier is unavailable.", outcome["version"])
            continue
        url = template.format(event_id=event_id)
        prior = [
            item
            for item in _records(root, game_id)
            if item.get("outcome_version") == outcome["version"]
            and item.get("status") in {"AVAILABLE", "PARTIAL"}
            and item.get("source_event_id") == str(event_id)
            and item.get("home") == game.get("home")
            and item.get("away") == game.get("away")
            and item.get("home_score") == outcome.get("home_score")
            and item.get("away_score") == outcome.get("away_score")
            and _time(item["captured_at"]) <= now.astimezone(UTC)
        ]
        if prior:
            cached = max(prior, key=lambda item: _time(item["captured_at"]))
            checked = _check(root, game_id)
            checked_at = cached["captured_at"] if checked is None else None
            if checked is not None and checked == {
                "checked_at": checked.get("checked_at"),
                "source_event_id": str(event_id),
                "outcome_version": outcome["version"],
                "evidence_sha256": _evidence_hash(cached),
            }:
                checked_at = checked["checked_at"]
            if checked_at is not None:
                age = (now.astimezone(UTC) - _time(checked_at)).total_seconds()
                if 0 <= age < interval:
                    result[game_id] = cached
                    continue
        try:
            body, receipt = season_sources._fetch(url, cfg, root, clock, "json")
            document = json.loads(body)
            if not isinstance(document, dict):
                raise TypeError("ESPN_SUMMARY_INVALID")
            evidence = _normalize(document, game, outcome, receipt, url)
            unchanged = next(
                (item for item in prior if item.get("raw_sha256") == evidence["raw_sha256"]),
                None,
            )
            if unchanged is not None:
                evidence = unchanged
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as error:
            evidence = _unavailable("FAILED", game, _stamp(clock()), str(error), outcome["version"], url)
        _archive(root, game_id, evidence)
        if evidence["status"] in {"AVAILABLE", "PARTIAL"}:
            _write_check(root, game_id, str(event_id), outcome["version"], evidence, _stamp(clock()))
        result[game_id] = evidence
    return result
