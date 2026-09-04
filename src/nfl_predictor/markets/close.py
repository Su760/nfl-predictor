from __future__ import annotations

from datetime import timedelta

from nfl_predictor.betting.math import same_book_clv as price_clv
from nfl_predictor.betting.policy import (
    OddsPolicy,
    OfficialEventState,
    TrustedEventSourceMatch,
)
from nfl_predictor.contracts.markets import DisplayedQuoteCandidate, MoneylineQuote


def same_book_clv(
    candidate: DisplayedQuoteCandidate,
    decision_quote: MoneylineQuote,
    close_quote: MoneylineQuote,
    event_state: OfficialEventState,
    policy: OddsPolicy,
    source_match: TrustedEventSourceMatch,
) -> float | None:
    if candidate.decision_status != "candidate" or candidate.reason_codes:
        return None
    event = event_state.event
    if event_state.status != "pregame":
        return None
    if (
        policy.source_match_reason(
            source_match,
            event,
            (decision_quote, close_quote),
        )
        is not None
    ):
        return None
    source_event_id = source_match.source_event_id
    if candidate.quote_id != decision_quote.quote_id:
        return None
    decision_selection = [item for item in decision_quote.selections if item.side == candidate.side]
    close_selection = [item for item in close_quote.selections if item.side == candidate.side]
    if len(decision_selection) != 1 or len(close_selection) != 1:
        return None
    same_contract = (
        decision_quote.canonical_event_id == event.canonical_event_id
        and decision_quote.canonical_event_id == close_quote.canonical_event_id
        and decision_quote.source_event_id == source_event_id
        and close_quote.source_event_id == source_event_id
        and decision_quote.book_key == close_quote.book_key
        and decision_selection[0].team == close_selection[0].team
        and decision_quote.market_key == close_quote.market_key
        and decision_quote.period == close_quote.period
        and decision_quote.market_semantics_version == close_quote.market_semantics_version
        and decision_quote.settlement_policy_url == close_quote.settlement_policy_url
        and decision_quote.settlement_policy_version == close_quote.settlement_policy_version
        and decision_quote.overtime_included is close_quote.overtime_included
        and decision_quote.tie_handling == close_quote.tie_handling
    )
    window_open = event.kickoff_at_utc - timedelta(minutes=policy.close_window_opens_minutes)
    window_close = event.kickoff_at_utc - timedelta(minutes=policy.close_window_closes_minutes)
    if (
        not same_contract
        or close_quote.canonical_event_id != decision_quote.canonical_event_id
        or not window_open <= close_quote.response_received_at_utc <= window_close
        or not window_open <= close_quote.provider_last_update_at_utc <= window_close
        or not policy.eligibility(
            close_quote,
            origin=candidate.origin,
            decision_at=close_quote.response_received_at_utc,
        ).eligible
    ):
        return None
    if decision_selection[0].decimal_price != candidate.decimal_price:
        return None
    return price_clv(candidate.decimal_price, close_selection[0].decimal_price)
