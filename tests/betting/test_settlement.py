from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from nfl_predictor.betting.policy import (
    BookSettlementContract,
    EventMatch,
    OddsPolicy,
    OfficialEventState,
    TrustedEventSourceMatch,
)
from nfl_predictor.betting.settlement import (
    OperatorSettlementEvidence,
    settle_displayed_candidate,
    settle_profit,
)
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.markets import (
    DisplayedQuoteCandidate,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.contracts.outcomes import Outcome
from nfl_predictor.markets.close import same_book_clv

KICKOFF = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
DECISION = KICKOFF - timedelta(minutes=60)


def _quote(
    *,
    quote_id: str = "quote-decision",
    book: str = "book-a",
    event_id: str = "event-1",
    home_price: str = "2.00",
    response_at: datetime = DECISION,
    provider_update_at: datetime | None = None,
    source_event_id: str = "provider-1",
    settlement_version: str = "v1",
) -> MoneylineQuote:
    return MoneylineQuote(
        quote_id=quote_id,
        capture_id=f"capture-{quote_id}",
        canonical_event_id=event_id,
        source_event_id=source_event_id,
        book_key=book,
        book_name=book,
        market_key="h2h",
        period="full_game",
        provider_last_update_at_utc=provider_update_at or response_at - timedelta(seconds=30),
        response_received_at_utc=response_at,
        capture_lag_seconds=1,
        provider_update_lag_seconds=30,
        selections=(
            MoneylineSelection(
                side="home", team="CHI", decimal_price=Decimal(home_price), raw_pointer="$[0]"
            ),
            MoneylineSelection(
                side="away", team="GB", decimal_price=Decimal("1.90"), raw_pointer="$[1]"
            ),
        ),
        overtime_included=True,
        tie_handling="push",
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version=settlement_version,
        provenance_grade=ProvenanceGrade.A,
    )


def _candidate(
    *,
    quote_id: str = "quote-decision",
    decision_status: str = "candidate",
    reason_codes: list[str] | None = None,
) -> DisplayedQuoteCandidate:
    return DisplayedQuoteCandidate(
        candidate_id="candidate-1",
        prediction_id="prediction-1",
        quote_id=quote_id,
        origin=Origin.T60,
        side="home",
        decision_at_utc=DECISION,
        decimal_price=Decimal("2.00"),
        raw_p_win=Decimal("0.55"),
        buffered_p_win=Decimal("0.52"),
        p_loss=Decimal("0.43"),
        buffered_p_loss=Decimal("0.46"),
        p_push=Decimal("0.02"),
        displayed_ev_per_unit=Decimal("0.06"),
        quarter_kelly_fraction=Decimal("0.01530612244897959183673469388"),
        policy_version="candidate-v1",
        decision_status=decision_status,
        reason_codes=[] if reason_codes is None else reason_codes,
    )


def _outcome(
    result: str,
    *,
    event_id: str = "event-1",
    status: str = "final",
) -> Outcome:
    scores = {"home": (24, 17), "away": (17, 24), "tie": (20, 20)}
    home, away = scores.get(result, (None, None))
    return Outcome(
        outcome_id="outcome-1",
        outcome_version=1,
        canonical_event_id=event_id,
        event_version_at_final=1,
        game_status=status,
        home_score=home,
        away_score=away,
        result=result,
        finalized_at_utc=KICKOFF + timedelta(hours=4) if status == "final" else None,
        source="nflverse",
        source_version_or_retrieved_at_utc=KICKOFF + timedelta(hours=4),
        raw_payload_sha256="a" * 64,
    )


def _operator_evidence(**changes: object) -> OperatorSettlementEvidence:
    values: dict[str, object] = {
        "evidence_id": "operator-resolution-1",
        "quote_id": "quote-decision",
        "canonical_event_id": "event-1",
        "outcome_id": "outcome-1",
        "outcome_version": 1,
        "operator": "book-a",
        "settlement_policy_url": "https://example.test/rules",
        "settlement_policy_version": "v1",
        "operator_event_status": "final",
        "resolution": "standard_confirmed",
        "observed_at_utc": KICKOFF + timedelta(hours=4),
        "frozen_at_utc": KICKOFF + timedelta(hours=4, seconds=1),
    }
    values.update(changes)
    return OperatorSettlementEvidence(**values)  # type: ignore[arg-type]


def test_settlement_requires_matching_frozen_operator_resolution() -> None:
    settlement = settle_displayed_candidate(
        _candidate(), _quote(), _outcome("home"), _operator_evidence()
    )

    assert settlement.result == "win"
    assert settlement.profit_units == Decimal(1)


def test_verified_operator_void_and_rule_conflicts_do_not_use_score_default() -> None:
    void = settle_displayed_candidate(
        _candidate(),
        _quote(),
        _outcome("home"),
        _operator_evidence(resolution="void_confirmed"),
    )
    conflict = settle_displayed_candidate(
        _candidate(),
        _quote(),
        _outcome("home"),
        _operator_evidence(operator_event_status="postponed", resolution="conflict"),
    )

    assert (void.result, void.profit_units, void.manual_review_required) == (
        "void",
        Decimal(0),
        False,
    )
    assert (conflict.result, conflict.profit_units, conflict.manual_review_required) == (
        "unresolved",
        None,
        True,
    )


@pytest.mark.parametrize("status", ["postponed", "cancelled", "suspended", "unresolved"])
def test_every_nonfinal_state_remains_unresolved_with_operator_evidence(status: str) -> None:
    outcome_result = "void" if status in {"postponed", "cancelled"} else "unresolved"
    settlement = settle_displayed_candidate(
        _candidate(),
        _quote(),
        _outcome(outcome_result, status=status),
        _operator_evidence(operator_event_status=status, resolution="unresolved"),
    )

    assert settlement.result == "unresolved"
    assert settlement.manual_review_required is True


@pytest.mark.parametrize(
    "changes",
    [
        {"quote_id": "other"},
        {"operator": "other-book"},
        {"settlement_policy_version": "other-version"},
        {"outcome_version": 2},
    ],
)
def test_operator_settlement_evidence_identity_must_match_quote_and_outcome(changes) -> None:
    with pytest.raises(ValueError, match="operator settlement evidence"):
        settle_displayed_candidate(
            _candidate(), _quote(), _outcome("home"), _operator_evidence(**changes)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_id": ""},
        {"outcome_version": True},
        {"observed_at_utc": KICKOFF.replace(tzinfo=None)},
        {
            "observed_at_utc": KICKOFF + timedelta(hours=4),
            "frozen_at_utc": KICKOFF + timedelta(hours=3),
        },
        {"operator_event_status": "unknown"},
        {"resolution": "guessed"},
    ],
)
def test_operator_settlement_evidence_rejects_invalid_domains_and_timestamps(changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        _operator_evidence(**changes)


def test_settlement_rejects_untyped_operator_evidence() -> None:
    with pytest.raises(TypeError, match="OperatorSettlementEvidence"):
        settle_displayed_candidate(
            _candidate(),
            _quote(),
            _outcome("home"),
            object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("outcome_result", "result", "profit"),
    [("home", "win", Decimal(1)), ("away", "loss", Decimal(-1)), ("tie", "push", Decimal(0))],
)
def test_verified_final_settlement_uses_linked_quote_terms(
    outcome_result: str, result: str, profit: Decimal
) -> None:
    settlement = settle_displayed_candidate(
        _candidate(), _quote(), _outcome(outcome_result), _operator_evidence()
    )

    assert settlement.result == result
    assert settlement.profit_units == profit
    assert settlement.operator == "book-a"
    assert settlement.market_semantics_version == "nfl-h2h-v1"
    assert settlement.settlement_policy_version == "v1"


def test_void_and_unresolved_profit_shapes_are_pure_and_explicit() -> None:
    assert settle_profit("home", "void", Decimal(2)) == ("void", Decimal(0))
    assert settle_profit("home", "unresolved", Decimal(2)) == ("unresolved", None)


def test_unknown_outcome_cannot_be_silently_settled_as_a_loss() -> None:
    with pytest.raises(ValueError, match="outcome"):
        settle_profit("home", "abandoned", Decimal(2))


def test_non_final_operator_state_requires_manual_review_and_is_not_guessed() -> None:
    settlement = settle_displayed_candidate(
        _candidate(),
        _quote(),
        _outcome("void", status="cancelled"),
        _operator_evidence(operator_event_status="cancelled", resolution="unresolved"),
    )

    assert settlement.result == "unresolved"
    assert settlement.profit_units is None
    assert settlement.manual_review_required is True


def test_settlement_rejects_candidate_quote_or_event_integrity_mismatches() -> None:
    with pytest.raises(ValueError, match="quote_id"):
        settle_displayed_candidate(
            _candidate(quote_id="wrong"), _quote(), _outcome("home"), _operator_evidence()
        )


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(decision_status="rejected"),
        _candidate(reason_codes=["MANUAL_REJECTION"]),
    ],
)
def test_ineligible_displayed_candidate_cannot_emit_settlement_profit(candidate) -> None:
    with pytest.raises(ValueError, match="eligible displayed candidate"):
        settle_displayed_candidate(
            candidate,
            _quote(),
            _outcome("home"),
            _operator_evidence(),
        )
    with pytest.raises(ValueError, match="event"):
        settle_displayed_candidate(
            _candidate(),
            _quote(),
            _outcome("home", event_id="other"),
            _operator_evidence(canonical_event_id="other"),
        )


