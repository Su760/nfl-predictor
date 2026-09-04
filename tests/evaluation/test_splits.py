from __future__ import annotations

import pytest

from nfl_predictor.evaluation.splits import outer_fold


def test_2026_fold_uses_through_2024_for_fit_and_2025_for_mapping() -> None:
    fold = outer_fold(2026, available_seasons=range(2016, 2026))

    assert fold.estimator_seasons == tuple(range(2016, 2025))
    assert fold.calibration_season == 2025
    assert fold.test_season == 2026
    assert set(fold.estimator_seasons).isdisjoint({fold.calibration_season, fold.test_season})


def test_outer_fold_requires_the_t_minus_one_calibration_season() -> None:
    with pytest.raises(ValueError, match="T-1 calibration season"):
        outer_fold(2026, available_seasons=range(2016, 2025))


def test_outer_fold_requires_estimator_history() -> None:
    with pytest.raises(ValueError, match="estimator history"):
        outer_fold(2026, available_seasons=[2025])


@pytest.mark.parametrize("target", [True, 2026.0, "2026"])
def test_outer_fold_rejects_non_integer_target_seasons(target: object) -> None:
    with pytest.raises(TypeError, match="target season"):
        outer_fold(target, available_seasons=range(2016, 2026))  # type: ignore[arg-type]


def test_outer_fold_rejects_non_integer_available_seasons() -> None:
    with pytest.raises(TypeError, match="available seasons"):
        outer_fold(2026, available_seasons=[2024, 2025.0])  # type: ignore[list-item]


def test_outer_fold_deduplicates_and_orders_available_history() -> None:
    fold = outer_fold(2026, available_seasons=[2025, 2024, 2023, 2024, 2027])

    assert fold.estimator_seasons == (2023, 2024)
