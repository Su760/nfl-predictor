from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import UtcModel
from .enums import Origin, ProvenanceGrade


class MoneylineSelection(UtcModel):
    side: Literal["home", "away"]
    team: str
    decimal_price: Decimal = Field(gt=1)
    source_outcome_id: str | None = None
    raw_pointer: str


class MoneylineQuote(UtcModel):
    quote_id: str
    capture_id: str
    canonical_event_id: str
    source_event_id: str
    book_key: str
    book_name: str
    market_key: Literal["h2h"]
    period: Literal["full_game"]
    provider_last_update_at_utc: datetime
    response_received_at_utc: datetime
    capture_lag_seconds: int = Field(ge=0)
    provider_update_lag_seconds: int = Field(ge=0)
    selections: tuple[MoneylineSelection, MoneylineSelection]
    overtime_included: Literal[True]
    tie_handling: Literal["push"]
    market_semantics_version: str
    settlement_policy_url: str
    settlement_policy_version: str
    provenance_grade: ProvenanceGrade

    @model_validator(mode="after")
    def require_binary_nfl_contract(self) -> "MoneylineQuote":
        if {item.side for item in self.selections} != {"home", "away"}:
            raise ValueError("V1 NFL h2h requires exactly home and away")
        return self


class MarketComparator(UtcModel):
    market_comparator_id: str
    origin_run_id: str
    canonical_event_id: str
    origin: Origin
    decision_at_utc: datetime
    market_policy_version: str
    quote_ids: list[str]
    r_home_market: Decimal = Field(ge=0, le=1)
    p_tie_shared_prior: Decimal = Field(ge=0, le=1)
    provenance_grade: Literal[ProvenanceGrade.A]


class DisplayedQuoteCandidate(UtcModel):
    candidate_id: str
    prediction_id: str
    quote_id: str
    origin: Origin
    side: Literal["home", "away"]
    decision_at_utc: datetime
    decimal_price: Decimal = Field(gt=1)
    raw_p_win: Decimal = Field(ge=0, le=1)
    buffered_p_win: Decimal = Field(ge=0, le=1)
    p_loss: Decimal = Field(ge=0, le=1)
    buffered_p_loss: Decimal = Field(ge=0, le=1)
    p_push: Decimal = Field(ge=0, le=1)
    displayed_ev_per_unit: Decimal
    fixed_stake_units: Decimal = Field(default=Decimal(1), ge=1, le=1)
    quarter_kelly_fraction: Decimal = Field(ge=0)
    policy_version: str
    decision_status: Literal["candidate", "rejected"]
    reason_codes: list[str]

    @model_validator(mode="after")
    def require_probability_partition(self) -> "DisplayedQuoteCandidate":
        if self.raw_p_win + self.p_loss + self.p_push != Decimal(1):
            raise ValueError("raw win/loss/push probabilities must sum to 1")
        if self.buffered_p_win + self.buffered_p_loss + self.p_push != Decimal(1):
            raise ValueError("buffered win/loss/push probabilities must sum to 1")
        if self.buffered_p_win > self.raw_p_win or self.buffered_p_loss < self.p_loss:
            raise ValueError("calibration buffer must move win mass to loss")
        expected_ev = self.buffered_p_win * (self.decimal_price - Decimal(1)) - self.buffered_p_loss
        if abs(self.displayed_ev_per_unit - expected_ev) > Decimal("0.000000000001"):
            raise ValueError("displayed EV does not match buffered probabilities")
        denominator = (self.decimal_price - Decimal(1)) * (
            self.buffered_p_win + self.buffered_p_loss
        )
        expected_kelly = (
            Decimal(0)
            if denominator <= 0
            else Decimal("0.25") * max(Decimal(0), expected_ev / denominator)
        )
        if abs(self.quarter_kelly_fraction - expected_kelly) > Decimal("0.000000000001"):
            raise ValueError("quarter Kelly does not match buffered probabilities")
        return self


class ActualTicket(UtcModel):
    ticket_id: str
    candidate_id: str | None
    model_attributed: bool
    operator: str
    jurisdiction: str
    accepted_at_utc: datetime
    accepted_decimal_price: Decimal = Field(gt=1)
    stake: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    settlement_id: str | None = None

    @model_validator(mode="after")
    def require_attribution_link(self) -> "ActualTicket":
        if self.model_attributed != (self.candidate_id is not None):
            raise ValueError("model attribution requires exactly one candidate link")
        return self