def _close_policy() -> OddsPolicy:
    contract = BookSettlementContract(
        book_key="book-a",
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="v1",
        overtime_included=True,
        tie_handling="push",
        verified_on="2026-08-28",
    )
    return OddsPolicy(book_allowlist=("book-a",), book_settlement_contracts=(contract,))


def _official_event(*, kickoff: datetime = KICKOFF, source_event_id: str = "provider-1"):
    event = EventVersion(
        canonical_event_id="event-1",
        event_version=1,
        source_event_ids={"the_odds_api": source_event_id},
        season=2026,
        season_type="REG",
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=kickoff,
        neutral_site=False,
        observed_at_utc=DECISION - timedelta(days=1),
        available_at_utc=DECISION - timedelta(days=1),
        captured_at_utc=DECISION - timedelta(days=1),
        raw_payload_sha256="b" * 64,
    )
    return OfficialEventState(event=event, status="pregame")


def _source_match(
    event_state: OfficialEventState | None = None,
    source_event_id: str = "provider-1",
) -> TrustedEventSourceMatch:
    chosen_event = event_state or _official_event()
    return _close_policy().trusted_source_match(
        EventMatch(chosen_event.event, source_event_id, None)
    )


def test_clv_requires_same_event_book_side_period_contract_and_close_window() -> None:
    candidate = _candidate()
    decision_quote = _quote()
    valid_close = _quote(
        quote_id="quote-close",
        home_price="1.90",
        response_at=KICKOFF - timedelta(minutes=5),
    )

    assert same_book_clv(
        candidate,
        decision_quote,
        valid_close,
        _official_event(),
        _close_policy(),
        _source_match(),
    ) == pytest.approx(0.05129329438755048)
    mismatches = (
        _quote(
            quote_id="event-close",
            event_id="other",
            home_price="1.90",
            response_at=KICKOFF - timedelta(minutes=5),
        ),
        _quote(
            quote_id="book-close",
            book="book-b",
            home_price="1.90",
            response_at=KICKOFF - timedelta(minutes=5),
        ),
        _quote(
            quote_id="contract-close",
            home_price="1.90",
            response_at=KICKOFF - timedelta(minutes=5),
            settlement_version="v2",
        ),
        _quote(
            quote_id="late-close",
            home_price="1.90",
            response_at=KICKOFF,
        ),
    )
    assert all(
        same_book_clv(
            candidate,
            decision_quote,
            quote,
            _official_event(),
            _close_policy(),
            _source_match(),
        )
        is None
        for quote in mismatches
    )


