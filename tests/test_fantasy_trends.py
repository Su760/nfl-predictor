"""Calendar-week changes must use disjoint, complete opportunity samples."""
import importlib

import pytest


def trend(rows, games, schedule=None):
    return importlib.import_module("fantasy.trends").weekly_trends(
        rows, games, schedule if schedule is not None else games)["p"]


def sample():
    games = [dict(game_id=f"g{w}", week=w, date=f"2026-09-{w:02}", teams=["CHI", "GB"])
             for w in [1, 2, 3]]
    rows = [dict(player_id="p", name="Player", position="TE", team="CHI", game_id=f"g{w}",
                 targets=n, team_targets=d, carries=0, team_carries=20, snaps=s, team_snaps=t)
            for w, n, d, s, t in [(1, 2, 10, 20, 40), (2, 9, 30, 45, 60), (3, 4, 20, 20, 40)]]
    return rows, games


def test_exact_weeks_counts_and_percentage_points_not_relative_percentage():
    rows, games = sample()
    weeks = trend(rows, games)["periods"]
    assert [p["week"] for p in weeks] == [1, 2, 3]
    assert weeks[1]["metrics"]["target_share"]["delta_pp"] == pytest.approx(10)
    assert weeks[2]["metrics"]["target_share"]["delta_pp"] == pytest.approx(-10)
    assert weeks[1]["metrics"]["snap_share"]["delta_pp"] == 25
    assert weeks[1]["metrics"]["carry_share"]["delta_pp"] == 0
    assert weeks[1]["metrics"]["target_share"]["numerator"] == 9
    assert weeks[1]["metrics"]["target_share"]["denominator"] == 30
    assert weeks[1]["games"][0]["date"] == "2026-09-02"
    assert set(weeks[0]["game_ids"]).isdisjoint(weeks[1]["game_ids"])
    assert weeks[0]["metrics"]["target_share"]["delta_pp"] is None


def test_bye_and_week_after_bye_do_not_substitute_previous_appearance():
    rows, games = sample()
    weeks = trend([rows[0], rows[2]], [games[0], games[2]])["periods"]
    assert weeks[1]["status"] == "No scheduled game (bye or schedule gap)"
    assert weeks[1]["metrics"]["target_share"]["value"] is None
    assert weeks[2]["metrics"]["target_share"]["delta_pp"] is None


@pytest.mark.parametrize("field", ["targets", "team_targets", "snaps", "team_snaps"])
def test_null_count_blocks_only_affected_metric_changes(field):
    rows, games = sample()
    rows[1][field] = None
    metric = "target_share" if "target" in field else "snap_share"
    weeks = trend(rows, games)["periods"]
    assert weeks[1]["metrics"][metric]["covered_games"] == 0
    assert weeks[1]["metrics"][metric]["delta_pp"] is None
    assert weeks[2]["metrics"][metric]["delta_pp"] is None
    assert weeks[1]["metrics"]["carry_share"]["delta_pp"] == 0


def test_missed_player_game_and_unfinished_game_never_zero():
    rows, games = sample()
    weeks = trend([rows[0], rows[2]], games)["periods"]
    assert weeks[1]["observed_games"] == 0 and weeks[1]["expected_games"] == 1
    assert weeks[1]["metrics"]["target_share"]["opportunities"] is None
    assert weeks[2]["metrics"]["target_share"]["delta_pp"] is None
    weeks = trend([rows[0], rows[2]], [games[0], games[2]], games)["periods"]
    assert weeks[1]["status"] == "Scheduled game not completed / result unavailable"


def test_zero_denominator_is_unavailable_but_true_zero_share_is_valid():
    rows, games = sample()
    rows[1].update(targets=0, team_targets=0)
    m = trend(rows, games)["periods"][1]["metrics"]
    assert m["target_share"]["delta_pp"] is None
    assert m["carry_share"]["value"] == 0


def test_team_change_blocks_deltas_and_duplicate_games_rejected():
    rows, games = sample()
    rows[0]["team"] = "GB"
    assert all(p["metrics"]["target_share"]["delta_pp"] is None
               for p in trend(rows, games)["periods"])
    with pytest.raises(ValueError, match="duplicate"):
        trend(rows + [rows[0]], games)


def test_legacy_snapshot_without_schedule_reports_unavailable_periods():
    rows, games = sample()
    periods = trend(rows, games, [])["periods"]
    assert all(p["metrics"]["target_share"]["delta_pp"] is None for p in periods)


def test_period_share_sums_counts_and_blocks_incomplete_multi_game_sample():
    rows, games = sample()
    games.append(dict(games[1], game_id="extra"))
    rows.append(dict(rows[1], game_id="extra", targets=1, team_targets=10))
    metric = trend(rows, games)["periods"][1]["metrics"]["target_share"]
    assert metric["value"] == 10 / 40  # Not mean(9/30, 1/10).
    assert metric["delta_pp"] == pytest.approx(5)
    assert metric["expected_games"] == metric["covered_games"] == 2
    rows[-1]["targets"] = None
    metric = trend(rows, games)["periods"][1]["metrics"]["target_share"]
    assert metric["covered_games"] == 1 and metric["expected_games"] == 2
    assert metric["opportunities"] is None and metric["team_opportunities"] == 40
    assert metric["value"] is None and metric["delta_pp"] is None


def test_duplicate_schedule_cannot_create_overlapping_periods():
    rows, games = sample()
    with pytest.raises(ValueError, match="duplicate scheduled game"):
        trend(rows, games, games + [dict(games[0], week=2)])
