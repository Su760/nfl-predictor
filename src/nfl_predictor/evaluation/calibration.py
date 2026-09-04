from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt

import numpy as np
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from nfl_predictor.evaluation.metrics import _validated_binary
from nfl_predictor.models.calibration import clipped_logit


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    n: int
    mean_probability: float
    observed_rate: float
    wilson_low: float
    wilson_high: float
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class _BinRows:
    lower: float
    upper: float
    rows: tuple[tuple[float, int, str], ...]


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _reliability_rows(
    probabilities: Sequence[float], labels: Sequence[int], event_ids: Sequence[str]
) -> tuple[tuple[float, int, str], ...]:
    values, targets = _validated_binary(probabilities, labels)
    identifiers = tuple(event_ids)
    if len(identifiers) != len(values):
        raise ValueError("reliability event IDs must be exactly aligned")
    if any(not isinstance(event_id, str) or not event_id.strip() for event_id in identifiers):
        raise ValueError("reliability event IDs must be non-blank strings")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("reliability event IDs must be unique")
    return tuple(zip(values, targets, identifiers, strict=True))


def _merge_tail_bins(raw_bins: Sequence[_BinRows], minimum_count: int) -> tuple[_BinRows, ...]:
    nonempty = [item for item in raw_bins if item.rows]
    merged: list[_BinRows] = []
    pending: _BinRows | None = None
    for item in nonempty:
        if pending is None:
            pending = item
        else:
            pending = _BinRows(
                lower=pending.lower,
                upper=item.upper,
                rows=pending.rows + item.rows,
            )
        if len(pending.rows) >= minimum_count:
            merged.append(pending)
            pending = None
    if pending is not None and merged:
        previous = merged[-1]
        merged[-1] = _BinRows(
            lower=previous.lower,
            upper=pending.upper,
            rows=previous.rows + pending.rows,
        )
    return tuple(merged)


def _summarize(raw_bins: Sequence[_BinRows]) -> tuple[ReliabilityBin, ...]:
    summaries: list[ReliabilityBin] = []
    for item in raw_bins:
        successes = sum(label for _, label, _ in item.rows)
        low, high = wilson_interval(successes, len(item.rows))
        summaries.append(
            ReliabilityBin(
                lower=item.lower,
                upper=item.upper,
                n=len(item.rows),
                mean_probability=sum(probability for probability, _, _ in item.rows)
                / len(item.rows),
                observed_rate=successes / len(item.rows),
                wilson_low=low,
                wilson_high=high,
                event_ids=tuple(event_id for _, _, event_id in item.rows),
            )
        )
    return tuple(summaries)


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if isinstance(successes, bool) or not isinstance(successes, int):
        raise TypeError("Wilson successes must be an integer")
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("Wilson observation count must be an integer")
    if n < 1:
        raise ValueError("Wilson interval requires observations")
    if not 0 <= successes <= n:
        raise ValueError("Wilson successes must be inside [0, n]")
    if isinstance(z, bool) or not isinstance(z, (int, float)):
        raise TypeError("Wilson z must be a real number")
    z_value = float(z)
    if not math.isfinite(z_value) or z_value <= 0.0:
        raise ValueError("Wilson z must be finite and positive")
    rate = successes / n
    denominator = 1.0 + z_value * z_value / n
    center = (rate + z_value * z_value / (2.0 * n)) / denominator
    half = z_value * sqrt((rate * (1.0 - rate) + z_value * z_value / (4.0 * n)) / n) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def fixed_width_reliability(
    probabilities: Sequence[float],
    labels: Sequence[int],
    event_ids: Sequence[str],
    *,
    minimum_count: int = 30,
) -> tuple[ReliabilityBin, ...]:
    minimum = _positive_integer(minimum_count, "minimum count")
    rows = _reliability_rows(probabilities, labels, event_ids)
    buckets: list[list[tuple[float, int, str]]] = [[] for _ in range(10)]
    for row in rows:
        index = min(int(row[0] * 10.0), 9)
        buckets[index].append(row)
    raw = tuple(
        _BinRows(
            index / 10.0,
            (index + 1) / 10.0,
            tuple(sorted(bucket, key=lambda row: (row[0], row[2]))),
        )
        for index, bucket in enumerate(buckets)
    )
    return _summarize(_merge_tail_bins(raw, minimum))


def equal_count_reliability(
    probabilities: Sequence[float],
    labels: Sequence[int],
    event_ids: Sequence[str],
    *,
    bin_count: int = 10,
    minimum_count: int = 30,
) -> tuple[ReliabilityBin, ...]:
    count = _positive_integer(bin_count, "bin count")
    minimum = _positive_integer(minimum_count, "minimum count")
    rows = sorted(
        _reliability_rows(probabilities, labels, event_ids), key=lambda row: (row[0], row[2])
    )
    chunk_size = max(1, math.ceil(len(rows) / count))
    raw: list[_BinRows] = []
    for start in range(0, len(rows), chunk_size):
        chunk = tuple(rows[start : start + chunk_size])
        raw.append(_BinRows(chunk[0][0], chunk[-1][0], chunk))
    return _summarize(_merge_tail_bins(raw, minimum))


def calibration_intercept_slope(
    probabilities: Sequence[float],
    labels: Sequence[int],
    max_iter: int,
    epsilon: float,
) -> tuple[float, float]:
    values, targets = _validated_binary(probabilities, labels)
    if set(targets) != {0, 1}:
        raise ValueError("calibration regression requires both binary classes")
    iterations = _positive_integer(max_iter, "calibration max_iter")
    mapping = LogisticRegression(C=float("inf"), max_iter=iterations)
    mapping.fit(clipped_logit(values, epsilon), np.asarray(targets, dtype=np.int64))
    return float(mapping.intercept_[0]), float(mapping.coef_[0, 0])
