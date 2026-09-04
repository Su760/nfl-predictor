from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.evaluation.betting_gates import (
    BettingEvidence,
    EvidenceLineage,
    MarketSignalEvidence,
    evaluate_incremental_market_signal,
    evaluate_paid_odds_review,
    evaluate_systematic_staking,
)
from nfl_predictor.evaluation.promotion import load_evaluation_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_evaluation_policy(ROOT / "configs" / "evaluation_policy_v1.toml")


def _lineage(
    event_ids: tuple[str, ...],
    *,
    evidence_prefix: str,
    origin: Origin = Origin.T60,
    candidate_policy_version: str = "candidate-v1",
    market_policy_version: str = "odds-v1",
    evaluation_policy_version: str = POLICY.policy_version,
    ledger_id: str = "ledger-v1",
    seasons: tuple[int, ...] = (2025, 2026),
) -> tuple[EvidenceLineage, ...]:
    return tuple(
        EvidenceLineage(
            canonical_event_id=event_id,
            season=seasons[index % len(seasons)],
            provenance_grade=ProvenanceGrade.A,
            origin=origin,
            candidate_policy_version=candidate_policy_version,
            market_policy_version=market_policy_version,
            evaluation_policy_version=evaluation_policy_version,
            evidence_id=f"{evidence_prefix}-{index}",
            ledger_id=ledger_id,
        )
        for index, event_id in enumerate(event_ids)
    )


def _evidence(**changes: object) -> BettingEvidence:
    values: dict[str, object] = {
        "complete_prospective_seasons": (2025, 2026),
        "full_2026_regular_season_complete": True,
        "hypothetical_roi": 0.08,
        "hypothetical_roi_lower_95": 0.01,
        "zero_stake_bootstrap_replicates": 0,
        "mean_observational_clv": 0.02,
        "calibration_intercept": 0.0,
        "calibration_slope": 1.0,
        "qualifying_actual_ticket_count": 10,
        "model_attributed_profit_amount": Decimal("125.00"),
        "profit_currency": "USD",
    }
    values.update(changes)
    declared_seasons = values["complete_prospective_seasons"]
    if isinstance(declared_seasons, tuple) and declared_seasons:
        values.setdefault(
            "lineage",
            _lineage(
                tuple(f"event-{index}" for index in range(1, len(declared_seasons) + 1)),
                evidence_prefix="betting-evidence",
                seasons=declared_seasons,  # type: ignore[arg-type]
            ),
        )
    return BettingEvidence(**values)  # type: ignore[arg-type]


def _market(**changes: object) -> MarketSignalEvidence:
    event_ids = tuple(f"event-{index}" for index in range(1, POLICY.minimum_common_games + 1))
    values: dict[str, object] = {
        "complete_prospective_seasons": (2025, 2026),
        "common_grade_a_games": POLICY.minimum_common_games,
        "market_event_ids": event_ids,
        "candidate_event_ids": event_ids,
        "upper_95_conditional_log_loss_delta": -0.01,
    }
    values.update(changes)
    declared_seasons = values["complete_prospective_seasons"]
    lineage_seasons = (
        declared_seasons
        if isinstance(declared_seasons, tuple) and declared_seasons
        else (2025, 2026)
    )
    market_event_ids = values["market_event_ids"]
    candidate_event_ids = values["candidate_event_ids"]
    if isinstance(market_event_ids, tuple):
        values.setdefault(
            "market_lineage",
            _lineage(
                market_event_ids,
                evidence_prefix="market-evidence",
                seasons=lineage_seasons,  # type: ignore[arg-type]
            ),
        )
    if isinstance(candidate_event_ids, tuple):
        values.setdefault(
            "candidate_lineage",
            _lineage(
                candidate_event_ids,
                evidence_prefix="candidate-evidence",
                seasons=lineage_seasons,  # type: ignore[arg-type]
            ),
        )
    return MarketSignalEvidence(**values)  # type: ignore[arg-type]


