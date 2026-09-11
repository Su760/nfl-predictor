from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
spec = importlib.util.spec_from_file_location(
    "season_sources", Path(__file__).parents[1] / "ops/season_sources.py"
)
season_sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(season_sources)


def _schedule(status: str = "STATUS_SCHEDULED", kickoff: str = "2026-09-10T00:00Z"):
    rows = []
    events = []
    for index in range(272):
        home, away = f"H{index:03}", f"A{index:03}"
        rows.append(
            {
                "season": "2026",
                "game_type": "REG",
                "home_team": home,
                "away_team": away,
                "game_id": f"game-{index}",
            }
        )
        events.append(
            {
                "id": str(index),
                "season": {"year": 2026, "type": 2},
                "week": {"number": 1},
                "competitions": [
                    {
                        "date": kickoff,
                        "timeValid": True,
                        "neutralSite": False,
                        "venue": {"id": str(index)},
                        "competitors": [
                            {"homeAway": "home", "team": {"abbreviation": home}, "score": ""},
                            {"homeAway": "away", "team": {"abbreviation": away}, "score": ""},
                        ],
                        "status": {"type": {"name": status, "completed": False, "state": "pre"}},
                    }
                ],
            }
        )
    return json.dumps({"events": events}).encode(), rows


def test_schedule_version_excludes_status_but_tracks_kickoff() -> None:
    scheduled_body, rows = _schedule()
    in_progress_body, _ = _schedule("STATUS_IN_PROGRESS")
    equivalent_body, _ = _schedule(kickoff="2026-09-10T00:00:00+00:00")
    moved_body, _ = _schedule(kickoff="2026-09-10T01:00Z")
    venue_metadata = json.loads(scheduled_body)
    for event in venue_metadata["events"]:
        event["competitions"][0]["venue"]["address"] = {"city": "Changed label"}
    venue_metadata_body = json.dumps(venue_metadata).encode()
    scheduled = season_sources._games(scheduled_body, rows, 2026)[0]
    in_progress = season_sources._games(in_progress_body, rows, 2026)[0]
    equivalent = season_sources._games(equivalent_body, rows, 2026)[0]
    moved = season_sources._games(moved_body, rows, 2026)[0]
    metadata_changed = season_sources._games(venue_metadata_body, rows, 2026)[0]
    assert scheduled["game_id"] == in_progress["game_id"] == "game-0"
    assert scheduled["kickoff"] == "2026-09-10T00:00:00Z"
    assert scheduled["schedule_version"] == in_progress["schedule_version"]
    assert scheduled["schedule_version"] == equivalent["schedule_version"]
    assert scheduled["schedule_version"] == metadata_changed["schedule_version"]
    assert scheduled["venue"] != metadata_changed["venue"]
    assert scheduled["venue_identity"] == metadata_changed["venue_identity"] == "0"
    assert moved["schedule_version"] != scheduled["schedule_version"]


def test_official_injury_parser_never_calls_unparsed_empty_data_healthy() -> None:
    receipt = {
        "captured_at": "2026-09-10T00:00:00Z",
        "source_last_modified": None,
        "raw_sha256": "a" * 64,
    }
    reports, check = season_sources._injuries(b"<html>no report table</html>", receipt, 2026)
    assert reports == {}
    assert check["status"] == "UNPARSED"
    assert check["reason"]


def test_real_captures_parse_expected_schedule_and_latest_depth() -> None:
    capture_root = Path("/tmp/nfl-season")
    if not (capture_root / "season.json").exists():
        pytest.skip("real captures unavailable")
    rows = (
        list(csv.DictReader(io.StringIO((Path("/tmp/nfl-week1-live/games.csv")).read_text())))
        if (Path("/tmp/nfl-week1-live/games.csv")).exists()
        else None
    )
    if rows is None:
        pytest.skip("nflverse CSV capture unavailable")
    games = season_sources._games((capture_root / "season.json").read_bytes(), rows, 2026)
    assert len(games) == 272


def test_injury_table_requires_week_and_normalizes_team_name() -> None:
    body = b"""<h1>2026 NFL Injury Report</h1><h2>Injuries - WEEK 1</h2>
    <div class='d3-o-section-sub-title'><span>Rams</span></div>
    <table class='d3-o-reports--detailed'><tr><th>Player</th><th>Position</th>
    <th>Injuries</th><th>Practice Status</th><th>Game Status</th></tr>
    <tr><td>A Player</td><td>QB</td><td>Knee</td><td>Limited</td><td>Questionable</td></tr></table>"""
    receipt = {
        "captured_at": "2026-09-10T00:00:00Z",
        "source_last_modified": None,
        "raw_sha256": "b" * 64,
    }
    reports, check = season_sources._injuries(body, receipt, 2026)
    assert check["status"] == "AVAILABLE"
    assert check["week"] == 1
    assert reports["LA"][0]["player"] == "A Player"
    assert check["publication_time_basis"] == "CAPTURE_ONLY"


def test_curl_transport_uses_argv_and_rejects_unapproved_effective_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("--output") + 1]).write_bytes(b"payload")
        return type("Completed", (), {"stdout": b"https://evil.example/payload"})()

    monkeypatch.setattr(season_sources.subprocess, "run", run)
    cfg = {
        "allowed_hosts": ["source.example"],
        "request_timeout_seconds": 30,
    }
    with pytest.raises(ValueError, match="REDIRECT_HOST"):
        season_sources._curl_download("https://source.example/file;touch-pwned", cfg, tmp_path)
    command, kwargs = calls[0]
    assert command[-1] == "https://source.example/file;touch-pwned"
    assert kwargs == {"check": True, "capture_output": True}
    assert not (tmp_path / "pwned").exists()


def test_team_check_marks_missing_participant_without_downgrading_other_games() -> None:
    check = {"status": "AVAILABLE", "reason": None}
    available = {"LA": [{"player": "A"}], "SF": [{"player": "B"}]}
    assert season_sources._team_check(check, available, ("LA", "SF"), "missing") is check
    missing = season_sources._team_check(check, available, ("LA", "SEA"), "missing")
    assert missing == {"status": "MISSING", "reason": "missing"}
    assert check == {"status": "AVAILABLE", "reason": None}
