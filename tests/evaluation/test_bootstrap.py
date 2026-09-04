from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from nfl_predictor.evaluation.bootstrap import (
    GameBundle,
    RoiBootstrapResult,
    moving_week_bootstrap,
    moving_week_roi_bootstrap,
)


def _bundle(
    event_id: str,
    week: int,
    value: float,
    *,
    season: int = 2025,
) -> GameBundle:
    return GameBundle(
        season=season,
        week=week,
        canonical_event_id=event_id,
        values={"value": value},
    )


def test_stored_seed_produces_hand_derived_circular_two_week_samples() -> None:
    bundles = [_bundle(f"event-{week}", week, float(week)) for week in range(1, 5)]

    draws = moving_week_bootstrap(
        bundles,
        statistic=lambda rows: sum(row.values["value"] for row in rows) / len(rows),
        replicates=5,
        seed=20260828,
        block_weeks=2,
    )

    assert draws.tolist() == [3.0, 3.0, 2.5, 2.0, 2.0]


def test_bootstrap_keeps_paired_model_values_in_one_game_bundle() -> None:
    bundles = [
        GameBundle(2025, week, f"event-{week}", {"challenger": 0.4, "incumbent": 0.5})
        for week in range(1, 5)
    ]

    draws = moving_week_bootstrap(
        bundles,
        statistic=lambda rows: (
            sum(row.values["challenger"] - row.values["incumbent"] for row in rows) / len(rows)
        ),
        replicates=5,
        seed=20260828,
        block_weeks=3,
    )

    assert draws == pytest.approx([-0.1] * 5)


def test_multi_season_bootstrap_resamples_each_season_without_crossing_boundaries() -> None:
    bundles = [
        _bundle("2024-1", 1, 1.0, season=2024),
        _bundle("2024-2", 2, 2.0, season=2024),
        _bundle("2025-1", 1, 10.0, season=2025),
        _bundle("2025-2", 2, 20.0, season=2025),
    ]

    draws = moving_week_bootstrap(
        bundles,
        statistic=lambda rows: float(
            (sum(row.season == 2024 for row in rows), sum(row.season == 2025 for row in rows))
            == (2, 2)
        ),
        replicates=10,
        seed=7,
        block_weeks=1,
    )

    assert draws.tolist() == [1.0] * 10


def test_bootstrap_rejects_missing_weeks_in_a_season() -> None:
    bundles = [
        _bundle("event-1", 1, 1.0),
        _bundle("event-3", 3, 3.0),
        _bundle("event-4", 4, 4.0),
    ]

    with pytest.raises(ValueError, match="contiguous"):
        moving_week_bootstrap(
            bundles,
            statistic=lambda rows: sum(row.values["value"] for row in rows),
            replicates=1,
            seed=20260828,
            block_weeks=2,
        )


def test_roi_bootstrap_preserves_and_counts_undefined_zero_stake_replicates() -> None:
    bundles = [
        GameBundle(2025, 1, "event-1", {"profit": 0.0, "stake": 0.0}),
        GameBundle(2025, 2, "event-2", {"profit": 0.0, "stake": 0.0}),
    ]

    result = moving_week_roi_bootstrap(bundles, replicates=7, seed=20260828, block_weeks=1)

    assert isinstance(result, RoiBootstrapResult)
    assert result.zero_stake_replicates == 7
    assert np.isnan(result.draws).all()
    assert result.lower_95 is None
    assert result.passes_positive_roi_gate is False


def test_roi_bootstrap_reports_positive_lower_bound_only_without_zero_stakes() -> None:
    bundles = [
        GameBundle(2025, 1, "event-1", {"profit": 1.0, "stake": 2.0}),
        GameBundle(2025, 2, "event-2", {"profit": 2.0, "stake": 4.0}),
    ]

    result = moving_week_roi_bootstrap(bundles, replicates=10, seed=20260828, block_weeks=1)

    assert result.zero_stake_replicates == 0
    assert result.draws.tolist() == [0.5] * 10
    assert result.lower_95 == 0.5
    assert result.passes_positive_roi_gate is True
    with pytest.raises(ValueError, match="read-only"):
        result.draws[0] = 0.0
    with pytest.raises(FrozenInstanceError):
        result.zero_stake_replicates = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("replicates", "seed", "block_weeks", "message"),
    [
        (0, 1, 1, "replicates"),
        (True, 1, 1, "replicates"),
        (1, -1, 1, "seed"),
        (1, 2**32, 1, "seed"),
        (1, 1, 0, "block weeks"),
    ],
)
def test_bootstrap_rejects_invalid_policy_domains(
    replicates: object, seed: object, block_weeks: object, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        moving_week_bootstrap(
            [_bundle("event", 1, 1.0)],
            statistic=lambda rows: rows[0].values["value"],
            replicates=replicates,  # type: ignore[arg-type]
            seed=seed,  # type: ignore[arg-type]
            block_weeks=block_weeks,  # type: ignore[arg-type]
        )


def test_bootstrap_rejects_empty_or_duplicate_game_bundles() -> None:
    with pytest.raises(ValueError, match="at least one"):
        moving_week_bootstrap([], lambda rows: 0.0, 1, 1, 1)
    duplicate = [_bundle("same", 1, 1.0), _bundle("same", 2, 2.0)]
    with pytest.raises(ValueError, match="unique"):
        moving_week_bootstrap(duplicate, lambda rows: 0.0, 1, 1, 1)


def test_game_bundle_rejects_invalid_metadata_and_values() -> None:
    with pytest.raises(ValueError, match="week"):
        _bundle("event", 0, 1.0)
    with pytest.raises(ValueError, match="finite"):
        _bundle("event", 1, math.inf)
    with pytest.raises(ValueError, match="non-blank"):
        _bundle(" ", 1, 1.0)


def test_generic_bootstrap_rejects_non_finite_statistics() -> None:
    with pytest.raises(ValueError, match="finite"):
        moving_week_bootstrap(
            [_bundle("event", 1, 1.0)],
            statistic=lambda rows: math.nan,
            replicates=1,
            seed=1,
            block_weeks=1,
        )
