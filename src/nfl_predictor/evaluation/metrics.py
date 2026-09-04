from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

Outcome: TypeAlias = Literal["home", "away", "tie"]
ProbabilityRow: TypeAlias = tuple[float, float, float]

CLASS_INDEX: dict[Outcome, int] = {"home": 0, "away": 1, "tie": 2}
_ALLOWED_OUTCOMES = frozenset(CLASS_INDEX)
_NORMALIZATION_TOLERANCE = 1e-12


@dataclass(frozen=True)
class Scorecard:
    n_all_settled: int
    n_non_ties: int
    n_ties: int
    n_unresolved: int
    multinomial_log_loss: float
    multiclass_brier: float
    conditional_non_tie_log_loss: float
    conditional_non_tie_brier: float
    straight_up_accuracy: float


def _validated_epsilon(epsilon: float) -> float:
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)):
        raise TypeError("epsilon must be a real number")
    value = float(epsilon)
    if not math.isfinite(value) or not 0.0 < value < 0.5:
        raise ValueError("epsilon must be finite and inside (0, 0.5)")
    return value


def _validated_multiclass(
    probabilities: Sequence[Sequence[float]], results: Sequence[str]
) -> tuple[tuple[ProbabilityRow, ...], tuple[Outcome, ...]]:
    probability_rows = tuple(probabilities)
    outcomes = tuple(results)
    if not probability_rows:
        raise ValueError("multiclass scores require at least one settled prediction")
    if len(probability_rows) != len(outcomes):
        raise ValueError("probabilities and results must be exactly aligned")
    invalid_outcomes = set(outcomes) - _ALLOWED_OUTCOMES
    if invalid_outcomes:
        raise ValueError("results must contain only home, away, or tie")
    validated: list[ProbabilityRow] = []
    for row in probability_rows:
        if len(row) != 3:
            raise ValueError("each probability row must contain exactly three values")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in row):
            raise TypeError("probabilities must be real numbers")
        values = cast(ProbabilityRow, tuple(float(value) for value in row))
        if any(not math.isfinite(value) for value in values):
            raise ValueError("probabilities must be finite")
        if any(not 0.0 <= value <= 1.0 for value in values):
            raise ValueError("probabilities must be inside [0, 1]")
        if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=_NORMALIZATION_TOLERANCE):
            raise ValueError("probabilities must sum to 1")
        validated.append(values)
    return tuple(validated), cast(tuple[Outcome, ...], outcomes)


