from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from nfl_predictor.evaluation.metrics import (
    Scorecard,
    binary_brier,
    binary_log_loss,
    multiclass_brier,
    multinomial_log_loss,
    score_predictions,
    straight_up_accuracy,
)


def test_unhalved_three_class_brier_has_range_zero_to_two() -> None:
    assert multiclass_brier([(1.0, 0.0, 0.0)], ["home"]) == 0.0
    assert multiclass_brier([(0.0, 1.0, 0.0)], ["home"]) == 2.0


def test_multinomial_log_loss_uses_the_observed_class_probability() -> None:
    score = multinomial_log_loss([(0.5, 0.25, 0.25)], ["home"])

    assert score == pytest.approx(math.log(2.0))


def test_binary_scores_have_hand_derived_values() -> None:
    probabilities = [0.75, 0.25]
    labels = [1, 0]

    assert binary_log_loss(probabilities, labels) == pytest.approx(-math.log(0.75))
    assert binary_brier(probabilities, labels) == pytest.approx(0.0625)


def test_score_predictions_returns_frozen_tie_aware_scorecard() -> None:
    probabilities = [(0.6, 0.3, 0.1), (0.2, 0.7, 0.1), (0.2, 0.3, 0.5)]
    results = ["home", "away", "tie"]

    score = score_predictions(
        probabilities,
        results,
        event_ids=("event-1", "event-2", "event-3"),
        unresolved_count=2,
    )

    assert isinstance(score, Scorecard)
    assert score.n_all_settled == 3
    assert score.n_non_ties == 2
    assert score.n_ties == 1
    assert score.n_unresolved == 2
    assert score.multinomial_log_loss == pytest.approx(
        (-math.log(0.6) - math.log(0.7) - math.log(0.5)) / 3
    )
    assert score.multiclass_brier == pytest.approx(0.26)
    assert score.conditional_non_tie_brier == pytest.approx(13 / 162)
    assert score.straight_up_accuracy == 1.0
    with pytest.raises(FrozenInstanceError):
        score.n_ties = 0  # type: ignore[misc]


def test_straight_up_tie_breaker_selects_home_and_excludes_final_ties() -> None:
    probabilities = [(0.45, 0.45, 0.1), (0.45, 0.45, 0.1)]

    assert straight_up_accuracy(probabilities, ["home", "tie"]) == 1.0


@pytest.mark.parametrize(
    ("probabilities", "results", "message"),
    [
        ([], [], "at least one"),
        ([(0.5, 0.4, 0.1)], ["home", "away"], "aligned"),
        ([(0.5, 0.5)], ["home"], "three values"),
        ([(0.5, 0.4, 0.2)], ["home"], "sum to 1"),
        ([(float("nan"), 0.5, 0.5)], ["home"], "finite"),
        ([(1.1, -0.1, 0.0)], ["home"], r"inside \[0, 1\]"),
        ([(0.5, 0.4, 0.1)], ["void"], "home, away, or tie"),
    ],
)
def test_multiclass_metrics_reject_invalid_prediction_rows(
    probabilities: list[tuple[float, ...]], results: list[str], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        multinomial_log_loss(probabilities, results)  # type: ignore[arg-type]


def test_score_predictions_requires_unique_aligned_event_ids() -> None:
    probabilities = [(0.6, 0.3, 0.1), (0.3, 0.6, 0.1)]

    with pytest.raises(ValueError, match="unique"):
        score_predictions(probabilities, ["home", "away"], event_ids=("same", "same"))
    with pytest.raises(ValueError, match="aligned"):
        score_predictions(probabilities, ["home", "away"], event_ids=("only-one",))


def test_score_predictions_requires_a_non_tie_denominator() -> None:
    with pytest.raises(ValueError, match="at least one non-tie"):
        score_predictions([(0.2, 0.2, 0.6)], ["tie"], event_ids=("event-1",))


@pytest.mark.parametrize("unresolved_count", [-1, True, 1.5])
def test_score_predictions_rejects_invalid_unresolved_counts(unresolved_count: object) -> None:
    with pytest.raises((TypeError, ValueError), match="unresolved"):
        score_predictions(
            [(0.6, 0.3, 0.1)],
            ["home"],
            event_ids=("event-1",),
            unresolved_count=unresolved_count,  # type: ignore[arg-type]
        )


def test_binary_scores_reject_invalid_labels_and_probabilities() -> None:
    with pytest.raises(ValueError, match="binary labels"):
        binary_brier([0.5], [2])
    with pytest.raises(ValueError, match="finite"):
        binary_log_loss([float("inf")], [1])
    with pytest.raises(ValueError, match=r"inside \[0, 1\]"):
        binary_brier([-0.1], [0])
    with pytest.raises(ValueError, match="aligned"):
        binary_log_loss([0.5, 0.6], [1])


def test_log_loss_rejects_invalid_epsilon() -> None:
    with pytest.raises(ValueError, match="epsilon"):
        multinomial_log_loss([(0.6, 0.3, 0.1)], ["home"], epsilon=0.0)
