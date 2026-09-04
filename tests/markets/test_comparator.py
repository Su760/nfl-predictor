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
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.markets import MoneylineQuote, MoneylineSelection
from nfl_predictor.markets.comparator import (
    build_market_comparator,
    median_market_home,
    no_vig_home_probability,
)

NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


def _contract(book: str) -> BookSettlementContract:
    return BookSettlementContract(
        book_key=book,
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url=f"https://{book}.test/rules",
        settlement_policy_version="v1",
        overtime_included=True,
        tie_handling="push",
        verified_on="2026-08-28",
    )


def _policy() -> OddsPolicy:
    return OddsPolicy(
        book_allowlist=("a", "b", "c"),
        book_settlement_contracts=(_contract("a"), _contract("b"), _contract("c")),
    )


def _quote(
    book: str,
    home: str,
    away: str,
    *,
    age_minutes: int = 1,
    capture_id: str = "capture-current",
    response_at: datetime = NOW,
    quote_suffix: str = "",
) -> MoneylineQuote:
    updated = response_at - timedelta(minutes=age_minutes)
    return MoneylineQuote(
        quote_id=f"quote-{book}{quote_suffix}",
        capture_id=capture_id,
        canonical_event_id="event-1",
        source_event_id="provider-1",
        book_key=book,
        book_name=book.upper(),
        market_key="h2h",
        period="full_game",
        provider_last_update_at_utc=updated,
        response_received_at_utc=response_at,
        capture_lag_seconds=1,
        provider_update_lag_seconds=age_minutes * 60,
        selections=(
            MoneylineSelection(
                side="home", team="CHI", decimal_price=Decimal(home), raw_pointer="$[0]"
            ),
            MoneylineSelection(
                side="away", team="GB", decimal_price=Decimal(away), raw_pointer="$[1]"
            ),
        ),
        overtime_included=True,
        tie_handling="push",
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url=f"https://{book}.test/rules",
        settlement_policy_version="v1",
        provenance_grade=ProvenanceGrade.A,
    )


def _prediction() -> Prediction:
    return Prediction(
        prediction_id="prediction-1",
        origin_run_id="origin-run-1",
        canonical_event_id="event-1",
        event_version=1,
        origin=Origin.T60,
        target_at_utc=NOW,
        decision_at_utc=NOW,
        model_lane="football_only",
        model_role="champion",
        model_artifact_id="model-1",
        calibrator_artifact_id=None,
        feature_snapshot_id="snapshot-1",
        market_snapshot_id=None,
        p_home=Decimal("0.50"),
        p_away=Decimal("0.48"),
        p_tie=Decimal("0.02"),
        predicted_winner="home",
        provenance_grade=ProvenanceGrade.A,
        status="COMPLETE",
        reason_codes=[],
        code_sha="1" * 40,
        policy_versions={"tie_prior": "tie-v1"},
        created_at_utc=NOW,
    )


