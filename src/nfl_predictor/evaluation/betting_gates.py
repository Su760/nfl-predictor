from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.evaluation.promotion import EvaluationPolicy


@dataclass(frozen=True)
class EvidenceLineage:
    canonical_event_id: str
    season: int
    provenance_grade: ProvenanceGrade
    origin: Origin
    candidate_policy_version: str
    market_policy_version: str
    evaluation_policy_version: str
    evidence_id: str
    ledger_id: str

    def __post_init__(self) -> None:
        identifiers = (
            self.canonical_event_id,
            self.candidate_policy_version,
            self.market_policy_version,
            self.evaluation_policy_version,
            self.evidence_id,
            self.ledger_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise ValueError("evidence lineage identifiers must be non-blank")
        if not isinstance(self.origin, Origin):
            raise TypeError("evidence lineage origin must be an Origin")
        if isinstance(self.season, bool) or not isinstance(self.season, int):
            raise TypeError("evidence lineage season must be an integer")
        if self.season < 1:
            raise ValueError("evidence lineage season must be positive")
        if not isinstance(self.provenance_grade, ProvenanceGrade):
            raise TypeError("evidence lineage grade must be a ProvenanceGrade")


@dataclass(frozen=True)
class BettingEvidence:
    lineage: tuple[EvidenceLineage, ...]
    complete_prospective_seasons: tuple[int, ...]
    full_2026_regular_season_complete: bool
    hypothetical_roi: float
    hypothetical_roi_lower_95: float | None
    zero_stake_bootstrap_replicates: int
    mean_observational_clv: float
    calibration_intercept: float
    calibration_slope: float
    qualifying_actual_ticket_count: int
    model_attributed_profit_amount: Decimal
    profit_currency: str | None

    def __post_init__(self) -> None:
        _validate_lineage(self.lineage, "betting")
        _validate_seasons(self.complete_prospective_seasons)
        _validate_declared_seasons(self.complete_prospective_seasons, self.lineage)
        if not isinstance(self.full_2026_regular_season_complete, bool):
            raise TypeError("season completion flag must be boolean")
        if (
            isinstance(self.zero_stake_bootstrap_replicates, bool)
            or not isinstance(self.zero_stake_bootstrap_replicates, int)
            or self.zero_stake_bootstrap_replicates < 0
        ):
            raise ValueError("zero-stake bootstrap count cannot be negative")
        if (
            isinstance(self.qualifying_actual_ticket_count, bool)
            or not isinstance(self.qualifying_actual_ticket_count, int)
            or self.qualifying_actual_ticket_count < 0
        ):
            raise ValueError("ticket count cannot be negative")
        metrics = (
            self.hypothetical_roi,
            self.mean_observational_clv,
            self.calibration_intercept,
            self.calibration_slope,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in metrics
        ):
            raise ValueError("betting evidence metrics must be finite")
        if self.hypothetical_roi_lower_95 is not None and (
            isinstance(self.hypothetical_roi_lower_95, bool)
            or not isinstance(self.hypothetical_roi_lower_95, (int, float))
            or not math.isfinite(float(self.hypothetical_roi_lower_95))
        ):
            raise ValueError("ROI lower bound must be a finite real number when defined")
        if (
            self.hypothetical_roi_lower_95 is not None
            and self.hypothetical_roi_lower_95 > self.hypothetical_roi
        ):
            raise ValueError("ROI lower bound cannot exceed point ROI")
        if not isinstance(self.model_attributed_profit_amount, Decimal):
            raise TypeError("model-attributed profit must be a Decimal")
        if not self.model_attributed_profit_amount.is_finite():
            raise ValueError("model-attributed profit must be finite")
        if (
            self.profit_currency is not None
            and re.fullmatch(r"[A-Z]{3}", self.profit_currency) is None
        ):
            raise ValueError("profit currency must be an uppercase ISO-style code")
        lower_is_undefined = self.hypothetical_roi_lower_95 is None
        if lower_is_undefined != (self.zero_stake_bootstrap_replicates > 0):
            raise ValueError("undefined ROI lower bound must match zero-stake replicates")


@dataclass(frozen=True)
class EvidenceDecision:
    eligible: bool
    authorizes_purchase: bool
    maximum_purchase_amount: Decimal
    purchase_currency: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MarketSignalEvidence:
    market_lineage: tuple[EvidenceLineage, ...]
    candidate_lineage: tuple[EvidenceLineage, ...]
    complete_prospective_seasons: tuple[int, ...]
    common_grade_a_games: int
    market_event_ids: tuple[str, ...]
    candidate_event_ids: tuple[str, ...]
    upper_95_conditional_log_loss_delta: float

    def __post_init__(self) -> None:
        _validate_lineage(self.market_lineage, "market")
        _validate_lineage(self.candidate_lineage, "candidate")
        _validate_seasons(self.complete_prospective_seasons)
        _validate_declared_seasons(self.complete_prospective_seasons, self.market_lineage)
        _validate_declared_seasons(self.complete_prospective_seasons, self.candidate_lineage)
        if (
            isinstance(self.common_grade_a_games, bool)
            or not isinstance(self.common_grade_a_games, int)
            or self.common_grade_a_games < 0
        ):
            raise ValueError("common Grade A game count must be a nonnegative integer")
        _validate_event_ids(self.market_event_ids, "market")
        _validate_event_ids(self.candidate_event_ids, "candidate")
        if {item.canonical_event_id for item in self.market_lineage} != set(self.market_event_ids):
            raise ValueError("market lineage must exactly align with market event IDs")
        if {item.canonical_event_id for item in self.candidate_lineage} != set(
            self.candidate_event_ids
        ):
            raise ValueError("candidate lineage must exactly align with candidate event IDs")
        common_count = len(set(self.market_event_ids) & set(self.candidate_event_ids))
        if self.common_grade_a_games != common_count:
            raise ValueError("common Grade A game count must equal the unique event intersection")
        delta = self.upper_95_conditional_log_loss_delta
        if (
            isinstance(delta, bool)
            or not isinstance(delta, (int, float))
            or not math.isfinite(float(delta))
        ):
            raise ValueError("conditional log-loss interval must be finite")


def _validate_seasons(seasons: tuple[int, ...]) -> None:
    if not isinstance(seasons, tuple):
        raise TypeError("prospective seasons must be an immutable tuple")
    if any(
        isinstance(season, bool) or not isinstance(season, int) or season < 1 for season in seasons
    ):
        raise ValueError("prospective seasons must contain positive integers")
    if len(set(seasons)) != len(seasons):
        raise ValueError("prospective seasons must be unique")


def _validate_event_ids(event_ids: tuple[str, ...], name: str) -> None:
    if not isinstance(event_ids, tuple):
        raise TypeError(f"{name} event IDs must be an immutable tuple")
    if any(not isinstance(event_id, str) or not event_id.strip() for event_id in event_ids):
        raise ValueError(f"{name} event IDs must be non-blank strings")
    if len(set(event_ids)) != len(event_ids):
        raise ValueError(f"{name} event IDs must be unique")


def _validate_lineage(lineage: tuple[EvidenceLineage, ...], name: str) -> None:
    if not isinstance(lineage, tuple):
        raise TypeError(f"{name} lineage must be an immutable tuple")
    if not lineage or any(not isinstance(item, EvidenceLineage) for item in lineage):
        raise TypeError(f"{name} lineage must contain EvidenceLineage records")
    event_ids = [item.canonical_event_id for item in lineage]
    evidence_ids = [item.evidence_id for item in lineage]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError(f"{name} lineage event IDs must be unique")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError(f"{name} lineage evidence IDs must be unique")


def _validate_declared_seasons(
    declared: tuple[int, ...], lineage: tuple[EvidenceLineage, ...]
) -> None:
    if set(declared) != {item.season for item in lineage}:
        raise ValueError("declared prospective seasons must equal lineage seasons")


def _append_lineage_reasons(
    reasons: list[str],
    policy: EvaluationPolicy,
    *lineages: tuple[EvidenceLineage, ...],
) -> None:
    combined = tuple(item for lineage in lineages for item in lineage)
    if len({item.origin for item in combined}) != 1:
        reasons.append("MIXED_ORIGIN_EVIDENCE")
    if len({item.candidate_policy_version for item in combined}) != 1:
        reasons.append("MIXED_CANDIDATE_POLICY_EVIDENCE")
    if len({item.market_policy_version for item in combined}) != 1:
        reasons.append("MIXED_MARKET_POLICY_EVIDENCE")
    if len({item.ledger_id for item in combined}) != 1:
        reasons.append("MIXED_LEDGER_EVIDENCE")
    if {item.evaluation_policy_version for item in combined} != {policy.policy_version}:
        reasons.append("EVALUATION_POLICY_VERSION_MISMATCH")
    if any(item.provenance_grade is not ProvenanceGrade.A for item in combined):
        reasons.append("NON_GRADE_A_EVIDENCE")


def calibration_inside_guardrails(evidence: BettingEvidence, policy: EvaluationPolicy) -> bool:
    return (
        policy.minimum_calibration_intercept
        <= evidence.calibration_intercept
        <= policy.maximum_calibration_intercept
        and policy.minimum_calibration_slope
        <= evidence.calibration_slope
        <= policy.maximum_calibration_slope
    )


def evaluate_systematic_staking(
    evidence: BettingEvidence, policy: EvaluationPolicy
) -> EvidenceDecision:
    reasons: list[str] = []
    _append_lineage_reasons(reasons, policy, evidence.lineage)
    if len(set(evidence.complete_prospective_seasons)) < policy.minimum_systematic_staking_seasons:
        reasons.append("FEWER_THAN_TWO_COMPLETE_PROSPECTIVE_SEASONS")
    if evidence.zero_stake_bootstrap_replicates:
        reasons.append("ZERO_STAKE_BOOTSTRAP_REPLICATE")
    if evidence.hypothetical_roi_lower_95 is None:
        reasons.append("ROI_LOWER_BOUND_UNDEFINED")
    elif evidence.hypothetical_roi_lower_95 <= 0:
        reasons.append("ROI_LOWER_BOUND_NOT_POSITIVE")
    if evidence.mean_observational_clv <= 0:
        reasons.append("MEAN_CLV_NOT_POSITIVE")
    if not calibration_inside_guardrails(evidence, policy):
        reasons.append("CALIBRATION_GUARDRAIL")
    return EvidenceDecision(not reasons, False, Decimal(0), None, tuple(reasons))


def evaluate_incremental_market_signal(
    evidence: MarketSignalEvidence, policy: EvaluationPolicy
) -> EvidenceDecision:
    reasons: list[str] = []
    _append_lineage_reasons(
        reasons,
        policy,
        evidence.market_lineage,
        evidence.candidate_lineage,
    )
    market_by_event = {item.canonical_event_id: item for item in evidence.market_lineage}
    candidate_by_event = {item.canonical_event_id: item for item in evidence.candidate_lineage}
    common_ids = set(market_by_event) & set(candidate_by_event)
    if any(
        (
            market_by_event[event_id].season,
            market_by_event[event_id].origin,
            market_by_event[event_id].candidate_policy_version,
            market_by_event[event_id].market_policy_version,
            market_by_event[event_id].evaluation_policy_version,
            market_by_event[event_id].ledger_id,
        )
        != (
            candidate_by_event[event_id].season,
            candidate_by_event[event_id].origin,
            candidate_by_event[event_id].candidate_policy_version,
            candidate_by_event[event_id].market_policy_version,
            candidate_by_event[event_id].evaluation_policy_version,
            candidate_by_event[event_id].ledger_id,
        )
        for event_id in common_ids
    ):
        reasons.append("MARKET_LINEAGE_MISMATCH")
    if len(set(evidence.complete_prospective_seasons)) < policy.minimum_promotion_seasons:
        reasons.append("FEWER_THAN_TWO_PROSPECTIVE_SEASONS")
    if evidence.common_grade_a_games < policy.minimum_common_games:
        reasons.append("INSUFFICIENT_COMMON_GRADE_A_GAMES")
    if not set(evidence.market_event_ids) <= set(evidence.candidate_event_ids):
        reasons.append("COVERAGE_GAP")
    if evidence.upper_95_conditional_log_loss_delta >= 0:
        reasons.append("CONDITIONAL_LOG_LOSS_INTERVAL_NOT_SUPERIOR")
    return EvidenceDecision(not reasons, False, Decimal(0), None, tuple(reasons))


def evaluate_paid_odds_review(
    evidence: BettingEvidence, policy: EvaluationPolicy
) -> EvidenceDecision:
    reasons: list[str] = []
    _append_lineage_reasons(reasons, policy, evidence.lineage)
    if not evidence.full_2026_regular_season_complete:
        reasons.append("2026_REGULAR_SEASON_INCOMPLETE")
    if evidence.hypothetical_roi <= 0:
        reasons.append("HYPOTHETICAL_ROI_NOT_POSITIVE")
    if evidence.mean_observational_clv <= 0:
        reasons.append("MEAN_CLV_NOT_POSITIVE")
    if not calibration_inside_guardrails(evidence, policy):
        reasons.append("CALIBRATION_GUARDRAIL")
    if evidence.qualifying_actual_ticket_count > 0 and evidence.model_attributed_profit_amount <= 0:
        reasons.append("MODEL_ATTRIBUTED_PROFIT_NOT_POSITIVE")
    if evidence.qualifying_actual_ticket_count > 0 and evidence.profit_currency is None:
        reasons.append("MODEL_ATTRIBUTED_PROFIT_CURRENCY_MISSING")
    if (
        evidence.qualifying_actual_ticket_count == 0
        and evidence.model_attributed_profit_amount != 0
    ):
        reasons.append("PROFIT_WITHOUT_QUALIFYING_TICKETS")
    cap = (
        evidence.model_attributed_profit_amount
        if evidence.qualifying_actual_ticket_count > 0
        and evidence.model_attributed_profit_amount > 0
        else Decimal(0)
    )
    currency = evidence.profit_currency if cap > 0 else None
    return EvidenceDecision(not reasons, False, cap, currency, tuple(reasons))
