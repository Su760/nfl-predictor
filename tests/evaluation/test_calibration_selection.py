from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from nfl_predictor.contracts.enums import Origin
from nfl_predictor.evaluation.calibration import (
    calibration_intercept_slope,
    equal_count_reliability,
    fixed_width_reliability,
    wilson_interval,
)
from nfl_predictor.evaluation.promotion import EvaluationPolicy, load_evaluation_policy
from nfl_predictor.models.baselines import ModelPolicy, load_model_policy
from nfl_predictor.models.calibration import (
    CALIBRATOR_REGISTRY,
    IdentityCalibrator,
    SigmoidCalibrator,
    clipped_logit,
)
from nfl_predictor.models.selection import CalibrationFoldLedger, select_calibration_family

ROOT = Path(__file__).resolve().parents[2]


def _evaluation_policy(**overrides: object) -> EvaluationPolicy:
    values: dict[str, object] = {
        "policy_version": "test-evaluation-v1",
        "bootstrap_replicates": 100,
        "bootstrap_seed": 20260828,
        "moving_block_weeks": 3,
        "calibration_regression_max_iter": 2000,
        "log_loss_epsilon": 1e-15,
        "minimum_promotion_seasons": 2,
        "minimum_systematic_staking_seasons": 2,
        "minimum_common_games": 4,
        "maximum_brier_degradation": 1.0,
        "minimum_calibration_intercept": -100.0,
        "maximum_calibration_intercept": 100.0,
        "minimum_calibration_slope": 0.001,
        "maximum_calibration_slope": 100.0,
    }
    values.update(overrides)
    return EvaluationPolicy.model_validate(values)


def _model_policy(selection_tolerance: float = 10.0) -> ModelPolicy:
    policy = load_model_policy(ROOT / "configs/model_policy_v1.toml")
    calibration = policy.calibration.model_copy(update={"selection_tolerance": selection_tolerance})
    return policy.model_copy(update={"calibration": calibration})


def _ledger(season: int, *, origin: Origin = Origin.T72) -> CalibrationFoldLedger:
    calibration_ids = tuple(f"cal-{season - 1}-{index}" for index in range(8))
    target_ids = tuple(f"test-{season}-{index}" for index in range(8))
    return CalibrationFoldLedger(
        origin=origin,
        target_season=season,
        estimator_event_ids=(f"train-{season}",),
        calibration_event_ids=calibration_ids,
        target_event_ids=target_ids,
        calibration_r_home=(0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9),
        target_r_home=(0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9),
    )


class _LabelReader:
    def __init__(self) -> None:
        self.read_seasons: list[int] = []

    def read(self, season: int, event_ids: Sequence[str]) -> tuple[int, ...]:
        self.read_seasons.append(season)
        assert all(str(season) in event_id for event_id in event_ids)
        return (0, 0, 1, 0, 1, 0, 1, 1)


class _FoldLedgers:
    def __init__(self) -> None:
        self.model_policy = _model_policy()
        self.evaluation_policy = _evaluation_policy()
        self._ledgers = {season: _ledger(season) for season in range(2019, 2025)}

    def read(self, *, origin: Origin, target_season: int) -> CalibrationFoldLedger:
        assert origin is Origin.T72
        return self._ledgers[target_season]


def test_2026_family_selection_never_reads_2025_labels() -> None:
    label_reader = _LabelReader()

    selected = select_calibration_family(
        target_season=2026,
        origin=Origin.T72,
        fold_ledgers=_FoldLedgers(),
        label_reader=label_reader,
    )

    assert selected == "identity"
    assert max(label_reader.read_seasons) == 2024
    assert 2025 not in label_reader.read_seasons


def test_calibrator_registry_excludes_ineligible_isotonic_family() -> None:
    assert tuple(CALIBRATOR_REGISTRY) == ("identity", "sigmoid")


def test_calibration_fold_ledger_rejects_overlapping_lineage() -> None:
    with pytest.raises(ValueError, match="disjoint"):
        CalibrationFoldLedger(
            origin=Origin.T72,
            target_season=2026,
            estimator_event_ids=("shared",),
            calibration_event_ids=("shared",),
            target_event_ids=("test",),
            calibration_r_home=(0.5,),
            target_r_home=(0.5,),
        )


def test_calibration_fold_ledger_rejects_duplicate_or_misaligned_rows() -> None:
    with pytest.raises(ValueError, match="unique"):
        CalibrationFoldLedger(
            origin=Origin.T72,
            target_season=2026,
            estimator_event_ids=("train",),
            calibration_event_ids=("cal", "cal"),
            target_event_ids=("test",),
            calibration_r_home=(0.4, 0.6),
            target_r_home=(0.5,),
        )
    with pytest.raises(ValueError, match="aligned"):
        CalibrationFoldLedger(
            origin=Origin.T72,
            target_season=2026,
            estimator_event_ids=("train",),
            calibration_event_ids=("cal-1", "cal-2"),
            target_event_ids=("test",),
            calibration_r_home=(0.4,),
            target_r_home=(0.5,),
        )


def test_clipped_logit_applies_only_the_declared_numerical_epsilon() -> None:
    logits = clipped_logit([0.0, 0.5, 1.0], epsilon=0.01)

    assert logits.shape == (3, 1)
    assert logits[:, 0] == pytest.approx([math.log(0.01 / 0.99), 0.0, math.log(0.99 / 0.01)])


