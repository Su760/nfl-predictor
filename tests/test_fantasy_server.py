"""Fantasy routes never access the forecasting runtime or arbitrary files."""
import importlib
import json
from datetime import UTC, datetime

import pytest


def server():
    return importlib.import_module("fantasy.server")


def test_routes_allowlist_and_no_data_response(tmp_path):
    cfg = {"cache": tmp_path, "season": 2026, "stale_after_hours": 24,
           "forecast_url": "http://127.0.0.1:8510/"}
    body, kind, status = server().response("/api/fantasy?window=last3", cfg)
    assert status == 200 and json.loads(body)["players"] == []
    assert json.loads(body)["state"] == "unavailable"
    for path in ("/../.env", "/raw/anything", "/.fantasy-cache/snapshot.json", "/fantasy.js/../config"):
        assert server().response(path, cfg) is None
    assert server().response("/api/fantasy?window=bad", cfg)[2] == 400


def test_stale_and_failed_refresh_remain_visible_without_new_fetch(tmp_path):
    cfg = {"cache": tmp_path, "season": 2026, "stale_after_hours": 24,
           "forecast_url": "http://127.0.0.1:8510/"}
    (tmp_path / "snapshot.json").write_text(json.dumps({"season": 2026, "rows": [], "games": [],
        "coverage": {}, "sources": {}, "updated_at": "2020-01-01T00:00:00+00:00"}))
    (tmp_path / "refresh.json").write_text('{"status":"failed","error":"HTTP 503"}')
    payload = json.loads(server().response("/api/fantasy", cfg)[0])
    assert payload["state"] == "stale" and payload["refresh"]["status"] == "failed"
    assert payload["updated_at"].startswith("2020")


def test_season_mismatch_and_corrupt_snapshot_are_unavailable(tmp_path):
    cfg = {"cache": tmp_path, "season": 2026, "stale_after_hours": 24,
           "forecast_url": "http://127.0.0.1:8510/"}
    (tmp_path / "snapshot.json").write_text(json.dumps({"season": 2025, "rows": [], "games": [],
        "coverage": {}, "sources": {}, "updated_at": datetime.now(UTC).isoformat()}))
    assert json.loads(server().response("/api/fantasy", cfg)[0])["state"] == "unavailable"
    (tmp_path / "snapshot.json").write_text("broken json")
    assert json.loads(server().response("/api/fantasy", cfg)[0])["state"] == "unavailable"


def test_existing_viewer_registers_fantasy_assets():
    viewer = importlib.import_module("ops.week1_viewer")
    result = viewer.static_response("/fantasy")
    assert result is not None
    assert b"Player Lab" in result[0]
    assert viewer.static_response("/fantasy.js")[1].startswith("text/javascript")
    assert b'href="/fantasy"' in viewer.static_response("/")[0]


def test_config_is_worktree_local_and_loopback(monkeypatch, tmp_path):
    config = importlib.import_module("fantasy.config")
    cfg = config.configuration()
    assert cfg["cache"].is_relative_to(config.ROOT)
    assert cfg["bind"] == "127.0.0.1" and cfg["port"] != 8510
    path = tmp_path / "bad.toml"
    path.write_text('cache_dir="../nfl-predictor-v2"\nbind="127.0.0.1"')
    monkeypatch.setenv("FANTASY_CONFIG", str(path))
    with pytest.raises(ValueError, match="worktree"):
        config.configuration()
