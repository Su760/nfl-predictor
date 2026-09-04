from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from nfl_predictor.contracts.markets import DisplayedQuoteCandidate, MoneylineQuote
from nfl_predictor.contracts.outcomes import Outcome, Settlement

OperatorEventStatus = Literal["final", "postponed", "cancelled", "suspended", "unresolved"]
OperatorResolution = Literal["standard_confirmed", "void_confirmed", "conflict", "unresolved"]


@dataclass(frozen=True)
class OperatorSettlementEvidence:
    evidence_id: str
    quote_id: str
    canonical_event_id: str
    outcome_id: str
    outcome_version: int
    operator: str
    settlement_policy_url: str
    settlement_policy_version: str
    operator_event_status: OperatorEventStatus
    resolution: OperatorResolution
    observed_at_utc: datetime
    frozen_at_utc: datetime

    def __post_init__(self) -> None:
        identifiers = (
            self.evidence_id,
            self.quote_id,
            self.canonical_event_id,
            self.outcome_id,
            self.operator,
            self.settlement_policy_url,
            self.settlement_policy_version,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise ValueError("operator settlement evidence identifiers must be non-blank")
        if isinstance(self.outcome_version, bool) or not isinstance(self.outcome_version, int):
            raise TypeError("operator settlement outcome version must be an integer")
        if self.outcome_version < 1:
            raise ValueError("operator settlement outcome version must be positive")
        if self.operator_event_status not in {
            "final",
            "postponed",
            "cancelled",
            "suspended",
            "unresolved",
        }:
            raise ValueError("operator settlement event status is invalid")
        if self.resolution not in {
            "standard_confirmed",
            "void_confirmed",
            "conflict",
            "unresolved",
        }:
            raise ValueError("operator settlement resolution is invalid")
        observed = _require_utc_timestamp(self.observed_at_utc, "observed")
        frozen = _require_utc_timestamp(self.frozen_at_utc, "frozen")
        if frozen < observed:
            raise ValueError("operator settlement evidence cannot freeze before observation")


def settle_profit(
    side: str, outcome_result: str, decimal_price: Decimal
) -> tuple[Literal["win", "loss", "push", "void", "unresolved"], Decimal | None]:
    if decimal_price <= Decimal(1):
        raise ValueError("decimal price must be greater than one")
    if outcome_result == "void":
        return "void", Decimal(0)
    if outcome_result == "unresolved":
        return "unresolved", None
    if outcome_result == "tie":
        return "push", Decimal(0)
    if outcome_result == side:
        return "win", decimal_price - Decimal(1)
    if outcome_result in {"home", "away"} and side in {"home", "away"}:
        return "loss", Decimal(-1)
    raise ValueError("unsupported side or outcome result")


def settle_displayed_candidate(
    candidate: DisplayedQuoteCandidate,
    quote: MoneylineQuote,
    outcome: Outcome,
    operator_evidence: OperatorSettlementEvidence,
) -> Settlement:
    if not isinstance(operator_evidence, OperatorSettlementEvidence):
        raise TypeError("settlement requires frozen OperatorSettlementEvidence")
    if candidate.decision_status != "candidate" or candidate.reason_codes:
        raise ValueError("settlement requires an eligible displayed candidate")
    if candidate.quote_id != quote.quote_id:
        raise ValueError("candidate quote_id does not match linked quote")
    if quote.canonical_event_id != outcome.canonical_event_id:
        raise ValueError("quote and outcome event do not match")
    selections = [item for item in quote.selections if item.side == candidate.side]
    if len(selections) != 1 or selections[0].decimal_price != candidate.decimal_price:
        raise ValueError("candidate price does not match linked quote selection")
    if (
        operator_evidence.quote_id != quote.quote_id
        or operator_evidence.canonical_event_id != quote.canonical_event_id
        or operator_evidence.outcome_id != outcome.outcome_id
        or operator_evidence.outcome_version != outcome.outcome_version
        or operator_evidence.operator != quote.book_key
        or operator_evidence.settlement_policy_url != quote.settlement_policy_url
        or operator_evidence.settlement_policy_version != quote.settlement_policy_version
    ):
        raise ValueError("operator settlement evidence does not match quote and outcome")
    if operator_evidence.observed_at_utc < outcome.source_version_or_retrieved_at_utc:
        raise ValueError("operator settlement evidence predates the linked outcome version")
    settlement_id = sha256(
        (
            f"{candidate.candidate_id}|{outcome.outcome_id}|{outcome.outcome_version}|"
            f"{operator_evidence.evidence_id}"
        ).encode()
    ).hexdigest()
    if (
        outcome.game_status != "final"
        or operator_evidence.operator_event_status != "final"
        or operator_evidence.resolution in {"conflict", "unresolved"}
    ):
        result: Literal["win", "loss", "push", "void", "unresolved"] = "unresolved"
        profit = None
        settled_at = None
        manual_review = True
    elif operator_evidence.resolution == "void_confirmed":
        result = "void"
        profit = Decimal(0)
        settled_at = operator_evidence.frozen_at_utc
        manual_review = False
    else:
        result, profit = settle_profit(candidate.side, outcome.result, candidate.decimal_price)
        settled_at = outcome.finalized_at_utc
        manual_review = False
    return Settlement(
        settlement_id=settlement_id,
        settlement_version=1,
        canonical_event_id=quote.canonical_event_id,
        candidate_id=candidate.candidate_id,
        ticket_id=None,
        operator=quote.book_key,
        market_semantics_version=quote.market_semantics_version,
        settlement_policy_url=quote.settlement_policy_url,
        settlement_policy_version=quote.settlement_policy_version,
        result=result,
        profit_units=profit,
        settled_at_utc=settled_at,
        manual_review_required=manual_review,
    )


def _require_utc_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"operator settlement {name} timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"operator settlement {name} timestamp must use UTC")
    return value
