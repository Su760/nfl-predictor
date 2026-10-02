from datetime import UTC, datetime, timedelta
import json

import pytest

from fantasy.receiving_data import archive_forecast
from fantasy.server import response


def archive():
    now = datetime(2026, 10, 2, 15, tzinfo=UTC)
    return now, {"generated_at": now.isoformat(), "sources": {"players": {"captured_at": (now - timedelta(hours=1)).isoformat()}},
                 "rows": [{"kickoff": (now + timedelta(days=2)).isoformat(), "targets": 7}]}


def test_exclusive_archives_preserve_versions(tmp_path):
    now, data = archive()
    first = archive_forecast(tmp_path, data, now)
    original = first.read_bytes()
    data["rows"][0]["targets"] = 8
    second = archive_forecast(tmp_path, data, now + timedelta(seconds=1))
    assert first != second and first.read_bytes() == original
    assert json.loads((tmp_path / "latest.json").read_text())["archive"] == second.name
    assert len(list((tmp_path / "forecasts").glob("*.json"))) == 2


def test_archive_refuses_write_crossing_kickoff_or_future_input(tmp_path):
    now, data = archive()
    with pytest.raises(ValueError, match="Kickoff passed"):
        archive_forecast(tmp_path, data, now + timedelta(days=3))
    assert not (tmp_path / "forecasts").exists()
    data["sources"]["players"]["captured_at"] = (now + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="after prediction cutoff"):
        archive_forecast(tmp_path, data, now)
    assert not (tmp_path / "forecasts").exists()


def test_api_reads_only_no_forecast_creation(tmp_path, monkeypatch):
    monkeypatch.setattr("fantasy.receiving_data.build", lambda *a, **k: pytest.fail("API must not build"))
    cfg = {"cache": tmp_path, "season": 2026, "stale_after_hours": 24}
    body, _, status = response("/api/fantasy/receiving", cfg)
    assert status == 200 and json.loads(body)["state"] == "unavailable"
    assert not list(tmp_path.iterdir())
    assert response("/receiving.js", cfg)[2] == 200
    from ops.week1_viewer import static_response
    assert static_response("/receiving.js")[1].startswith("text/javascript")


def test_experiment_refuses_repeated_comparison(tmp_path, monkeypatch):
    from fantasy import receiving_eval
    monkeypatch.setattr(receiving_eval, "ARTIFACT_ROOT", tmp_path)
    (tmp_path / "freeze.json").write_text("{}")
    with pytest.raises(ValueError, match="already sealed"):
        receiving_eval.run()


def test_franchise_aliases_reconcile_without_accepting_other_contradictions(monkeypatch):
    from fantasy import receiving_data
    player = dict(player_id="00-0000001", player_display_name="WR", position="WR", team="LV", season=2019,
                  season_type="REG", game_id="g", targets=1, receptions=1, receiving_yards=8)
    team = dict(team="LV", season=2019, season_type="REG", game_id="g", targets=1, attempts=2)
    game = dict(game_id="g", season="2019", week="1", game_type="REG", gameday="2019-09-08",
                gametime="13:00", away_team="OAK", home_team="DEN", home_score="1", away_score="2")
    def sources(name, *a, **k):
        return {"players": [player], "teams": [team], "schedule": [game]}[name], {}
    monkeypatch.setattr(receiving_data, "source", sources)
    players, teams, games, _ = receiving_data.dataset(2019)
    assert players[0]["team"] == teams[0]["team"] == games[0]["away_team"] == "LV"
    assert games[0]["game_id"] == "g"
    player["team"] = "KC"
    with pytest.raises(ValueError, match="contradicts scheduled"):
        receiving_data.dataset(2019)
