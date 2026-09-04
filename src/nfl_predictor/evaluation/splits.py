from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class SeasonFold:
    estimator_seasons: tuple[int, ...]
    calibration_season: int
    test_season: int

    def __post_init__(self) -> None:
        if set(self.estimator_seasons) & {self.calibration_season, self.test_season}:
            raise ValueError("estimator, calibration, and test seasons must be disjoint")
        if self.calibration_season != self.test_season - 1:
            raise ValueError("calibration season must be T-1")
        if any(season > self.test_season - 2 for season in self.estimator_seasons):
            raise ValueError("estimator seasons must be no later than T-2")


def outer_fold(target_season: int, available_seasons: Iterable[int]) -> SeasonFold:
    if isinstance(target_season, bool) or not isinstance(target_season, int):
        raise TypeError("target season must be an integer")
    if target_season < 1:
        raise ValueError("target season must be positive")
    available_values = tuple(available_seasons)
    if any(isinstance(season, bool) or not isinstance(season, int) for season in available_values):
        raise TypeError("available seasons must contain only integers")
    available = tuple(sorted({season for season in available_values if season <= target_season}))
    estimator = tuple(season for season in available if season <= target_season - 2)
    if not estimator:
        raise ValueError("outer fold requires estimator history")
    if target_season - 1 not in available:
        raise ValueError("outer fold requires the T-1 calibration season")
    return SeasonFold(estimator, target_season - 1, target_season)
