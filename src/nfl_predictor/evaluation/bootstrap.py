from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class GameBundle:
    season: int
    week: int
    canonical_event_id: str
    values: Mapping[str, float]

    def __post_init__(self) -> None:
        if isinstance(self.season, bool) or not isinstance(self.season, int):
            raise TypeError("bundle season must be an integer")
        if self.season < 1:
            raise ValueError("bundle season must be positive")
        if isinstance(self.week, bool) or not isinstance(self.week, int):
            raise TypeError("bundle week must be an integer")
        if self.week < 1:
            raise ValueError("bundle week must be positive")
        if not isinstance(self.canonical_event_id, str) or not self.canonical_event_id.strip():
            raise ValueError("bundle event ID must be a non-blank string")
        copied: dict[str, float] = {}
        for name, value in self.values.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("bundle value names must be non-blank strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("bundle values must be real numbers")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("bundle values must be finite")
            copied[name] = number
        if not copied:
            raise ValueError("bundle values cannot be empty")
        object.__setattr__(self, "values", MappingProxyType(copied))


@dataclass(frozen=True)
class RoiBootstrapResult:
    draws: NDArray[np.float64]
    zero_stake_replicates: int

    def __post_init__(self) -> None:
        values = np.asarray(self.draws, dtype=np.float64).copy()
        if values.ndim != 1 or values.size == 0:
            raise ValueError("ROI bootstrap draws must be a non-empty one-dimensional array")
        if isinstance(self.zero_stake_replicates, bool) or not isinstance(
            self.zero_stake_replicates, int
        ):
            raise TypeError("zero-stake replicate count must be an integer")
        if not 0 <= self.zero_stake_replicates <= values.size:
            raise ValueError("zero-stake replicate count is outside the draw count")
        undefined = int(np.isnan(values).sum())
        if undefined != self.zero_stake_replicates:
            raise ValueError("zero-stake replicate count must match undefined ROI draws")
        if np.isinf(values).any():
            raise ValueError("ROI bootstrap draws cannot contain infinite values")
        values.setflags(write=False)
        object.__setattr__(self, "draws", values)

    @property
    def lower_95(self) -> float | None:
        if self.zero_stake_replicates:
            return None
        return float(np.quantile(self.draws, 0.05))

    @property
    def passes_positive_roi_gate(self) -> bool:
        lower = self.lower_95
        return lower is not None and lower > 0.0


def _bootstrap_parameters(replicates: int, seed: int, block_weeks: int) -> None:
    if isinstance(replicates, bool) or not isinstance(replicates, int):
        raise TypeError("bootstrap replicates must be an integer")
    if replicates < 1:
        raise ValueError("bootstrap replicates must be positive")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("bootstrap seed must be an integer")
    if not 0 <= seed < 2**32:
        raise ValueError("bootstrap seed must be inside [0, 2**32)")
    if isinstance(block_weeks, bool) or not isinstance(block_weeks, int):
        raise TypeError("bootstrap block weeks must be an integer")
    if block_weeks < 1:
        raise ValueError("bootstrap block weeks must be positive")


def _group_bundles(
    bundles: Sequence[GameBundle],
) -> dict[int, dict[int, tuple[GameBundle, ...]]]:
    rows = tuple(bundles)
    if not rows:
        raise ValueError("moving-week bootstrap requires at least one game bundle")
    if any(not isinstance(bundle, GameBundle) for bundle in rows):
        raise TypeError("bootstrap rows must be GameBundle instances")
    identifiers = [bundle.canonical_event_id for bundle in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("bootstrap event IDs must be unique")
    grouped: dict[int, dict[int, list[GameBundle]]] = defaultdict(lambda: defaultdict(list))
    for bundle in rows:
        grouped[bundle.season][bundle.week].append(bundle)
    for season, week_map in grouped.items():
        weeks = sorted(week_map)
        if weeks != list(range(weeks[0], weeks[-1] + 1)):
            raise ValueError(f"bootstrap weeks must be contiguous within season {season}")
    return {
        season: {
            week: tuple(sorted(week_rows, key=lambda item: item.canonical_event_id))
            for week, week_rows in week_map.items()
        }
        for season, week_map in grouped.items()
    }


def _samples(
    grouped: Mapping[int, Mapping[int, tuple[GameBundle, ...]]],
    replicates: int,
    seed: int,
    block_weeks: int,
) -> Iterator[list[GameBundle]]:
    rng = np.random.default_rng(seed)
    for _ in range(replicates):
        sample: list[GameBundle] = []
        for season in sorted(grouped):
            week_map = grouped[season]
            weeks = sorted(week_map)
            sampled_weeks: list[int] = []
            while len(sampled_weeks) < len(weeks):
                start = int(rng.integers(0, len(weeks)))
                sampled_weeks.extend(
                    weeks[(start + offset) % len(weeks)] for offset in range(block_weeks)
                )
            for week in sampled_weeks[: len(weeks)]:
                sample.extend(week_map[week])
        yield sample


def moving_week_bootstrap(
    bundles: Sequence[GameBundle],
    statistic: Callable[[list[GameBundle]], float],
    replicates: int,
    seed: int,
    block_weeks: int,
) -> NDArray[np.float64]:
    _bootstrap_parameters(replicates, seed, block_weeks)
    grouped = _group_bundles(bundles)
    values = np.empty(replicates, dtype=np.float64)
    for index, sample in enumerate(_samples(grouped, replicates, seed, block_weeks)):
        result = statistic(sample)
        if isinstance(result, bool) or not isinstance(result, (int, float, np.floating)):
            raise TypeError("bootstrap statistic must return a real number")
        values[index] = float(result)
        if not math.isfinite(values[index]):
            raise ValueError("bootstrap statistic must return a finite value")
    return values


def moving_week_roi_bootstrap(
    bundles: Sequence[GameBundle],
    replicates: int,
    seed: int,
    block_weeks: int,
) -> RoiBootstrapResult:
    _bootstrap_parameters(replicates, seed, block_weeks)
    grouped = _group_bundles(bundles)
    for bundle in bundles:
        if "profit" not in bundle.values or "stake" not in bundle.values:
            raise ValueError("ROI bundles require profit and stake values")
        if bundle.values["stake"] < 0.0:
            raise ValueError("ROI stakes cannot be negative")
    draws = np.empty(replicates, dtype=np.float64)
    zero_stake = 0
    for index, sample in enumerate(_samples(grouped, replicates, seed, block_weeks)):
        total_stake = sum(bundle.values["stake"] for bundle in sample)
        if total_stake == 0.0:
            draws[index] = np.nan
            zero_stake += 1
        else:
            draws[index] = sum(bundle.values["profit"] for bundle in sample) / total_stake
    return RoiBootstrapResult(draws=draws, zero_stake_replicates=zero_stake)
