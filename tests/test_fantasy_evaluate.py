import importlib
import pytest


def evaluation():
    return importlib.import_module("fantasy.evaluate")


def test_paired_metrics_signed_bias_and_position_groups():
    e = evaluation()
    rows = [dict(game_id="g", position="WR", actual=3, context=5, baseline=6),
            dict(game_id="h", position="RB", actual=4, context=3, baseline=2)]
    result = e.metrics(rows)
    assert result["context"]["mae"] == 1.5
    assert result["context"]["rmse"] == pytest.approx((2.5)**.5)
    assert result["context"]["bias"] == .5  # prediction minus actual
    assert result["baseline"]["bias"] == .5
    with pytest.raises(ValueError, match="paired"):
        e.metrics([dict(rows[0], baseline=None)])


def test_acceptance_gate_checks_coverage_all_formats_and_position_regressions():
    e = evaluation()
    from fantasy.points import settings
    cfg = settings()
    m = {"context": {"mae": 1, "rmse": 1}, "baseline": {"mae": 2, "rmse": 2}}
    report = {"coverage": {"2023": {"ALL": {"eligible": 80, "total": 100}}},
              "scores": {s: {"ALL": m, "WR": m, "RB": m, "TE": m} for s in cfg["reception_points"]}}
    assert e.gate(report, cfg) == []
    report["coverage"]["2023"]["ALL"]["eligible"] = 79
    assert any("coverage" in r for r in e.gate(report, cfg))
    report["scores"]["ppr"]["TE"] = {"context": {"mae": 1, "rmse": 3}, "baseline": {"mae": 2, "rmse": 2}}
    assert any("TE" in r for r in e.gate(report, cfg))


def test_completed_runs_cannot_silently_refit_or_reopen_holdout(tmp_path, monkeypatch):
    e = evaluation()
    monkeypatch.setattr(e, "configuration", lambda: {"cache": tmp_path})
    root = tmp_path / "expected-points"
    root.mkdir()
    (root / "freeze.json").write_text("{}")
    with pytest.raises(ValueError, match="sealed"):
        e.run("validate")
    (root / "holdout.json").write_text("{}")
    with pytest.raises(ValueError, match="already evaluated"):
        e.run("holdout")
