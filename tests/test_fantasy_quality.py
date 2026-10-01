import importlib


def quality():
    return importlib.import_module("fantasy.quality")


def fixture():
    player = dict(player_id="p", team="CHI", position="TE", targets=2, carries=1,
                  receptions=1, receiving_yards=8, rushing_yards=-2,
                  receiving_tds=0, rushing_tds=0, receiving_air_yards=3)
    base = dict(posteam="CHI", play_type="pass", play_deleted=0, two_point_attempt=0,
                pass_attempt=1, rush_attempt=0, receiver_player_id="p", rusher_player_id=None,
                complete_pass=0, receiving_yards=None, pass_touchdown=0, rush_touchdown=0,
                td_player_id=None)
    plays = [dict(base, play_id=1, yardline_100=20, air_yards=-2, complete_pass=1, receiving_yards=8),
             dict(base, play_id=2, yardline_100=5, air_yards=5),
             dict(base, play_id=3, play_type="run", pass_attempt=0, rush_attempt=1,
                  receiver_player_id=None, rusher_player_id="p", rushing_yards=-2, yardline_100=10),
             dict(play_id=4, desc="END GAME")]
    return player, plays


def test_nested_boundaries_separate_counts_depth_and_reconciliation():
    p, plays = fixture()
    a = quality().audit_player(p, plays)
    assert a["reason"] is None
    assert a["metrics"]["rz_targets"] == 2 and a["metrics"]["rz_carries"] == 1
    assert a["metrics"]["i10_targets"] == 1 and a["metrics"]["i10_carries"] == 1
    assert a["metrics"]["i5_targets"] == 1 and a["metrics"]["i5_carries"] == 0
    assert a["metrics"]["depth_sum"] == 3 and a["metrics"]["depth_count"] == 2
    assert a["components"]["rushing_yards"] == -2


def test_nullified_plays_excluded_but_missing_or_duplicate_games_fail_closed():
    p, plays = fixture()
    for field, value in [("play_type", "no_play"), ("play_deleted", 1), ("two_point_attempt", 1)]:
        extra = dict(plays[0], play_id=9, **{field: value})
        assert quality().audit_player(p, plays + [extra])["metrics"]["rz_targets"] == 2
    for broken in [plays[:-1], plays + [plays[0]], []]:
        result = quality().audit_player(p, broken)
        assert result["reason"] == "incomplete_or_duplicate_pbp"
        assert result["metrics"]["rz_targets"] is None


def test_missing_context_blocks_own_metric_and_model_not_other_metrics():
    p, plays = fixture()
    plays[0]["air_yards"] = None
    result = quality().audit_player(p, plays)
    assert result["metrics"]["rz_targets"] == 2
    assert result["metrics"]["depth_sum"] is None
    assert result["metrics"]["depth_count"] == 1
    assert result["reason"] == "missing_context"
    plays[0]["yardline_100"] = None
    assert quality().audit_player(p, plays)["metrics"]["rz_targets"] is None


def test_general_outcome_reconciliation_withholds_lateral_mismatch_not_actuals():
    p, plays = fixture()
    p["receiving_yards"] += 10
    result = quality().audit_player(p, plays)
    assert result["reason"] == "outcome_reconciliation"
    assert result["components"]["receiving_yards"] == 18
    assert result["metrics"]["rz_targets"] == 2


def test_missing_box_counts_are_not_zero_and_true_no_targets_has_no_mean():
    p, plays = fixture()
    p["targets"] = None
    assert quality().audit_player(p, plays)["metrics"]["rz_targets"] is None
    p, plays = fixture()
    p.update(targets=0, receptions=0, receiving_yards=0, receiving_tds=0, receiving_air_yards=0)
    result = quality().audit_player(p, plays[2:])
    assert result["metrics"]["rz_targets"] == 0
    assert result["metrics"]["depth_count"] == 0
    assert result["reason"] is None
