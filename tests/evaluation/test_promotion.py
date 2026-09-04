from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.evaluation.promotion import (
    EvaluationPolicy,
    ModelEvaluation,
    PromotionPolicy,
    load_evaluation_policy,
)

ROOT = Path(__file__).resolve().parents[2]


def _policy(**overrides: object) -> EvaluationPolicy:
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
        "maximum_brier_degradation": 0.005,
        "minimum_calibration_intercept": -0.10,
        "maximum_calibration_intercept": 0.10,
        "minimum_calibration_slope": 0.80,
        "maximum_calibration_slope": 1.20,
    }
    values.update(overrides)
    return EvaluationPolicy.model_validate(values)


def _evaluation(
    *,
    challenger: bool,
    event_ids: tuple[str, ...] = ("2024-a", "2024-b", "2025-a", "2025-b"),
    origin: Origin = Origin.T72,
    model_lane: str = "football_only",
    confirmatory: bool | None = None,
    evidence_grade: ProvenanceGrade = ProvenanceGrade.A,
    evidence_basis: str = "prospective",
    estimator_training_event_ids: tuple[str, ...] = ("train-1", "train-2"),
    calibration_event_ids: tuple[str, ...] = ("calibration-1",),
    loss: float | None = None,
    brier: float | None = None,
    brier_by_event: dict[str, float] | None = None,
    intercept: float = 0.0,
    slope: float = 1.0,
) -> ModelEvaluation:
    chosen_loss = loss if loss is not None else (0.5 if challenger else 0.6)
    chosen_brier = brier if brier is not None else (0.39 if challenger else 0.40)
    brier_rows = brier_by_event or {event_id: chosen_brier for event_id in event_ids}
    return ModelEvaluation(
        event_ids=event_ids,
        estimator_training_event_ids=estimator_training_event_ids,
        calibration_event_ids=calibration_event_ids,
        origin=origin,
        model_lane=model_lane,  # type: ignore[arg-type]
        confirmatory=challenger if confirmatory is None else confirmatory,
        evidence_grade=evidence_grade,
        evidence_basis=evidence_basis,  # type: ignore[arg-type]
        season_by_event={event_id: int(event_id[:4]) for event_id in event_ids},
        week_by_event={event_id: index % 2 + 1 for index, event_id in enumerate(event_ids)},
        multinomial_log_loss_by_event={event_id: chosen_loss for event_id in event_ids},
        multiclass_brier_by_event=brier_rows,
        calibration_intercept=intercept,
        calibration_slope=slope,
    )


def _decision(
    challenger: ModelEvaluation | None = None,
    incumbent: ModelEvaluation | None = None,
    policy: EvaluationPolicy | None = None,
):
    return PromotionPolicy(policy or _policy()).evaluate(
        incumbent or _evaluation(challenger=False),
        challenger or _evaluation(challenger=True),
    )


def test_confirmatory_prospective_grade_a_challenger_promotes_on_all_thresholds() -> None:
    decision = _decision()

    assert decision.promote is True
    assert decision.reason_codes == ()
    assert decision.upper_95_log_loss_delta == pytest.approx(-0.1)
    with pytest.raises(FrozenInstanceError):
        decision.promote = False  # type: ignore[misc]


def test_challenger_cannot_promote_by_dropping_hard_games() -> None:
    challenger = _evaluation(challenger=True, event_ids=("2024-a", "2024-b", "2025-a"))

    decision = _decision(challenger=challenger)

    assert decision.promote is False
    assert "COVERAGE_GAP" in decision.reason_codes


def test_challenger_superset_uses_common_games_for_brier_guardrail() -> None:
    challenger_ids = (
        "2024-a",
        "2024-b",
        "2025-a",
        "2025-b",
        "2025-easy-a",
        "2025-easy-b",
    )
    challenger = _evaluation(
        challenger=True,
        event_ids=challenger_ids,
        brier_by_event={
            "2024-a": 0.406,
            "2024-b": 0.406,
            "2025-a": 0.406,
            "2025-b": 0.406,
            "2025-easy-a": 0.0,
            "2025-easy-b": 0.0,
        },
    )

    decision = _decision(challenger=challenger)

    assert decision.promote is False
    assert "BRIER_GUARDRAIL" in decision.reason_codes


