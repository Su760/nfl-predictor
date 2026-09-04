from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from statistics import median

from nfl_predictor.betting.policy import (
    OddsPolicy,
    OfficialEventState,
    TrustedEventSourceMatch,
)
from nfl_predictor.contracts.enums import PredictionStatus, ProvenanceGrade
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.markets import MarketComparator, MoneylineQuote


def no_vig_home_probability(home_price: Decimal, away_price: Decimal) -> Decimal:
    if home_price <= Decimal(1) or away_price <= Decimal(1):
        raise ValueError("decimal prices must be greater than one")
    q_home = Decimal(1) / home_price
    q_away = Decimal(1) / away_price
    return q_home / (q_home + q_away)


def selection_price(quote: MoneylineQuote, side: str) -> Decimal:
    matches = [item.decimal_price for item in quote.selections if item.side == side]
    if len(matches) != 1:
        raise ValueError(f"quote must contain exactly one {side} selection")
    return matches[0]


def median_market_home(quotes: Iterable[MoneylineQuote]) -> Decimal:
    probabilities = [
        no_vig_home_probability(selection_price(quote, "home"), selection_price(quote, "away"))
        for quote in quotes
    ]
    if not probabilities:
        raise ValueError("market comparator requires at least one eligible book")
    return Decimal(str(median(probabilities)))


def build_market_comparator(
    quotes: Iterable[MoneylineQuote],
    prediction: Prediction,
    *,
    tie_prior: Decimal,
    policy: OddsPolicy,
    market_comparator_id: str,
    event_state: OfficialEventState,
    source_match: TrustedEventSourceMatch,
) -> MarketComparator:
    event = event_state.event
    if (
        prediction.provenance_grade is not ProvenanceGrade.A
        or prediction.status is not PredictionStatus.COMPLETE
        or prediction.canonical_event_id != event.canonical_event_id
        or prediction.event_version != event.event_version
        or not event_state.is_pregame_at(prediction.decision_at_utc)
    ):
        raise ValueError("Grade A comparator requires a complete exact official pregame prediction")
    if tie_prior != prediction.p_tie:
        raise ValueError("market comparator tie prior must equal the prediction tie prior")
    selection = policy.select_frozen_capture(quotes, prediction)
    if selection.reason_code == "AMBIGUOUS_LATEST_CAPTURE":
        raise ValueError("market comparator has an ambiguous latest capture")
    if selection.reason_code == "DUPLICATE_BOOK_IN_CAPTURE":
        raise ValueError("market comparator has duplicate book ambiguity")
    if selection.reason_code == "INCONSISTENT_CAPTURE_TIMESTAMP":
        raise ValueError("market comparator capture timestamps are inconsistent")
    eligible = selection.quotes
    source_reason = policy.source_match_reason(source_match, event, eligible)
    if source_reason is not None:
        raise ValueError(f"market comparator source context rejected: {source_reason}")
    return MarketComparator(
        market_comparator_id=market_comparator_id,
        origin_run_id=prediction.origin_run_id,
        canonical_event_id=prediction.canonical_event_id,
        origin=prediction.origin,
        decision_at_utc=prediction.decision_at_utc,
        market_policy_version=policy.policy_version,
        quote_ids=[quote.quote_id for quote in eligible],
        r_home_market=median_market_home(eligible),
        p_tie_shared_prior=prediction.p_tie,
        provenance_grade=ProvenanceGrade.A,
    )
