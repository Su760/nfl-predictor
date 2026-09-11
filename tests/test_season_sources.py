from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from datetime import UTC, datetime
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


def _inactive_game() -> dict:
    return {
        "game_id": "game-1",
        "season": 2026,
        "week": 1,
        "away": "SF",
        "home": "LA",
        "away_name": "San Francisco 49ers",
        "home_name": "Los Angeles Rams",
        "kickoff": "2026-09-11T00:35:00Z",
    }


def _inactive_article(
    *,
    description: str = "Official inactives for the Week 1 game: San Francisco 49ers at Los Angeles Rams",
    published: str = "2026-09-10T23:26:21Z",
    body: str = "NINERS\n\nQB Player One\n\nRAMS\n\nWR Player Two",
) -> bytes:
    value = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Game inactives: San Francisco 49ers at Los Angeles Rams",
        "description": description,
        "datePublished": published,
        "articleBody": body,
    }
    return ('<script type="application/ld+json">' + json.dumps(value) + "</script>").encode()


def test_official_inactive_article_structure_parses_both_teams() -> None:
    rows, published = season_sources._inactive_article(
        _inactive_article(), _inactive_game(), "https://www.nfl.com/news/test"
    )
    assert {row["team"] for row in rows} == {"SF", "LA"}
    assert len(rows) == 2
    assert published == "2026-09-10T23:26:21Z"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        (
            {"description": "Official 2025 inactives: San Francisco 49ers at Los Angeles Rams"},
            "SEASON",
        ),
        (
            {
                "description": "Official inactives for Week 2: San Francisco 49ers at Los Angeles Rams"
            },
            "WEEK",
        ),
        (
            {"description": "Official inactives for Week 1: Seattle Seahawks at Los Angeles Rams"},
            "TEAMS",
        ),
        ({"body": "NINERS\n\nQB Player One"}, "SECTIONS"),
    ],
)
def test_inactive_article_rejects_wrong_identity_or_missing_team_section(
    changes: dict[str, str], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        season_sources._inactive_article(
            _inactive_article(**changes), _inactive_game(), "https://www.nfl.com/news/test"
        )


def test_inactives_calendar_rollover_and_stale_article():
    game = {**_inactive_game(), "week":18, "kickoff":"2027-01-10T18:00:00Z"}
    body = _inactive_article(description="Official 2026 season Week 18 inactives: San Francisco 49ers at Los Angeles Rams", published="2027-01-10T17:00:00Z")
    assert season_sources._inactive_article(body,game,"https://www.nfl.com/news/test")[0]
    with pytest.raises(ValueError, match="NOT_PREGAME"):
        season_sources._inactive_article(_inactive_article(published="2026-01-10T17:00:00Z"),_inactive_game(),"https://www.nfl.com/news/test")


def test_inactives_modified_body_and_future_source_rejected():
    body = _inactive_article()
    with pytest.raises(ValueError, match="FROM_FUTURE"):
        season_sources._inactive_article(body,_inactive_game(),"https://www.nfl.com/news/test",captured_at=datetime(2026,9,10,23,tzinfo=UTC))
    value = season_sources._news_article(body)
    value["dateModified"] = "2026-09-11T01:00:00Z"
    changed = ('<script type="application/ld+json">'+json.dumps(value)+'</script>').encode()
    with pytest.raises(ValueError, match="NOT_PREGAME"):
        season_sources._inactive_article(changed,_inactive_game(),"https://www.nfl.com/news/test")


def test_official_inactive_links_reject_other_hosts_and_older_report_cannot_replace(tmp_path,monkeypatch):
    landing = b'<a href="https://github.com/news/game-inactives-wrong"></a><a href="/news/game-inactives-new"></a><a href="/news/game-inactives-old"></a>'
    links = season_sources._inactive_links(landing,32)
    assert len(links)==2 and all(url.startswith("https://www.nfl.com/news/") for url in links)
    def fetch(url,*args):
        published = "2026-09-10T23:30:00Z" if url.endswith("new") else "2026-09-10T23:20:00Z"
        return _inactive_article(published=published), {"captured_at":"2026-09-10T23:31:00Z","source_last_modified":None,"raw_sha256":"a"*64}
    monkeypatch.setattr(season_sources,"_fetch",fetch)
    matched,_ = season_sources._inactives(landing,{"maximum_inactive_articles":32,"maximum_inactive_publication_age_seconds":259200},tmp_path,lambda:datetime(2026,9,10,23,31,tzinfo=UTC),[_inactive_game()])
    assert matched["game-1"][1]["source_updated_at"] == "2026-09-10T23:30:00Z"