def _event_state(*, status: str = "pregame", version: int = 1) -> OfficialEventState:
    event = EventVersion(
        canonical_event_id="event-1",
        event_version=version,
        source_event_ids={"the_odds_api": "provider-1"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=NOW + timedelta(hours=1),
        neutral_site=False,
        observed_at_utc=NOW - timedelta(days=1),
        available_at_utc=NOW - timedelta(days=1),
        captured_at_utc=NOW - timedelta(days=1),
        raw_payload_sha256="a" * 64,
    )
    return OfficialEventState(event, status)  # type: ignore[arg-type]


def _source_match() -> TrustedEventSourceMatch:
    policy = _policy()
    return policy.trusted_source_match(EventMatch(_event_state().event, "provider-1", None))


def test_two_way_same_book_no_vig_probability() -> None:
    result = no_vig_home_probability(Decimal("2.00"), Decimal("1.80"))
    assert float(result) == pytest.approx(0.4736842105263158)


def test_no_vig_rejects_non_positive_profit_prices() -> None:
    with pytest.raises(ValueError, match="greater than one"):
        no_vig_home_probability(Decimal(1), Decimal(2))


def test_median_is_per_book_not_a_mixed_side_consensus() -> None:
    quotes = (
        _quote("a", "2.0", "1.8"),
        _quote("b", "1.5", "3.0"),
        _quote("c", "4.0", "1.25"),
    )

    assert median_market_home(quotes) == Decimal("0.4736842105263157894736842103")


def test_comparator_filters_by_cutoff_and_origin_freshness_and_records_quote_ids() -> None:
    valid = _quote("a", "2.0", "1.8")
    stale = _quote("b", "1.5", "3.0", age_minutes=16)

    comparator = build_market_comparator(
        (valid, stale),
        _prediction(),
        tie_prior=Decimal("0.02"),
        policy=_policy(),
        market_comparator_id="comparator-1",
        event_state=_event_state(),
        source_match=_source_match(),
    )

    assert comparator.quote_ids == ["quote-a"]
    assert comparator.r_home_market == Decimal("0.4736842105263157894736842103")
    assert comparator.p_tie_shared_prior == Decimal("0.02")
    assert comparator.origin_run_id == "origin-run-1"


def test_comparator_refuses_to_construct_without_an_eligible_book() -> None:
    with pytest.raises(ValueError, match="eligible book"):
        build_market_comparator(
            (_quote("b", "1.5", "3.0", age_minutes=16),),
            _prediction(),
            tie_prior=Decimal("0.02"),
            policy=_policy(),
            market_comparator_id="comparator-1",
            event_state=_event_state(),
            source_match=_source_match(),
        )


def test_comparator_uses_only_newest_capture_inside_exact_origin_window() -> None:
    older = _quote(
        "a",
        "5.0",
        "1.1",
        capture_id="capture-old",
        response_at=NOW - timedelta(minutes=5),
        quote_suffix="-old",
    )
    newest = _quote(
        "a",
        "2.0",
        "1.8",
        capture_id="capture-new",
        response_at=NOW,
        quote_suffix="-new",
    )
    outside = _quote(
        "b",
        "1.2",
        "5.0",
        capture_id="capture-outside",
        response_at=NOW - timedelta(minutes=11),
    )

    comparator = build_market_comparator(
        (older, outside, newest),
        _prediction(),
        tie_prior=Decimal("0.02"),
        policy=_policy(),
        market_comparator_id="comparator-newest",
        event_state=_event_state(),
        source_match=_source_match(),
    )

    assert comparator.quote_ids == ["quote-a-new"]


def test_comparator_fails_closed_on_ambiguous_latest_capture() -> None:
    first = _quote("a", "2.0", "1.8", capture_id="capture-a", quote_suffix="-a")
    second = _quote("b", "1.9", "2.0", capture_id="capture-b", quote_suffix="-b")

    with pytest.raises(ValueError, match="ambiguous latest capture"):
        build_market_comparator(
            (first, second),
            _prediction(),
            tie_prior=Decimal("0.02"),
            policy=_policy(),
            market_comparator_id="comparator-ambiguous",
            event_state=_event_state(),
            source_match=_source_match(),
        )


def test_comparator_fails_closed_on_duplicate_book_inside_frozen_capture() -> None:
    first = _quote("a", "2.0", "1.8", quote_suffix="-1")
    duplicate = _quote("a", "2.1", "1.7", quote_suffix="-2")

    with pytest.raises(ValueError, match="duplicate book"):
        build_market_comparator(
            (first, duplicate),
            _prediction(),
            tie_prior=Decimal("0.02"),
            policy=_policy(),
            market_comparator_id="comparator-duplicate",
            event_state=_event_state(),
            source_match=_source_match(),
        )


def test_comparator_requires_grade_a_complete_exact_official_pregame_source_context() -> None:
    valid = build_market_comparator(
        (_quote("a", "2.0", "1.8"),),
        _prediction(),
        tie_prior=Decimal("0.02"),
        policy=_policy(),
        market_comparator_id="comparator-valid",
        event_state=_event_state(),
        source_match=_source_match(),
    )

    assert valid.provenance_grade is ProvenanceGrade.A
    invalid_inputs = (
        (_prediction().model_copy(update={"provenance_grade": ProvenanceGrade.B}), _event_state()),
        (_prediction().model_copy(update={"status": "DEGRADED"}), _event_state()),
        (_prediction(), _event_state(status="started")),
        (_prediction(), _event_state(version=2)),
    )
    for prediction, event_state in invalid_inputs:
        with pytest.raises(ValueError, match="Grade A comparator"):
            build_market_comparator(
                (_quote("a", "2.0", "1.8"),),
                prediction,
                tie_prior=Decimal("0.02"),
                policy=_policy(),
                market_comparator_id="comparator-rejected",
                event_state=event_state,
                source_match=_source_match(),
            )


def test_comparator_rejects_tie_prior_that_differs_from_prediction() -> None:
    with pytest.raises(ValueError, match="tie prior"):
        build_market_comparator(
            (_quote("a", "2.0", "1.8"),),
            _prediction(),
            tie_prior=Decimal("0.03"),
            policy=_policy(),
            market_comparator_id="comparator-tie-mismatch",
            event_state=_event_state(),
            source_match=_source_match(),
        )
