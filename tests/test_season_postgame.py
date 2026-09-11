from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import season_postgame  # noqa: I001


NOW = datetime(2026, 9, 11, 3, tzinfo=UTC)


def game(version=1, home_score=7, away_score=27):
    return {
        "game_id": "2026_01_SF_LA",
        "source_event_id": "401872657",
        "home": "LA",
        "away": "SF",
        "outcomes": [{
            "version": version,
            "status": "FINAL",
            "observed_at": "2026-09-11T02:47:45Z",
            "home_score": home_score,
            "away_score": away_score,
        }],
    }


def summary(*, event_id="401872657", final=True, home_score=7, away_score=27, statistics=True, plays=True):
    document = {
        "header": {
            "id": event_id,
            "competitions": [{
                "id": event_id,
                "status": {"type": {
                    "name": "STATUS_FINAL" if final else "STATUS_IN_PROGRESS",
                    "state": "post" if final else "in",
                    "completed": final,
                }},
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": "LAR"}, "score": str(home_score)},
                    {"homeAway": "away", "team": {"abbreviation": "SF"}, "score": str(away_score)},
                ],
            }],
        },
        "boxscore": {"teams": []},
        "scoringPlays": [],
    }
    if statistics:
        document["boxscore"]["teams"] = [
            {"team": {"abbreviation": "LAR"}, "statistics": [{"name": "totalYards", "label": "Total Yards", "displayValue": "201"}]},
            {"team": {"abbreviation": "SF"}, "statistics": [{"name": "totalYards", "label": "Total Yards", "displayValue": "388"}]},
        ]
    if plays:
        document["scoringPlays"] = [{
            "id": "p1", "period": {"number": 1}, "clock": {"displayValue": "6:15"},
            "team": {"abbreviation": "SF"}, "type": {"text": "Field Goal Good"},
            "text": "20 Yd Field Goal", "homeScore": 0, "awayScore": 3,
        }]
    return document


def cfg():
    return {
        "zero_dollar_mode": True,
        "allow_paid_usage": False,
        "postgame_refresh_seconds": 3600,
        "postgame_summary_url": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}",
    }


def install_fetch(monkeypatch, documents, calls):
    def fetch(url, config, root, clock, suffix):
        calls.append(url)
        document = documents.pop(0) if len(documents) > 1 else documents[0]
        body = json.dumps(document).encode()
        return body, {"captured_at": clock().isoformat().replace("+00:00", "Z"), "raw_sha256": sha256(body).hexdigest()}
    monkeypatch.setattr(season_postgame.season_sources, "_fetch", fetch)