def test_paid_data_cap_is_zero_without_realized_model_profit() -> None:
    evidence = _evidence(
        qualifying_actual_ticket_count=0,
        model_attributed_profit_amount=Decimal(0),
        profit_currency=None,
    )

    decision = evaluate_paid_odds_review(evidence, POLICY)

    assert decision.maximum_purchase_amount == 0
    assert decision.authorizes_purchase is False


def test_paid_review_is_non_mutating_and_cap_never_exceeds_realized_profit() -> None:
    evidence = _evidence(model_attributed_profit_amount=Decimal("25.50"))
    decision = evaluate_paid_odds_review(evidence, POLICY)

    assert decision.eligible is True
    assert decision.authorizes_purchase is False
    assert decision.maximum_purchase_amount == Decimal("25.50")
    assert decision.purchase_currency == "USD"


def test_early_positive_streak_cannot_enable_systematic_staking() -> None:
    decision = evaluate_systematic_staking(_evidence(complete_prospective_seasons=(2026,)), POLICY)
    assert decision.eligible is False
    assert "FEWER_THAN_TWO_COMPLETE_PROSPECTIVE_SEASONS" in decision.reason_codes


def test_undefined_roi_lower_bound_from_any_zero_stake_replicate_blocks_staking() -> None:
    decision = evaluate_systematic_staking(
        _evidence(hypothetical_roi_lower_95=None, zero_stake_bootstrap_replicates=1), POLICY
    )

    assert decision.eligible is False
    assert "ZERO_STAKE_BOOTSTRAP_REPLICATE" in decision.reason_codes
    assert "ROI_LOWER_BOUND_UNDEFINED" in decision.reason_codes


def test_nonpositive_roi_clv_and_calibration_each_block_systematic_staking() -> None:
    decision = evaluate_systematic_staking(
        _evidence(
            hypothetical_roi_lower_95=0.0,
            mean_observational_clv=0.0,
            calibration_slope=0.1,
        ),
        POLICY,
    )
    assert set(decision.reason_codes) == {
        "ROI_LOWER_BOUND_NOT_POSITIVE",
        "MEAN_CLV_NOT_POSITIVE",
        "CALIBRATION_GUARDRAIL",
    }


def test_market_signal_claim_requires_two_prospective_seasons() -> None:
    decision = evaluate_incremental_market_signal(
        _market(complete_prospective_seasons=(2026,)), POLICY
    )
    assert decision.eligible is False
    assert "FEWER_THAN_TWO_PROSPECTIVE_SEASONS" in decision.reason_codes


def test_market_signal_requires_common_coverage_and_superior_interval() -> None:
    decision = evaluate_incremental_market_signal(
        _market(
            common_grade_a_games=1,
            market_event_ids=("event-1", "missing"),
            upper_95_conditional_log_loss_delta=0.0,
        ),
        POLICY,
    )
    assert set(decision.reason_codes) == {
        "INSUFFICIENT_COMMON_GRADE_A_GAMES",
        "COVERAGE_GAP",
        "CONDITIONAL_LOG_LOSS_INTERVAL_NOT_SUPERIOR",
    }


def test_paid_review_rejects_incomplete_or_unprofitable_evidence_and_bad_ticket_shape() -> None:
    decision = evaluate_paid_odds_review(
        _evidence(
            full_2026_regular_season_complete=False,
            hypothetical_roi=0.0,
            hypothetical_roi_lower_95=0.0,
            mean_observational_clv=0.0,
            calibration_intercept=10.0,
            qualifying_actual_ticket_count=1,
            model_attributed_profit_amount=Decimal(0),
            profit_currency=None,
        ),
        POLICY,
    )
    assert set(decision.reason_codes) == {
        "2026_REGULAR_SEASON_INCOMPLETE",
        "HYPOTHETICAL_ROI_NOT_POSITIVE",
        "MEAN_CLV_NOT_POSITIVE",
        "CALIBRATION_GUARDRAIL",
        "MODEL_ATTRIBUTED_PROFIT_NOT_POSITIVE",
        "MODEL_ATTRIBUTED_PROFIT_CURRENCY_MISSING",
    }
    assert decision.maximum_purchase_amount == 0