def test_clv_rejects_stale_provider_update_changed_source_and_flexed_kickoff() -> None:
    candidate = _candidate()
    decision = _quote()
    stale_update = _quote(
        quote_id="stale-update",
        home_price="1.90",
        response_at=KICKOFF - timedelta(minutes=5),
        provider_update_at=KICKOFF - timedelta(minutes=11),
    )
    changed_source = _quote(
        quote_id="changed-source",
        home_price="1.90",
        response_at=KICKOFF - timedelta(minutes=5),
        source_event_id="provider-flexed",
    )
    valid = _quote(
        quote_id="valid-close",
        home_price="1.90",
        response_at=KICKOFF - timedelta(minutes=5),
    )

    assert (
        same_book_clv(
            candidate,
            decision,
            stale_update,
            _official_event(),
            _close_policy(),
            _source_match(),
        )
        is None
    )
    assert (
        same_book_clv(
            candidate,
            decision,
            changed_source,
            _official_event(),
            _close_policy(),
            _source_match(),
        )
        is None
    )
    flexed_event = _official_event(kickoff=KICKOFF + timedelta(hours=1))
    assert (
        same_book_clv(
            candidate,
            decision,
            valid,
            flexed_event,
            _close_policy(),
            _source_match(flexed_event),
        )
        is None
    )


def test_aliasless_first_capture_source_match_supports_same_book_clv() -> None:
    established = _official_event()
    aliasless = OfficialEventState(
        established.event.model_copy(update={"source_event_ids": {"nflverse": "2026_01_GB_CHI"}}),
        "pregame",
    )
    source_match = _source_match(aliasless)
    close = _quote(
        quote_id="aliasless-close",
        home_price="1.90",
        response_at=KICKOFF - timedelta(minutes=5),
    )

    assert same_book_clv(
        _candidate(),
        _quote(),
        close,
        aliasless,
        _close_policy(),
        source_match,
    ) == pytest.approx(0.05129329438755048)


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(decision_status="rejected"),
        _candidate(reason_codes=["MANUAL_REJECTION"]),
    ],
)
def test_ineligible_displayed_candidate_cannot_emit_clv(candidate) -> None:
    close = _quote(
        quote_id="ineligible-close",
        home_price="1.90",
        response_at=KICKOFF - timedelta(minutes=5),
    )

    assert (
        same_book_clv(
            candidate,
            _quote(),
            close,
            _official_event(),
            _close_policy(),
            _source_match(),
        )
        is None
    )