def test_final_summary_normalizes_supported_postgame_facts(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "AVAILABLE"
    assert evidence["source_event_id"] == "401872657"
    assert evidence["team_statistics"]["LA"][0]["display_value"] == "201"
    assert evidence["plays"] == [{"source_play_id": "p1", "period": 1, "clock": "6:15", "team": "SF", "type": "Field Goal Good", "text": "20 Yd Field Goal", "home_score": 0, "away_score": 3}]
    assert "not a pregame model input" in evidence["limitations"][0]


@pytest.mark.parametrize("document,reason", [
    (summary(event_id="wrong"), "ESPN_SUMMARY_EVENT_MISMATCH"),
    (summary(final=False), "ESPN_SUMMARY_NOT_FINAL"),
    (summary(home_score=8), "ESPN_SUMMARY_SCORE_MISMATCH"),
])
def test_rejects_wrong_game_nonfinal_and_mismatched_scores(tmp_path, monkeypatch, document, reason):
    calls = []
    install_fetch(monkeypatch, [document], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "FAILED"
    assert evidence["limitations"] == [reason]
    assert evidence["raw_sha256"] is None


def test_repeated_settled_tick_uses_archived_capture(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    current = [NOW]
    clock = lambda: current[0]
    first = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    current[0] += timedelta(minutes=10)
    second = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    assert first == second
    assert len(calls) == 1
    assert len(list((tmp_path / "postgame" / "2026_01_SF_LA").glob("*.json"))) == 1


def test_new_outcome_version_forces_capture_and_retains_correction(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary(), summary(home_score=8)], calls)
    season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)
    corrected = season_postgame.refresh_postgame({"games": [game(2, home_score=8)]}, tmp_path, cfg(), lambda: NOW + timedelta(minutes=1))["2026_01_SF_LA"]
    assert corrected["outcome_version"] == 2
    assert corrected["home_score"] == 8
    records = [json.loads(path.read_text()) for path in (tmp_path / "postgame" / "2026_01_SF_LA").glob("*.json")]
    assert {record["outcome_version"] for record in records} == {1, 2}
    assert len(calls) == 2


def test_missing_supported_sections_is_partial(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary(statistics=False, plays=False)], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "PARTIAL"
    assert evidence["team_statistics"] == {}
    assert evidence["plays"] == []
    assert len(evidence["limitations"]) == 4


def test_current_nonfinal_outcome_never_fetches(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    value = game()
    value["outcomes"].append({"version": 2, "status": "RETRACTED"})
    evidence = season_postgame.refresh_postgame({"games": [value]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "NOT_FINAL"
    assert calls == []


def test_stale_refetch_updates_check_time_without_changing_semantic_record(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    current = [NOW]
    clock = lambda: current[0]
    first = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    current[0] = NOW + timedelta(hours=2)
    second = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    current[0] += timedelta(minutes=1)
    third = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    assert first == second == third
    assert len(calls) == 2
    assert len(list((tmp_path / "postgame" / "2026_01_SF_LA").glob("*.json"))) == 1
    check = json.loads((tmp_path / "postgame_checks" / "2026_01_SF_LA.json").read_text())
    assert check["checked_at"] == "2026-09-11T05:00:00Z"


def test_unsafe_game_id_is_rejected_before_path_or_fetch(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    value = game()
    value["game_id"] = "../escape"
    with pytest.raises(ValueError, match="GAME_ID_INVALID"):
        season_postgame.refresh_postgame({"games": [value]}, tmp_path, cfg(), lambda: NOW)
    assert calls == []
    assert not (tmp_path / "escape").exists()


def test_future_outcome_is_rejected_before_fetch(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    value = game()
    value["outcomes"][0]["observed_at"] = "2026-09-11T03:00:01Z"
    evidence = season_postgame.refresh_postgame({"games": [value]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "FAILED"
    assert evidence["limitations"] == ["OUTCOME_OBSERVED_IN_FUTURE"]
    assert calls == []


def test_existing_evidence_without_check_view_uses_capture_time(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary()], calls)
    current = [NOW]
    clock = lambda: current[0]
    first = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    (tmp_path / "postgame_checks" / "2026_01_SF_LA.json").unlink()
    current[0] += timedelta(minutes=10)
    second = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), clock)
    assert first == second
    assert len(calls) == 1


def test_changed_source_event_bypasses_same_version_cache(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary(), summary(event_id="401872999")], calls)
    first = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)
    changed = game()
    changed["source_event_id"] = "401872999"
    second = season_postgame.refresh_postgame({"games": [changed]}, tmp_path, cfg(), lambda: NOW + timedelta(minutes=1))
    assert first["2026_01_SF_LA"]["source_event_id"] == "401872657"
    assert second["2026_01_SF_LA"]["source_event_id"] == "401872999"
    assert len(calls) == 2


def test_null_optional_boxscore_is_partial_instead_of_aborting(tmp_path, monkeypatch):
    calls = []
    document = summary()
    document["boxscore"] = None
    install_fetch(monkeypatch, [document], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "PARTIAL"
    assert evidence["team_statistics"] == {}
    assert calls


def test_null_optional_scoring_play_team_is_preserved_without_aborting(tmp_path, monkeypatch):
    calls = []
    document = summary()
    document["scoringPlays"][0]["team"] = None
    install_fetch(monkeypatch, [document], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "AVAILABLE"
    assert evidence["plays"][0]["team"] is None


def test_mismatched_check_identity_never_uses_old_capture_fallback(tmp_path, monkeypatch):
    calls = []
    install_fetch(monkeypatch, [summary(), summary(event_id="401872999"), summary()], calls)
    original = game()
    changed = game()
    changed["source_event_id"] = "401872999"
    season_postgame.refresh_postgame({"games": [original]}, tmp_path, cfg(), lambda: NOW)
    season_postgame.refresh_postgame({"games": [changed]}, tmp_path, cfg(), lambda: NOW + timedelta(minutes=1))
    season_postgame.refresh_postgame({"games": [original]}, tmp_path, cfg(), lambda: NOW + timedelta(minutes=2))
    assert len(calls) == 3


@pytest.mark.parametrize("field", ["period", "clock", "type"])
def test_null_optional_play_details_do_not_abort_refresh(tmp_path, monkeypatch, field):
    calls = []
    document = summary()
    document["scoringPlays"][0][field] = None
    install_fetch(monkeypatch, [document], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "AVAILABLE"
    expected = {"period": "period", "clock": "clock", "type": "type"}[field]
    assert evidence["plays"][0][expected] is None


def test_null_optional_statistics_list_is_partial(tmp_path, monkeypatch):
    calls = []
    document = summary()
    document["boxscore"]["teams"][0]["statistics"] = None
    install_fetch(monkeypatch, [document], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "PARTIAL"
    assert evidence["team_statistics"]["LA"] == []


@pytest.mark.parametrize("mutation", ["team", "status", "competitor"])
def test_malformed_required_nested_shapes_return_failed(tmp_path, monkeypatch, mutation):
    calls = []
    document = summary()
    competition = document["header"]["competitions"][0]
    if mutation == "team":
        competition["competitors"][0]["team"] = None
    elif mutation == "status":
        competition["status"] = None
    else:
        competition["competitors"][0] = None
    install_fetch(monkeypatch, [document], calls)
    evidence = season_postgame.refresh_postgame({"games": [game()]}, tmp_path, cfg(), lambda: NOW)["2026_01_SF_LA"]
    assert evidence["status"] == "FAILED"
    assert calls
