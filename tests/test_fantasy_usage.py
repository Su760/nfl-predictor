"""Usage invariants: weighted shares, missing observations, calendar windows."""
import importlib

import pytest


def usage():
    return importlib.import_module("fantasy.usage")


def fixture():
    games = [dict(game_id=f"g{w}", week=w, date=f"2026-09-{w:02}",
                  teams=["CHI", "GB"]) for w in (1, 2, 4, 5)]
    rows = [dict(player_id="a", name="Alpha", position="WR", team="CHI",
                 game_id=f"g{w}", targets=t, team_targets=d, carries=0, team_carries=20,
                 air_yards=-2, snaps=30, team_snaps=60, red_zone=0)
            for w, t, d in [(1, 1, 10), (2, 9, 30), (4, 4, 20), (5, 2, 10)]]
    return rows, games


def test_shares_are_ratios_of_sums_and_byes_do_not_consume_games():
    rows, games = fixture()
    p = usage().summarize(rows, games, "last3")[0]
    assert p["weeks"] == [2, 4, 5]
    assert p["metrics"]["target_share"]["value"] == pytest.approx(15 / 60)
    assert p["metrics"]["snap_share"]["value"] == .5
    assert p["metrics"]["air_yards"]["value"] == -6
    assert p["metrics"]["carries"]["value"] == 0


def test_snap_share_uses_summed_counts_not_rounded_weekly_percentages():
    rows, games = fixture()
    rows[-1].update(snaps=10, team_snaps=20)
    rows[-2].update(snaps=60, team_snaps=100)
    m = usage().summarize(rows, games, "last3")[0]["metrics"]["snap_share"]
    assert m["value"] == pytest.approx(100 / 180)
    assert m["numerator"] == 100 and m["denominator"] == 180


def test_short_history_reports_actual_sample_and_missing_middle_game_is_unavailable():
    rows, games = fixture()
    p = usage().summarize(rows[:1], games[:1], "last3")[0]
    assert p["expected_games"] == 1 and p["metrics"]["targets"]["value"] == 1
    p = usage().summarize(rows[:2] + rows[3:], games, "last3")[0]
    assert p["weeks"] == [2, 4, 5]
    assert p["metrics"]["targets"]["value"] is None
    assert p["metrics"]["targets"]["covered_games"] == 2


def test_missed_latest_game_is_not_replaced_with_old_appearance_or_zero():
    rows, games = fixture()
    p = usage().summarize(rows[:-1], games, "last")[0]
    assert p["weeks"] == [5]
    assert p["observed_games"] == 0
    assert p["metrics"]["targets"]["value"] is None
    assert p["metrics"]["target_share"]["value"] is None


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), "bad"])
def test_missing_cell_invalidates_full_window_total_and_paired_share(missing):
    rows, games = fixture()
    rows[-1]["targets"] = missing
    p = usage().summarize(rows, games, "last3")[0]
    assert p["metrics"]["targets"]["value"] is None
    assert p["metrics"]["targets"]["covered_games"] == 2
    assert p["metrics"]["target_share"]["value"] is None
    assert p["metrics"]["target_share"]["covered_games"] == 2


def test_zero_denominator_is_unavailable_and_single_game_is_correct():
    rows, games = fixture()
    rows[-1]["team_targets"] = 0
    p = usage().summarize(rows, games, "last")[0]
    assert p["metrics"]["targets"]["value"] == 2
    assert p["metrics"]["target_share"]["value"] is None


def test_missing_denominator_never_reduces_the_window_silently():
    rows, games = fixture()
    rows[1]["team_targets"] = None
    m = usage().summarize(rows, games, "season")[0]["metrics"]["target_share"]
    assert m["value"] is None and m["covered_games"] == 3 and m["expected_games"] == 4


def test_duplicate_player_games_rejected_and_unknown_window_rejected():
    rows, games = fixture()
    with pytest.raises(ValueError, match="duplicate"):
        usage().summarize(rows + [rows[0]], games, "season")
    with pytest.raises(ValueError, match="window"):
        usage().summarize(rows, games, "foo")


def test_transfer_does_not_mix_teams_or_claim_complete_season_coverage():
    rows, games = fixture()
    rows[0]["team"] = "GB"
    p = usage().summarize(rows, games, "season")[0]
    assert p["transfer"] is True
    assert p["metrics"]["targets"]["value"] is None
    assert "team" in p["window_note"].lower()