def test_profit_without_qualifying_tickets_is_not_a_purchase_cap() -> None:
    decision = evaluate_paid_odds_review(
        _evidence(
            qualifying_actual_ticket_count=0,
            model_attributed_profit_amount=Decimal(10),
            profit_currency="USD",
        ),
        POLICY,
    )
    assert "PROFIT_WITHOUT_QUALIFYING_TICKETS" in decision.reason_codes
    assert decision.maximum_purchase_amount == 0
    assert decision.purchase_currency is None


@pytest.mark.parametrize(
    "changes",
    [
        {"hypothetical_roi": float("nan")},
        {"mean_observational_clv": float("inf")},
        {"model_attributed_profit_amount": Decimal("NaN")},
        {"zero_stake_bootstrap_replicates": 1, "hypothetical_roi_lower_95": 0.01},
        {"zero_stake_bootstrap_replicates": 0, "hypothetical_roi_lower_95": None},
    ],
)
def test_betting_evidence_rejects_nonfinite_or_contradictory_values(changes) -> None:
    with pytest.raises(ValueError):
        _evidence(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"complete_prospective_seasons": (2025, 2025)},
        {"complete_prospective_seasons": (True, 2026)},
        {"complete_prospective_seasons": (0, 2026)},
        {"full_2026_regular_season_complete": 1},
        {"zero_stake_bootstrap_replicates": True},
        {"qualifying_actual_ticket_count": -1},
        {"qualifying_actual_ticket_count": True},
        {"model_attributed_profit_amount": 1.0},
        {"profit_currency": "usd"},
        {"calibration_slope": True},
    ],
)
def test_betting_evidence_rejects_invalid_types_domains_and_profit_shape(changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        _evidence(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"complete_prospective_seasons": (2025, 2025)},
        {"complete_prospective_seasons": (True, 2026)},
        {"complete_prospective_seasons": (0, 2026)},
        {"common_grade_a_games": True},
        {"common_grade_a_games": -1},
        {"market_event_ids": ("", "event-2")},
        {"market_event_ids": ("event-1", "event-1")},
        {"candidate_event_ids": ("event-1", " ")},
        {"upper_95_conditional_log_loss_delta": float("nan")},
        {"upper_95_conditional_log_loss_delta": float("inf")},
    ],
)
def test_market_signal_evidence_rejects_invalid_types_domains_ids_and_floats(changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        _market(**changes)


def test_zero_counts_and_negative_realized_profit_remain_valid_gate_evidence() -> None:
    evidence = _evidence(
        qualifying_actual_ticket_count=0,
        model_attributed_profit_amount=Decimal(-1),
        profit_currency="USD",
    )
    decision = evaluate_paid_odds_review(evidence, POLICY)
    assert "PROFIT_WITHOUT_QUALIFYING_TICKETS" in decision.reason_codes


def test_market_common_count_must_equal_unique_event_intersection() -> None:
    with pytest.raises(ValueError, match="intersection"):
        _market(
            common_grade_a_games=POLICY.minimum_common_games,
            market_event_ids=("event-1", "event-2"),
            candidate_event_ids=("event-1", "event-2"),
        )


def test_betting_gate_rejects_mixed_origin_candidate_policy_and_evaluation_lineage() -> None:
    lineage = (
        EvidenceLineage(
            canonical_event_id="event-1",
            season=2025,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v1",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="evidence-1",
            ledger_id="ledger-1",
        ),
        EvidenceLineage(
            canonical_event_id="event-2",
            season=2026,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T72,
            candidate_policy_version="candidate-v2",
            market_policy_version="odds-v1",
            evaluation_policy_version="evaluation-other",
            evidence_id="evidence-2",
            ledger_id="ledger-1",
        ),
    )

    decision = evaluate_systematic_staking(_evidence(lineage=lineage), POLICY)

    assert {
        "MIXED_ORIGIN_EVIDENCE",
        "MIXED_CANDIDATE_POLICY_EVIDENCE",
        "EVALUATION_POLICY_VERSION_MISMATCH",
    } <= set(decision.reason_codes)


def test_market_gate_rejects_same_origin_market_and_ledger_lineage_mismatch() -> None:
    market_lineage = (
        EvidenceLineage(
            canonical_event_id="event-1",
            season=2025,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v1",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="market-evidence-1",
            ledger_id="ledger-market",
        ),
    )
    candidate_lineage = (
        EvidenceLineage(
            canonical_event_id="event-1",
            season=2025,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v2",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="candidate-evidence-1",
            ledger_id="ledger-candidate",
        ),
    )

    decision = evaluate_incremental_market_signal(
        _market(
            complete_prospective_seasons=(2025,),
            common_grade_a_games=1,
            market_event_ids=("event-1",),
            candidate_event_ids=("event-1",),
            market_lineage=market_lineage,
            candidate_lineage=candidate_lineage,
        ),
        POLICY,
    )

    assert "MARKET_LINEAGE_MISMATCH" in decision.reason_codes


def test_market_gate_rejects_event_level_season_swap_with_equal_aggregate_sets() -> None:
    event_ids = ("event-1", "event-2")
    market_lineage = _lineage(event_ids, evidence_prefix="market-evidence")
    candidate_lineage = (
        EvidenceLineage(
            canonical_event_id="event-1",
            season=2026,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v1",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="candidate-evidence-1",
            ledger_id="ledger-v1",
        ),
        EvidenceLineage(
            canonical_event_id="event-2",
            season=2025,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v1",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="candidate-evidence-2",
            ledger_id="ledger-v1",
        ),
    )

    decision = evaluate_incremental_market_signal(
        _market(
            common_grade_a_games=2,
            market_event_ids=event_ids,
            candidate_event_ids=event_ids,
            market_lineage=market_lineage,
            candidate_lineage=candidate_lineage,
        ),
        POLICY,
    )

    assert "MARKET_LINEAGE_MISMATCH" in decision.reason_codes


def test_roi_lower_bound_cannot_exceed_point_roi() -> None:
    with pytest.raises(ValueError, match="lower bound"):
        _evidence(hypothetical_roi=0.05, hypothetical_roi_lower_95=0.06)


def test_gate_lineage_rejects_non_grade_a_mixed_market_policy_and_ledger() -> None:
    lineage = (
        EvidenceLineage(
            canonical_event_id="event-1",
            season=2025,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v1",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="evidence-1",
            ledger_id="ledger-1",
        ),
        EvidenceLineage(
            canonical_event_id="event-2",
            season=2026,
            provenance_grade=ProvenanceGrade.B,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v2",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="evidence-2",
            ledger_id="ledger-2",
        ),
    )

    decision = evaluate_systematic_staking(_evidence(lineage=lineage), POLICY)

    assert {
        "NON_GRADE_A_EVIDENCE",
        "MIXED_MARKET_POLICY_EVIDENCE",
        "MIXED_LEDGER_EVIDENCE",
    } <= set(decision.reason_codes)


def test_declared_prospective_seasons_must_equal_lineage_seasons() -> None:
    one_season = (
        EvidenceLineage(
            canonical_event_id="event-1",
            season=2026,
            provenance_grade=ProvenanceGrade.A,
            origin=Origin.T60,
            candidate_policy_version="candidate-v1",
            market_policy_version="odds-v1",
            evaluation_policy_version=POLICY.policy_version,
            evidence_id="evidence-1",
            ledger_id="ledger-1",
        ),
    )

    with pytest.raises(ValueError, match="seasons"):
        _evidence(complete_prospective_seasons=(2025, 2026), lineage=one_season)
