import importlib
import pytest


def points():
    return importlib.import_module("fantasy.points")


def test_explicit_scoring_negative_yards_exclusions_and_missing_components():
    p = points()
    components = dict(rushing_yards=-10, receiving_yards=80, rushing_tds=1,
                      receiving_tds=1, receptions=5, fumbles_lost=9, passing_tds=4)
    assert p.score(components, "standard") == 19
    assert p.score(components, "half_ppr") == 21.5
    assert p.score(components, "ppr") == 24
    components["receiving_tds"] = None
    assert p.score(components, "ppr") is None
    with pytest.raises(ValueError):
        p.score(components, "unknown")


def test_fixed_shrinkage_unseen_bin_and_predictor_leakage_guard():
    p = points()
    context = dict(position="TE", kind="target", yardline=5, depth=3)
    events = [dict(context=context, components=dict(rushing_yards=0, receiving_yards=10,
        rushing_tds=0, receiving_tds=0, receptions=1))]
    record = dict(season=2019, events=events)
    model = p.fit([record])
    expected = p.predict(model, [context], "ppr")
    assert expected == 2
    noisy = dict(context, receptions=0, yards_gained=900, touchdown=1)
    assert p.predict(model, [noisy], "ppr") == expected
    assert p.predict(model, [dict(context, yardline=90, depth=60)], "ppr") == 2
    assert p.predict(model, [dict(context, depth=None)], "ppr") is None
    with pytest.raises(ValueError, match="training"):
        p.fit([dict(record, season=2026)])
    with pytest.raises(ValueError, match="training"):
        p.fit([dict(record, season=2024)])


def test_window_mean_uses_total_depth_and_target_count_negative_values_allowed():
    p = points()
    rows = [{"metrics": {"depth_sum": -4, "depth_count": 2, "targets": 2}},
            {"metrics": {"depth_sum": 12, "depth_count": 1, "targets": 1}}]
    metric = p.depth_metric(rows)
    assert metric["value"] == pytest.approx(8/3)
    assert metric["covered_targets"] == 3 and metric["expected_targets"] == 3
    rows[1]["metrics"]["depth_sum"] = None
    assert p.depth_metric(rows)["value"] is None


def test_sparse_cell_uses_exact_prior_and_unseen_position_is_not_zero():
    p = points()
    context = dict(position="RB", kind="carry", yardline=5, depth=None)
    zero = dict(rushing_yards=0, receiving_yards=0, rushing_tds=0, receiving_tds=0, receptions=0)
    events = [dict(context=context, components=dict(zero, rushing_yards=80)),
              dict(context=dict(context, yardline=90), components=zero)]
    model = p.fit([dict(season=2019, events=events)])
    assert p.predict(model, [context], "ppr") == pytest.approx((8+100*4)/101)
    assert p.predict(model, [context], "ppr", baseline=True) == 4
    assert p.predict(model, [dict(context, position="TE")], "ppr") is None


def test_missing_games_withhold_full_points_and_zero_target_depth_remains_unknown():
    p = points()
    row = {"metrics": {"depth_sum": 0, "depth_count": 0, "targets": 0},
           "components": dict(rushing_yards=0, receiving_yards=0, rushing_tds=0,
                              receiving_tds=0, receptions=0), "reason": None, "contexts": []}
    game = dict(game_id="g", week=1, date="2026-09-13", usage={"quality": row})
    player = dict(transfer=False, games=[game])
    assert p.summarize_quality(player, "last")["points"]["ppr"]["actual"]["value"] == 0
    assert p.summarize_quality(player, "last")["metrics"]["target_depth"]["value"] is None
    player["games"].append(dict(game, game_id="missing", usage={}))
    result = p.summarize_quality(player, "last3")
    assert result["points"]["ppr"]["actual"]["value"] is None
    assert result["points"]["ppr"]["actual"]["covered_games"] == 1