def test_promotion_fails_closed_across_origins() -> None:
    decision = _decision(challenger=_evaluation(challenger=True, origin=Origin.T60))

    assert decision.promote is False
    assert "ORIGIN_MISMATCH" in decision.reason_codes


def test_promotion_fails_closed_across_model_lanes() -> None:
    decision = _decision(challenger=_evaluation(challenger=True, model_lane="market_blend"))

    assert decision.promote is False
    assert "MODEL_LANE_MISMATCH" in decision.reason_codes


def test_exploratory_challenger_cannot_promote() -> None:
    decision = _decision(challenger=_evaluation(challenger=True, confirmatory=False))

    assert decision.promote is False
    assert "CHALLENGER_NOT_CONFIRMATORY" in decision.reason_codes


@pytest.mark.parametrize(
    ("grade", "basis"),
    [
        (ProvenanceGrade.C, "historical"),
        (ProvenanceGrade.B, "prospective"),
        (ProvenanceGrade.A, "historical"),
    ],
)
def test_promotion_rejects_unqualified_or_mislabeled_evidence(
    grade: ProvenanceGrade, basis: str
) -> None:
    challenger = _evaluation(challenger=True, evidence_grade=grade, evidence_basis=basis)

    decision = _decision(challenger=challenger)

    assert decision.promote is False
    assert "INELIGIBLE_EVIDENCE" in decision.reason_codes


def test_separately_labeled_historical_grade_b_evidence_is_formally_eligible() -> None:
    incumbent = _evaluation(
        challenger=False,
        evidence_grade=ProvenanceGrade.B,
        evidence_basis="historical",
    )
    challenger = _evaluation(
        challenger=True,
        evidence_grade=ProvenanceGrade.B,
        evidence_basis="historical",
    )

    decision = _decision(challenger=challenger, incumbent=incumbent)

    assert decision.promote is True


def test_promotion_does_not_mix_prospective_and_historical_evidence_cohorts() -> None:
    challenger = _evaluation(
        challenger=True,
        evidence_grade=ProvenanceGrade.B,
        evidence_basis="historical",
    )

    decision = _decision(challenger=challenger)

    assert decision.promote is False
    assert "EVIDENCE_MISMATCH" in decision.reason_codes


def test_promotion_requires_common_games_and_two_outer_test_seasons() -> None:
    one_season_ids = ("2025-a", "2025-b", "2025-c", "2025-d")
    decision = _decision(
        challenger=_evaluation(challenger=True, event_ids=one_season_ids),
        incumbent=_evaluation(challenger=False, event_ids=one_season_ids),
    )

    assert decision.promote is False
    assert "INSUFFICIENT_PROSPECTIVE_SAMPLE" in decision.reason_codes


def test_promotion_requires_upper_log_loss_bound_below_zero() -> None:
    decision = _decision(challenger=_evaluation(challenger=True, loss=0.7))

    assert decision.promote is False
    assert decision.upper_95_log_loss_delta == pytest.approx(0.1)
    assert "LOG_LOSS_INTERVAL_NOT_SUPERIOR" in decision.reason_codes


def test_promotion_applies_brier_and_calibration_guardrails() -> None:
    challenger = _evaluation(
        challenger=True,
        brier=0.406,
        intercept=0.11,
        slope=0.79,
    )

    decision = _decision(challenger=challenger)

    assert "BRIER_GUARDRAIL" in decision.reason_codes
    assert "CALIBRATION_INTERCEPT_GUARDRAIL" in decision.reason_codes
    assert "CALIBRATION_SLOPE_GUARDRAIL" in decision.reason_codes


def test_common_event_season_and_week_metadata_must_match() -> None:
    challenger = _evaluation(challenger=True)
    challenger = replace(
        challenger,
        week_by_event={**challenger.week_by_event, "2024-a": 18},
    )

    decision = _decision(challenger=challenger)

    assert decision.promote is False
    assert "EVENT_METADATA_MISMATCH" in decision.reason_codes