def test_identity_calibrator_preserves_valid_probabilities() -> None:
    calibrator = IdentityCalibrator.from_policy(_model_policy()).fit([0.2, 0.8], [0, 1])

    assert calibrator.transform([0.2, 0.8]).tolist() == [0.2, 0.8]


def test_sigmoid_calibrator_requires_both_classes_and_returns_probabilities() -> None:
    calibrator = SigmoidCalibrator.from_policy(_model_policy())
    with pytest.raises(ValueError, match="both binary classes"):
        calibrator.fit([0.2, 0.8], [1, 1])

    fitted = calibrator.fit([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
    transformed = fitted.transform([0.2, 0.8])

    assert transformed.shape == (2,)
    assert 0.0 < transformed[0] < transformed[1] < 1.0


def test_calibration_intercept_slope_is_finite_and_warning_free_on_sklearn_1_9() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        intercept, slope = calibration_intercept_slope(
            [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9],
            [0, 0, 1, 0, 1, 0, 1, 1],
            max_iter=2000,
            epsilon=1e-15,
        )

    assert math.isfinite(intercept)
    assert slope > 0.0
    assert caught == []


def test_fixed_width_reliability_uses_frozen_deciles_and_wilson_counts() -> None:
    probabilities = [0.05] * 30 + [0.95] * 30
    labels = [0, 1] * 15 + [1] * 27 + [0] * 3
    event_ids = [f"event-{index:02d}" for index in range(60)]

    bins = fixed_width_reliability(probabilities, labels, event_ids, minimum_count=30)

    assert [(item.lower, item.upper, item.n) for item in bins] == [
        (0.0, 0.1, 30),
        (0.9, 1.0, 30),
    ]
    assert bins[0].mean_probability == pytest.approx(0.05)
    assert bins[0].observed_rate == 0.5
    assert bins[1].observed_rate == 0.9
    assert bins[0].event_ids == tuple(event_ids[:30])


def test_reliability_suppresses_a_total_sample_below_the_minimum() -> None:
    assert fixed_width_reliability([0.5] * 29, [0] * 29, [f"e-{i}" for i in range(29)]) == ()


def test_fixed_width_reliability_membership_is_input_order_invariant() -> None:
    probabilities = [0.05] * 30
    labels = [index % 2 for index in range(30)]
    event_ids = [f"event-{index:02d}" for index in range(30)]

    forward = fixed_width_reliability(probabilities, labels, event_ids, minimum_count=30)
    reverse = fixed_width_reliability(
        list(reversed(probabilities)),
        list(reversed(labels)),
        list(reversed(event_ids)),
        minimum_count=30,
    )

    assert forward == reverse


def test_equal_count_reliability_is_stable_by_probability_then_event_id() -> None:
    probabilities = [0.8] * 30 + [0.2] * 30
    labels = [1] * 30 + [0] * 30
    event_ids = [f"z-{index:02d}" for index in range(30)] + [
        f"a-{index:02d}" for index in range(30)
    ]

    forward = equal_count_reliability(
        probabilities, labels, event_ids, bin_count=2, minimum_count=30
    )
    reverse = equal_count_reliability(
        list(reversed(probabilities)),
        list(reversed(labels)),
        list(reversed(event_ids)),
        bin_count=2,
        minimum_count=30,
    )

    assert forward == reverse
    assert forward[0].event_ids == tuple(f"a-{index:02d}" for index in range(30))
    assert forward[1].event_ids == tuple(f"z-{index:02d}" for index in range(30))


def test_equal_count_reliability_merges_an_undersized_tail() -> None:
    probabilities = np.linspace(0.01, 0.99, 305).tolist()
    labels = [index % 2 for index in range(305)]
    event_ids = [f"event-{index:03d}" for index in range(305)]

    bins = equal_count_reliability(probabilities, labels, event_ids, bin_count=10, minimum_count=30)

    assert len(bins) == 9
    assert [item.n for item in bins[:-1]] == [31] * 8
    assert bins[-1].n == 57


def test_wilson_interval_has_hand_checked_one_of_one_bounds() -> None:
    low, high = wilson_interval(1, 1)

    assert low == pytest.approx(0.2065493144)
    assert high == 1.0


def test_reliability_rejects_duplicate_event_ids_and_invalid_domains() -> None:
    with pytest.raises(ValueError, match="unique"):
        fixed_width_reliability([0.2, 0.8], [0, 1], ["same", "same"], minimum_count=1)
    with pytest.raises(ValueError, match="minimum count"):
        fixed_width_reliability([0.2], [0], ["event"], minimum_count=0)
    with pytest.raises((TypeError, ValueError), match="bin count"):
        equal_count_reliability([0.2], [0], ["event"], bin_count=True, minimum_count=1)


def test_selection_rejects_a_ledger_from_another_origin() -> None:
    ledgers = _FoldLedgers()
    ledgers._ledgers[2019] = _ledger(2019, origin=Origin.T60)

    with pytest.raises(ValueError, match="origin"):
        select_calibration_family(2026, Origin.T72, ledgers, _LabelReader())


def test_evaluation_policy_file_loads_exact_declared_values() -> None:
    policy = load_evaluation_policy(ROOT / "configs/evaluation_policy_v1.toml")

    assert policy.policy_version == "evaluation-v1"
    assert policy.bootstrap_replicates == 10_000
    assert policy.bootstrap_seed == 20_260_828
    assert policy.moving_block_weeks == 3
