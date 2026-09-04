from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from nfl_predictor.betting.policy import (
    BookSettlementContract,
    CalibrationBinEvidence,
    CandidateContext,
    CandidatePolicy,
    InMemoryDecisionRepository,
    OddsPolicy,
)
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.markets import (
    DisplayedQuoteCandidate,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.runtime.markets import DurableCalibrationBins, ProductionMarketLayer

NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


def _contract(book: str) -> BookSettlementContract:
    return BookSettlementContract(
        book_key=book, market_semantics_version="nfl-h2h-v1",
        settlement_policy_url=f"https://{book}.test/rules", settlement_policy_version="v1",
        overtime_included=True, tie_handling="push", verified_on="2026-09-01",
    )


def _policy() -> OddsPolicy:
    return OddsPolicy(
        book_allowlist=("a", "b"), book_settlement_contracts=(_contract("a"), _contract("b")),
    )


def _event() -> EventVersion:
    return EventVersion(
        canonical_event_id="event-v1", event_version=1, source_event_ids={"the_odds_api": "provider-v1"},
        season=2026, season_type="REG", week=1, home_team="CHI", away_team="GB",
        kickoff_at_utc=NOW + timedelta(hours=1), neutral_site=False,
        observed_at_utc=NOW - timedelta(days=1), available_at_utc=NOW - timedelta(days=1),
        captured_at_utc=NOW - timedelta(days=1), raw_payload_sha256="a" * 64,
    )


def _prediction() -> Prediction:
    return Prediction(
        prediction_id="prediction-v1", origin_run_id="run-v1", canonical_event_id="event-v1",
        event_version=1, origin=Origin.T60, target_at_utc=NOW, decision_at_utc=NOW,
        model_lane="football_only", model_role="champion", model_artifact_id="champion-v1",
        calibrator_artifact_id="calibrator-v1", feature_snapshot_id="snapshot-v1", market_snapshot_id=None,
        p_home=Decimal("0.55"), p_away=Decimal("0.43"), p_tie=Decimal("0.02"),
        predicted_winner="home", provenance_grade=ProvenanceGrade.A, status="COMPLETE", reason_codes=[],
        code_sha="c" * 40, policy_versions={"model": "model-v1", "forecast": "forecast-v1"}, created_at_utc=NOW,
    )


def _quote(book: str, home: str, away: str) -> MoneylineQuote:
    return MoneylineQuote(
        quote_id=f"quote-{book}", capture_id="capture-v1", canonical_event_id="event-v1",
        source_event_id="provider-v1", book_key=book, book_name=book, market_key="h2h", period="full_game",
        provider_last_update_at_utc=NOW - timedelta(minutes=1), response_received_at_utc=NOW,
        capture_lag_seconds=1, provider_update_lag_seconds=60,
        selections=(
            MoneylineSelection(side="home", team="CHI", decimal_price=Decimal(home), raw_pointer="$.home"),
            MoneylineSelection(side="away", team="GB", decimal_price=Decimal(away), raw_pointer="$.away"),
        ),
        overtime_included=True, tie_handling="push", market_semantics_version="nfl-h2h-v1",
        settlement_policy_url=f"https://{book}.test/rules", settlement_policy_version="v1",
        provenance_grade=ProvenanceGrade.A,
    )


class _Ids:
    def candidate(self, **values) -> DisplayedQuoteCandidate:
        prediction = values["prediction"]
        quote = values["quote"]
        return DisplayedQuoteCandidate(
            candidate_id=f"candidate-{values['side']}", prediction_id=prediction.prediction_id,
            quote_id=quote.quote_id, origin=prediction.origin, side=values["side"],
            decision_at_utc=prediction.decision_at_utc, decimal_price=values["price"],
            raw_p_win=values["raw_p_win"], buffered_p_win=values["buffered_p_win"],
            p_loss=values["raw_p_loss"], buffered_p_loss=values["buffered_p_loss"],
            p_push=values["p_push"], displayed_ev_per_unit=values["ev"],
            quarter_kelly_fraction=values["kelly"], policy_version=values["policy_version"],
            decision_status="candidate", reason_codes=[],
        )

    def decision(self, **values) -> BettingDecision:
        prediction = values["prediction"]
        return BettingDecision(
            decision_id=f"decision-{values['side']}", prediction_id=prediction.prediction_id,
            canonical_event_id=prediction.canonical_event_id, origin=prediction.origin, side=values["side"],
            quote_id=values["quote_id"] if values["candidate_id"] else None,
            candidate_id=values["candidate_id"], evaluated_at_utc=prediction.decision_at_utc,
            policy_version=values["policy_version"], status=values["status"], reason_codes=values["reason_codes"],
        )


def _evidence() -> CalibrationBinEvidence:
    return CalibrationBinEvidence(
        one_sided_upper_absolute_error=Decimal("0.03"), sample_count=50, confidence=0.95,
        cutoff_at_utc=NOW - timedelta(seconds=1), origin=Origin.T60, binning_method="equal_count",
        out_of_sample=True, frozen=True, evidence_id="evidence-v1",
    )


def _layer(bins: DurableCalibrationBins) -> ProductionMarketLayer:
    policy = _policy()
    candidates = CandidatePolicy(
        policy, policy, _Ids(),
        CandidateContext("champion-v1", {"model": "model-v1", "forecast": "forecast-v1"}),
        InMemoryDecisionRepository(),
    )
    return ProductionMarketLayer(
        active_event=lambda event_id: _event() if event_id == "event-v1" else None,
        odds_policy=policy, candidate_policy=candidates, calibration_bins=bins,
        comparator_id=lambda prediction, quote_ids: f"comparator-{prediction.prediction_id}-{len(quote_ids)}",
    )


# Catches: persisted evidence returned despite being unfrozen, in-sample, wrong-origin, or not
# strictly older than the decision time.
def test_calibration_bins_return_only_eligible_historical_evidence(tmp_path: Path) -> None:
    bins = DurableCalibrationBins(tmp_path / "calibration.json", (_evidence(),))

    found = bins.lookup(origin=Origin.T60, probability=Decimal("0.55"), strictly_before_utc=NOW,
                        minimum_games=50, confidence=0.95)

    assert found == _evidence()
    ineligible = _evidence().__class__(
        **{**_evidence().__dict__, "frozen": False, "evidence_id": "unfrozen"}
    )
    assert DurableCalibrationBins(tmp_path / "invalid.json", (ineligible,)).lookup(
        origin=Origin.T60, probability=Decimal("0.55"), strictly_before_utc=NOW,
        minimum_games=50, confidence=0.95,
    ) is None


# Catches: bypassing comparator selection, trusted event-source matching, or the audited
# home-and-away candidate policy decisions.
def test_market_layer_returns_comparator_and_both_audited_decisions(tmp_path: Path) -> None:
    quotes = (_quote("a", "2.05", "1.80"), _quote("b", "2.10", "1.75"))
    evaluation = _layer(DurableCalibrationBins(tmp_path / "calibration.json", (_evidence(),))).evaluate(
        _prediction(), quotes, NOW
    )

    assert evaluation.comparator is not None
    assert evaluation.comparator.quote_ids == ["quote-a", "quote-b"]
    assert {decision.side for decision in evaluation.decisions} == {"home", "away"}
    assert all(candidate.fixed_stake_units == Decimal(1) for candidate in evaluation.candidates)
    assert evaluation.quotes == quotes
