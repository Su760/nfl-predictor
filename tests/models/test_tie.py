from __future__ import annotations

import math

import pytest

from nfl_predictor.models.tie import TieLayer, to_three_way


# Catches: computing a raw tie frequency instead of the specified Jeffreys posterior mean.
def test_jeffreys_tie_prior_and_three_way_vector() -> None:
    layer = TieLayer.fit(["home", "away", "tie", "home"])
    assert layer.p_tie == pytest.approx(1.5 / 5.0)

    vector = to_three_way(r_home=0.60, p_tie=layer.p_tie)
    assert vector == pytest.approx((0.42, 0.28, 0.30), abs=1e-15)
    assert sum(vector) == pytest.approx(1.0, abs=1e-15)


# Catches: accepting an empty history creates a prior with no valid observed games.
def test_tie_layer_rejects_empty_results() -> None:
    with pytest.raises(ValueError, match="at least one"):
        TieLayer.fit([])


# Catches: unknown outcomes are included in the Jeffreys denominator and bias the tie prior.
@pytest.mark.parametrize("results", [["home", "cancelled"], ["HOME"], [""]])
def test_tie_layer_rejects_invalid_results(results: list[str]) -> None:
    with pytest.raises(ValueError, match="home, away, or tie"):
        TieLayer.fit(results)


# Catches: NaN/Inf or out-of-range values produce a vector that is not a probability distribution.
@pytest.mark.parametrize(
    ("r_home", "p_tie"),
    [
        (-0.01, 0.1),
        (1.01, 0.1),
        (0.5, -0.01),
        (0.5, 1.01),
        (float("nan"), 0.1),
        (0.5, float("nan")),
        (float("inf"), 0.1),
        (0.5, float("-inf")),
    ],
)
def test_three_way_rejects_non_finite_and_out_of_range_inputs(r_home: float, p_tie: float) -> None:
    assert not (
        math.isfinite(r_home) and math.isfinite(p_tie) and 0 <= r_home <= 1 and 0 <= p_tie <= 1
    )
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        to_three_way(r_home, p_tie)


# Catches: endpoint arithmetic produces negative mass or a vector whose total drifts from one.
@pytest.mark.parametrize(
    ("r_home", "p_tie", "expected"),
    [
        (0.0, 0.0, (0.0, 1.0, 0.0)),
        (1.0, 0.0, (1.0, 0.0, 0.0)),
        (0.0, 1.0, (0.0, 0.0, 1.0)),
        (1.0, 1.0, (0.0, 0.0, 1.0)),
    ],
)
def test_three_way_preserves_exact_probability_endpoints(
    r_home: float, p_tie: float, expected: tuple[float, float, float]
) -> None:
    vector = to_three_way(r_home, p_tie)
    assert vector == expected
    assert sum(vector) == 1.0
