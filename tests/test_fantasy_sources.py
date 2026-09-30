"""Real parser contracts, with deliberately incomplete provider inputs."""
import importlib

import pytest


def source():
    return importlib.import_module("fantasy.sources")


def test_snap_denominator_requires_unique_integer_consistent_with_all_rows():
    assert source().snap_denominator([
        {"offense_snaps": "60", "offense_pct": "1"},
        {"offense_snaps": "30", "offense_pct": ".50"}]) == 60
    assert source().snap_denominator([
        {"offense_snaps": "1", "offense_pct": ".01"}]) is None
    assert source().snap_denominator([]) is None
    assert source().snap_denominator([
        {"offense_snaps": "60", "offense_pct": "1"},
        {"offense_snaps": "30", "offense_pct": ".9"}]) is None


def play(**kw):
    return dict(dict(game_id="g", play_id=1, posteam="CHI", desc="Pass",
                     play_type="pass", play_deleted=0, two_point_attempt=0,
                     receiver_player_id="p", rusher_player_id=None,
                     pass_attempt=1, rush_attempt=0, yardline_100=20), **kw)


def test_red_zone_excludes_nullified_and_two_point_plays_and_requires_final():
    plays = [play(), play(play_id=2, play_type="no_play"),
             play(play_id=3, two_point_attempt=1), play(play_id=4, play_deleted=1),
             play(play_id=5, desc="END GAME", play_type=None, pass_attempt=0)]
    assert source().red_zone(plays, "p", "CHI", 1, 0) == 1
    assert source().red_zone(plays[:-1], "p", "CHI", 1, 0) is None
    assert source().red_zone(plays, "p", "CHI", 2, 0) is None


def test_red_zone_missing_field_is_not_zero_and_duplicate_play_fails_closed():
    end = play(play_id=2, desc="END GAME", pass_attempt=0)
    assert source().red_zone([play(yardline_100=None), end], "p", "CHI", 1, 0) is None
    assert source().red_zone([play(), play(), end], "p", "CHI", 1, 0) is None
    assert source().red_zone([play(yardline_100=21), end], "p", "CHI", 1, 0) == 0


def data():
    return {"schedule": [dict(season=2026, game_type="REG", game_id="g", week=1,
        gameday="2026-09-10", home_team="CHI", away_team="GB", home_score=10, away_score=7)],
        "players": [dict(season=2026, season_type="REG", game_id="g", week=1,
        player_id="p", player_display_name="Player", position="WR", team="CHI",
        targets=1, carries=0, receiving_air_yards=None)],
        "teams": [dict(season=2026, season_type="REG", game_id="g", team="CHI",
        targets=10, carries=20)], "snaps": [], "ids": [], "pbp": []}


def test_normalization_keeps_missing_optional_sources_null():
    result = source().normalize(data(), 2026, "2026-09-30")
    row = result["rows"][0]
    assert row["targets"] == 1 and row["team_targets"] == 10
    assert row["snaps"] is None and row["air_yards"] is None
    assert row["red_zone"] is None


def test_non_player_provider_rows_do_not_block_identified_wr_rb():
    d = data()
    d["players"].append({**d["players"][0], "player_id": None, "position": None})
    assert len(source().normalize(d, 2026, "2026-09-30")["rows"]) == 1


def test_current_season_and_completed_schedule_required_no_historical_fallback():
    d = data()
    d["players"][0]["season"] = 2025
    assert source().normalize(d, 2026, "2026-09-30")["rows"] == []
    d = data()
    d["schedule"][0]["home_score"] = None
    assert source().normalize(d, 2026, "2026-09-30")["rows"] == []


def test_same_day_scored_schedule_is_not_treated_as_completed():
    d = data()
    d["schedule"][0]["gameday"] = "2026-09-30"
    assert source().normalize(d, 2026, "2026-09-30")["games"] == []


def test_snap_only_player_has_unknown_usage_not_fabricated_zeros():
    d = data()
    d["players"] = []
    d["ids"] = [dict(gsis_id="p", pfr_id="X")]
    d["snaps"] = [dict(season=2026, game_type="REG", game_id="g", team="CHI",
                       player="Player", position="WR", pfr_player_id="X",
                       offense_snaps=50, offense_pct=1)]
    r = source().normalize(d, 2026, "2026-09-30")["rows"][0]
    assert r["snaps"] == 50 and r["team_snaps"] == 50
    assert r["targets"] is None and r["carries"] is None and r["red_zone"] is None


def test_ambiguous_id_join_and_duplicate_source_keys_fail_closed():
    d = data()
    d["ids"] = [dict(gsis_id="p", pfr_id="X"), dict(gsis_id="q", pfr_id="X")]
    d["snaps"] = [dict(season=2026, game_type="REG", game_id="g", team="CHI",
                       pfr_player_id="X", offense_snaps=50, offense_pct=1)]
    assert source().normalize(d, 2026, "2026-09-30")["rows"][0]["snaps"] is None
    d["players"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        source().normalize(d, 2026, "2026-09-30")


def test_failed_refresh_preserves_snapshot_and_does_not_relabel_old_bytes(tmp_path):
    import json
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"updated_at":"old","rows":[]}')
    cfg = {"cache": tmp_path, "season": 2026, "sources": {k: k for k in data()}}

    def fail(name, url, cfg):
        raise ValueError("HTTP 503")

    assert source().refresh(cfg, fail)["status"] == "failed"
    assert json.loads(snapshot.read_text())["updated_at"] == "old"
    assert "HTTP 503" in (tmp_path / "refresh.json").read_text()


def test_optional_source_failure_is_visible_and_never_uses_old_values(tmp_path):
    import json
    cfg = {"cache": tmp_path, "season": 2026, "sources": {k: k for k in data()}}

    def fetch(name, url, cfg):
        if name == "snaps":
            raise ValueError("HTTP 503")
        return data()[name], {"status": "available"}

    assert source().refresh(cfg, fetch)["status"] == "success"
    saved = json.loads((tmp_path / "snapshot.json").read_text())
    assert saved["sources"]["snaps"]["status"] == "unavailable"
    assert saved["rows"][0]["snaps"] is None