def _validated_binary(
    probabilities: Sequence[float], labels: Sequence[int]
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    values = tuple(probabilities)
    targets = tuple(labels)
    if not values:
        raise ValueError("binary scores require at least one observation")
    if len(values) != len(targets):
        raise ValueError("binary probabilities and labels must be exactly aligned")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise TypeError("binary probabilities must be real numbers")
    probabilities_float = tuple(float(value) for value in values)
    if any(not math.isfinite(value) for value in probabilities_float):
        raise ValueError("binary probabilities must be finite")
    if any(not 0.0 <= value <= 1.0 for value in probabilities_float):
        raise ValueError("binary probabilities must be inside [0, 1]")
    if any(isinstance(label, bool) or not isinstance(label, int) for label in targets):
        raise TypeError("binary labels must be integers")
    if set(targets) - {0, 1}:
        raise ValueError("binary labels must contain only 0 or 1")
    return probabilities_float, targets


def multinomial_log_loss(
    probabilities: Sequence[Sequence[float]],
    results: Sequence[str],
    epsilon: float = 1e-15,
) -> float:
    rows, outcomes = _validated_multiclass(probabilities, results)
    epsilon_value = _validated_epsilon(epsilon)
    losses = [
        -math.log(min(1.0 - epsilon_value, max(epsilon_value, row[CLASS_INDEX[result]])))
        for row, result in zip(rows, outcomes, strict=True)
    ]
    return sum(losses) / len(losses)


def multiclass_brier(probabilities: Sequence[Sequence[float]], results: Sequence[str]) -> float:
    rows, outcomes = _validated_multiclass(probabilities, results)
    scores = [
        sum((value - float(index == CLASS_INDEX[result])) ** 2 for index, value in enumerate(row))
        for row, result in zip(rows, outcomes, strict=True)
    ]
    return sum(scores) / len(scores)


def binary_log_loss(
    probabilities: Sequence[float], labels: Sequence[int], epsilon: float = 1e-15
) -> float:
    values, targets = _validated_binary(probabilities, labels)
    epsilon_value = _validated_epsilon(epsilon)
    losses: list[float] = []
    for probability, label in zip(values, targets, strict=True):
        value = min(1.0 - epsilon_value, max(epsilon_value, probability))
        losses.append(-(label * math.log(value) + (1 - label) * math.log(1.0 - value)))
    return sum(losses) / len(losses)


def binary_brier(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    values, targets = _validated_binary(probabilities, labels)
    return sum(
        (probability - label) ** 2 for probability, label in zip(values, targets, strict=True)
    ) / len(targets)


def conditional_non_tie_home(
    probabilities: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    rows = tuple(probabilities)
    if not rows:
        raise ValueError("conditional scores require at least one probability row")
    conditional: list[float] = []
    for row in rows:
        if len(row) != 3:
            raise ValueError("each probability row must contain exactly three values")
        p_home, p_away, p_tie = row
        _validated_multiclass([(p_home, p_away, p_tie)], ["home"])
        denominator = p_home + p_away
        if denominator <= 0.0:
            raise ValueError("non-tie probability mass must be positive")
        conditional.append(p_home / denominator)
    return tuple(conditional)


def straight_up_accuracy(probabilities: Sequence[Sequence[float]], results: Sequence[str]) -> float:
    rows, outcomes = _validated_multiclass(probabilities, results)
    settled_non_ties = [
        (("home" if p_home >= p_away else "away"), result)
        for (p_home, p_away, _), result in zip(rows, outcomes, strict=True)
        if result != "tie"
    ]
    if not settled_non_ties:
        raise ValueError("straight-up accuracy requires at least one non-tie")
    return sum(predicted == actual for predicted, actual in settled_non_ties) / len(
        settled_non_ties
    )


def score_predictions(
    probabilities: Sequence[Sequence[float]],
    results: Sequence[str],
    *,
    event_ids: Sequence[str],
    unresolved_count: int = 0,
) -> Scorecard:
    rows, outcomes = _validated_multiclass(probabilities, results)
    identifiers = tuple(event_ids)
    if len(identifiers) != len(rows):
        raise ValueError("event IDs and predictions must be exactly aligned")
    if any(not isinstance(event_id, str) or not event_id.strip() for event_id in identifiers):
        raise ValueError("event IDs must be non-blank strings")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("event IDs must be unique")
    if isinstance(unresolved_count, bool) or not isinstance(unresolved_count, int):
        raise TypeError("unresolved count must be an integer")
    if unresolved_count < 0:
        raise ValueError("unresolved count cannot be negative")
    non_tie_indices = [index for index, result in enumerate(outcomes) if result != "tie"]
    if not non_tie_indices:
        raise ValueError("conditional/winner scorecards require at least one non-tie")
    non_tie_rows = [rows[index] for index in non_tie_indices]
    conditional = conditional_non_tie_home(non_tie_rows)
    binary_labels = [int(outcomes[index] == "home") for index in non_tie_indices]
    return Scorecard(
        n_all_settled=len(outcomes),
        n_non_ties=len(non_tie_indices),
        n_ties=len(outcomes) - len(non_tie_indices),
        n_unresolved=unresolved_count,
        multinomial_log_loss=multinomial_log_loss(rows, outcomes),
        multiclass_brier=multiclass_brier(rows, outcomes),
        conditional_non_tie_log_loss=binary_log_loss(conditional, binary_labels),
        conditional_non_tie_brier=binary_brier(conditional, binary_labels),
        straight_up_accuracy=straight_up_accuracy(rows, outcomes),
    )
