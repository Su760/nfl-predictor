from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import UtcModel


class Outcome(UtcModel):
    outcome_id: str
    outcome_version: int = Field(ge=1)
    canonical_event_id: str
    event_version_at_final: int
    game_status: Literal["final", "postponed", "cancelled", "suspended", "unresolved"]
    home_score: int | None
    away_score: int | None
    result: Literal["home", "away", "tie", "void", "unresolved"]
    finalized_at_utc: datetime | None
    source: str
    source_version_or_retrieved_at_utc: datetime
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_result_consistency(self) -> "Outcome":
        if self.game_status == "final":
            if self.home_score is None or self.away_score is None or self.finalized_at_utc is None:
                raise ValueError("final outcome requires scores and finalization time")
            expected = (
                "home"
                if self.home_score > self.away_score
                else "away"
                if self.away_score > self.home_score
                else "tie"
            )
            if self.result != expected:
                raise ValueError("final result conflicts with score")
        elif self.result not in {"void", "unresolved"}:
            raise ValueError("non-final outcome cannot declare a winner")
        return self


class Settlement(UtcModel):
    settlement_id: str
    settlement_version: int = Field(ge=1)
    supersedes_settlement_id: str | None = None
    canonical_event_id: str
    candidate_id: str | None
    ticket_id: str | None
    operator: str
    market_semantics_version: str
    settlement_policy_url: str
    settlement_policy_version: str
    result: Literal["win", "loss", "push", "void", "unresolved"]
    profit_units: Decimal | None
    profit_amount: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    settled_at_utc: datetime | None
    manual_review_required: bool

    @model_validator(mode="after")
    def require_link_and_profit_shape(self) -> "Settlement":
        if (self.settlement_version == 1) != (self.supersedes_settlement_id is None):
            raise ValueError("only settlement corrections require a superseded settlement")
        if (self.candidate_id is None) == (self.ticket_id is None):
            raise ValueError("settlement must link exactly one candidate or ticket")
        if self.result == "unresolved":
            if (
                self.profit_units is not None
                or self.profit_amount is not None
                or self.settled_at_utc is not None
            ):
                raise ValueError("unresolved settlement cannot have profit/final time")
        elif self.settled_at_utc is None:
            raise ValueError("resolved settlement requires final time")
        if self.result != "unresolved" and self.candidate_id is not None and (
            self.profit_units is None
            or self.profit_amount is not None
            or self.currency is not None
        ):
            raise ValueError("displayed candidate settlement uses units only")
        if self.result != "unresolved" and self.ticket_id is not None and (
            self.profit_amount is None or self.currency is None
        ):
            raise ValueError("actual ticket settlement requires amount and currency")
        if self.result in {"push", "void"} and self.profit_units not in {None, Decimal(0)}:
            raise ValueError("push/void profit must be zero")
        if self.result in {"push", "void"} and self.profit_amount not in {None, Decimal(0)}:
            raise ValueError("push/void profit amount must be zero")
        return self