def test_model_evaluation_enforces_unique_exactly_aligned_finite_rows() -> None:
    with pytest.raises(ValueError, match="unique"):
        _evaluation(challenger=False, event_ids=("2024-a", "2024-a"))

    evaluation = _evaluation(challenger=False)
    with pytest.raises(ValueError, match="exactly align"):
        replace(
            evaluation,
            multinomial_log_loss_by_event={"2024-a": 0.5},
        )
    with pytest.raises(ValueError, match="finite"):
        replace(
            evaluation,
            multinomial_log_loss_by_event={
                **evaluation.multinomial_log_loss_by_event,
                "2024-a": float("nan"),
            },
        )
    with pytest.raises(ValueError, match="exactly align"):
        replace(evaluation, multiclass_brier_by_event={"2024-a": 0.4})
    with pytest.raises(ValueError, match=r"inside \[0, 2\]"):
        replace(
            evaluation,
            multiclass_brier_by_event={
                **evaluation.multiclass_brier_by_event,
                "2024-a": 2.01,
            },
        )


def test_model_evaluation_rejects_estimator_training_overlap_with_test_events() -> None:
    with pytest.raises(ValueError, match="estimator-training.*disjoint"):
        replace(
            _evaluation(challenger=False),
            estimator_training_event_ids=("2024-a",),
        )


def test_model_evaluation_rejects_calibration_overlap_with_test_events() -> None:
    with pytest.raises(ValueError, match="calibration.*disjoint"):
        replace(
            _evaluation(challenger=False),
            calibration_event_ids=("2025-b",),
        )


def test_model_evaluation_rejects_overlapping_training_and_calibration_lineage() -> None:
    with pytest.raises(ValueError, match="training and calibration.*disjoint"):
        replace(
            _evaluation(challenger=False),
            estimator_training_event_ids=("shared-lineage",),
            calibration_event_ids=("shared-lineage",),
        )


@pytest.mark.parametrize(
    ("field", "event_ids", "message"),
    [
        ("estimator_training_event_ids", (), "estimator-training.*nonempty"),
        ("calibration_event_ids", (), "calibration.*nonempty"),
        ("estimator_training_event_ids", ("train", "train"), "estimator-training.*unique"),
        ("calibration_event_ids", ("cal", "cal"), "calibration.*unique"),
    ],
)
def test_model_evaluation_requires_nonempty_unique_lineage_ids(
    field: str, event_ids: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_evaluation(challenger=False), **{field: event_ids})


def test_model_evaluation_rejects_untyped_origin_lane_and_basis_values() -> None:
    with pytest.raises(TypeError, match="Origin"):
        replace(_evaluation(challenger=False), origin="T72")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="model lane"):
        _evaluation(challenger=False, model_lane="market_comparator")
    with pytest.raises(ValueError, match="evidence basis"):
        _evaluation(challenger=False, evidence_basis="reconstructed")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bootstrap_replicates", 0),
        ("bootstrap_seed", -1),
        ("moving_block_weeks", 0),
        ("calibration_regression_max_iter", 0),
        ("log_loss_epsilon", 0.0),
        ("minimum_promotion_seasons", 0),
        ("minimum_systematic_staking_seasons", 0),
        ("minimum_common_games", 0),
        ("maximum_brier_degradation", -0.001),
        ("minimum_calibration_slope", 0.0),
    ],
)
def test_evaluation_policy_rejects_invalid_domains(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _policy(**{field: value})


def test_evaluation_policy_rejects_inverted_guardrail_bounds() -> None:
    with pytest.raises(ValidationError, match="intercept"):
        _policy(minimum_calibration_intercept=0.2, maximum_calibration_intercept=0.1)
    with pytest.raises(ValidationError, match="slope"):
        _policy(minimum_calibration_slope=1.2, maximum_calibration_slope=0.8)


def test_loaded_evaluation_policy_is_frozen_and_forbids_unknown_fields(tmp_path: Path) -> None:
    policy = load_evaluation_policy(ROOT / "configs/evaluation_policy_v1.toml")
    with pytest.raises(ValidationError):
        policy.bootstrap_replicates = 1  # type: ignore[misc]

    invalid = tmp_path / "evaluation.toml"
    invalid.write_text(
        (ROOT / "configs/evaluation_policy_v1.toml").read_text() + "unknown = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_evaluation_policy(invalid)
