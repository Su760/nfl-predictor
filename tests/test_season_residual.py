import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
r = importlib.import_module("season_residual")
CFG = {"ridge_penalty": 1.0, "max_iterations": 100, "tolerance": 1e-9}


def test_offset_keeps_elo_fixed_and_recovers_incremental_signal():
    x = np.tile([-2.0, -1.0, 1.0, 2.0], 100).reshape(-1, 1)
    y = (x[:, 0] > 0).astype(float)
    fitted = r.fit_offset(x, np.zeros(len(x)), y, CFG)
    assert fitted["beta"][1] > 0
    probabilities = r.predict_offset(fitted, x, np.zeros(len(x)))
    assert probabilities[y == 1].mean() > 0.9
    zero = {"mean": [0.0], "scale": [1.0], "beta": [0.0, 0.0]}
    assert r.predict_offset(zero, [[500.0]], [np.log(3)])[0] == pytest.approx(0.75)


def test_training_scaler_and_fold_ignore_future_and_test_labels():
    rows = [{"season": s, "result": "home", "features": [s]} for s in range(2018, 2027)]
    before = r.partition(rows, 2023, 2018)
    rows[-1]["features"] = [1e12]
    rows[-1]["result"] = "away"
    after = r.partition(rows, 2023, 2018)
    assert before == after
    assert [x["season"] for x in after[0]] == [2018, 2019, 2020, 2021]
    assert [x["season"] for x in after[1]] == [2022]
    model = r.fit_offset([[0.0], [2.0]], [0.0, 0.0], [0.0, 1.0], CFG)
    assert model["mean"] == [1.0]
    r.predict_offset(model, [[1e9]], [0.0])
    assert model["mean"] == [1.0]


def test_control_handles_no_epa_and_fit_fails_closed():
    m = r.fit_offset(np.empty((4, 0)), [0.0] * 4, [0.0, 1.0, 0.0, 1.0], CFG)
    assert r.predict_offset(m, np.empty((2, 0)), [0.0, np.log(3)]).tolist() == pytest.approx(
        [0.5, 0.75]
    )
    with pytest.raises(ValueError, match="INVALID_OFFSET_TRAINING"):
        r.fit_offset([[float("nan")], [1.0]], [0.0, 0.0], [0.0, 1.0], CFG)
    with pytest.raises(ValueError, match="DID_NOT_CONVERGE"):
        r.fit_offset([[0.0], [1.0]], [0.0, 0.0], [0.0, 1.0], {**CFG, "max_iterations": 1})


def test_join_rejects_missing_duplicate_and_wrong_cutoff():
    native = SimpleNamespace(
        event_id="g",
        kickoff_at_utc=datetime(2024, 9, 1, 18, tzinfo=UTC),
        season=2024,
        result="home",
        features=(1.0, 2.0, 3.0, 4.0),
        snapshot_id="s",
    )
    base = {
        "game_id": "g",
        "cutoff": "2024-09-01T17:00:00Z",
        "result": "home",
        "season": 2024,
        "q_home": 0.6,
    }
    assert r.join_rows([native], [base], 3600)[0]["feature_snapshot"] == "s"
    for baseline, error in [
        ([], "MISSING"),
        ([base, base], "DUPLICATE"),
        ([{**base, "cutoff": "2024-09-01T18:00:00Z"}], "CUTOFF"),
        ([{**base, "result": "away"}], "LABEL"),
    ]:
        with pytest.raises(ValueError, match=error):
            r.join_rows([native], baseline, 3600)


def test_numerically_converged_large_fit_does_not_stall_on_rounding():
    rng = np.random.default_rng(42)
    x = rng.normal(size=(2000, 4))
    offset = rng.normal(size=2000)
    y = rng.binomial(1, r.sigmoid(offset + x @ [0.2, -0.1, 0.05, 0.03]))
    model = r.fit_offset(x, offset, y, CFG)
    design = np.column_stack((np.ones(len(x)), (x - model["mean"]) / model["scale"]))
    beta = np.asarray(model["beta"])
    gradient = design.T @ (r.predict_offset(model, x, offset) - y) + beta
    assert np.max(np.abs(gradient)) / len(x) < 1e-9
