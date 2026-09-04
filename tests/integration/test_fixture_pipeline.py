from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from nfl_predictor.betting.settlement import OperatorSettlementEvidence
from nfl_predictor.capture.service import CaptureService
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin, PredictionStatus, ProvenanceGrade, SnapshotStatus
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.lineage import CaptureManifest, FeatureSnapshot, NormalizedFact
from nfl_predictor.contracts.markets import (
    ActualTicket,
    DisplayedQuoteCandidate,
    MarketComparator,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.evaluation.promotion import load_evaluation_policy
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.sources.base import BuildIdentity, RawResponse
from nfl_predictor.sources.outcomes import OutcomeAdapter
from nfl_predictor.workflows import outcomes as outcomes_module
from nfl_predictor.workflows.forecast import (
    ArtifactBinding,
    CaptureBundle,
    DurableForecastRepository,
    ForecastExecutionContext,
    ForecastRepositories,
    ForecastWorkflow,
    MarketEvaluation,
)
from nfl_predictor.workflows.outcomes import OutcomeWorkflow
from nfl_predictor.workflows.report import ReportWorkflow

NOW = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)
KICKOFF = NOW + timedelta(hours=1)
CODE_SHA = "1" * 40
LOCK_SHA = "2" * 64
ROOT = Path(__file__).resolve().parents[2]
EVALUATION_POLICY = load_evaluation_policy(ROOT / "configs" / "evaluation_policy_v1.toml")


class FixtureSource:
    source = "fixture-football"

    def __init__(self, payload: bytes, source: str | None = None) -> None:
        self.payload = payload
        if source is not None:
            self.source = source

    def fetch(self, request: dict[str, object]) -> RawResponse:
        return RawResponse(
            source=self.source,
            request_fingerprint="fixture-only",
            request_started_at_utc=NOW - timedelta(seconds=2),
            response_received_at_utc=NOW - timedelta(seconds=1),
            http_status=200,
            payload=self.payload,
            allowlisted_headers={},
        )


def event() -> EventVersion:
    return EventVersion(
        canonical_event_id="event-1",
        event_version=1,
        source_event_ids={"nflverse": "2026_01_GB_CHI"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=KICKOFF,
        neutral_site=False,
        observed_at_utc=NOW - timedelta(days=7),
        available_at_utc=NOW - timedelta(days=7),
        captured_at_utc=NOW - timedelta(days=7),
        raw_payload_sha256="a" * 64,
    )


class FixtureLineageRepository:
    def __init__(self) -> None:
        self.manifests: dict[str, CaptureManifest] = {}
        self.facts: dict[str, NormalizedFact] = {}

    def resolve_facts(self, ids: list[str]) -> list[NormalizedFact]:
        return [self.facts[item] for item in ids]

    def resolve_manifests(self, ids: list[str]) -> list[CaptureManifest]:
        return [self.manifests[item] for item in ids]


class RequiredCapture:
    def __init__(
        self, service: CaptureService, source: FixtureSource, repo: FixtureLineageRepository
    ) -> None:
        self.service = service
        self.source = source
        self.repo = repo

    def capture_live(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        manifest = self.service.capture(self.source, {}, attempt_id)
        self.repo.manifests[manifest.capture_id] = manifest
        fact = NormalizedFact(
            fact_id=f"fact-strength-{manifest.capture_id}",
            capture_id=manifest.capture_id,
            fact_type="team_strength_prior",
            entity_keys={"team": "CHI"},
            payload={"strength": Decimal("0.25")},
            raw_pointer="$",
            available_at_utc=manifest.response_received_at_utc,
            captured_at_utc=manifest.response_received_at_utc,
            provenance_grade=ProvenanceGrade.A,
            normalization_schema_version="v1",
            fact_content_sha256="3" * 64,
        )
        self.repo.facts[fact.fact_id] = fact
        return CaptureBundle((manifest, fact), {"strength": Decimal("0.25")})

    def capture_replay(
        self, obligation: OriginObligation, attempt_id: str, cutoff: datetime
    ) -> CaptureBundle:
        raise AssertionError("fixture prospective run cannot use replay capture")


class FixtureMarketCapture:
    enabled = True

    def __init__(self, service: CaptureService, repo: FixtureLineageRepository) -> None:
        self.service = service
        self.repo = repo

    def capture(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        payload = b'[{"book":"book-a","home":2.0,"away":1.9}]'
        manifest = self.service.capture(FixtureSource(payload, "fixture-market"), {}, attempt_id)
        self.repo.manifests[manifest.capture_id] = manifest
        quote = MoneylineQuote(
            quote_id="quote-1",
            capture_id=manifest.capture_id,
            canonical_event_id=obligation.event.canonical_event_id,
            source_event_id=obligation.event.source_event_ids["nflverse"],
            book_key="book-a",
            book_name="Book A",
            market_key="h2h",
            period="full_game",
            provider_last_update_at_utc=manifest.response_received_at_utc,
            response_received_at_utc=manifest.response_received_at_utc,
            capture_lag_seconds=0,
            provider_update_lag_seconds=0,
            selections=(
                MoneylineSelection(
                    side="home", team="CHI", decimal_price=Decimal(2), raw_pointer="$[0]"
                ),
                MoneylineSelection(
                    side="away",
                    team="GB",
                    decimal_price=Decimal("1.9"),
                    raw_pointer="$[1]",
                ),
            ),
            overtime_included=True,
            tie_handling="push",
            market_semantics_version="nfl-h2h-v1",
            settlement_policy_url="https://example.test/rules",
            settlement_policy_version="v1",
            provenance_grade=ProvenanceGrade.A,
        )
        second_quote = quote.model_copy(
            update={
                "quote_id": "quote-2",
                "book_key": "book-b",
                "book_name": "Book B",
                "selections": (
                    quote.selections[0].model_copy(update={"decimal_price": Decimal("1.98")}),
                    quote.selections[1].model_copy(update={"decimal_price": Decimal("1.92")}),
                ),
            }
        )
        return CaptureBundle((manifest, quote, second_quote), (quote, second_quote))


class Builder:
    def __init__(self, repo: FixtureLineageRepository) -> None:
        self.repo = repo

    def build(self, item: EventVersion, origin: Origin, cutoff: datetime, mode: str):
        fact = list(self.repo.facts.values())[-1]
        return FeatureSnapshot(
            snapshot_id="snapshot-1",
            canonical_event_id=item.canonical_event_id,
            event_version=item.event_version,
            origin=origin,
            decision_at_utc=cutoff,
            feature_policy_version="features-v1",
            feature_schema_version="schema-v1",
            values={"strength": Decimal("0.25")},
            input_manifest_ids=[fact.capture_id],
            input_fact_ids=[fact.fact_id],
            join_policy_version="asof-v1",
            provenance_grade=ProvenanceGrade.A,
            feature_vector_sha256="4" * 64,
            status=SnapshotStatus.COMPLETE,
            reason_codes=[],
        )


class Registry:
    def for_origin(self, origin: Origin):
        return (
            ArtifactBinding(
                "artifact-1",
                origin,
                "champion",
                True,
                True,
                market_policy_version="odds-v1",
            ),
        )


class Predictor:
    def predict(self, snapshot, artifact, *, origin_run_id, obligation, created_at_utc, context):
        return Prediction(
            prediction_id="prediction-1",
            origin_run_id=origin_run_id,
            canonical_event_id=obligation.event.canonical_event_id,
            event_version=obligation.event.event_version,
            origin=obligation.origin,
            target_at_utc=obligation.window.target_at_utc,
            decision_at_utc=snapshot.decision_at_utc,
            model_lane="football_only",
            model_role="champion",
            model_artifact_id=artifact.artifact_id,
            calibrator_artifact_id="calibrator-artifact-1",
            feature_snapshot_id=snapshot.snapshot_id,
            market_snapshot_id=None,
            p_home=Decimal("0.60"),
            p_away=Decimal("0.38"),
            p_tie=Decimal("0.02"),
            predicted_winner="home",
            provenance_grade=context.provenance_grade,
            status=PredictionStatus.COMPLETE,
            reason_codes=[],
            code_sha=CODE_SHA,
            policy_versions={"forecast": obligation.policy_version},
            created_at_utc=created_at_utc,
        )


class FailingPredictor:
    def predict(self, snapshot, artifact, *, origin_run_id, obligation, created_at_utc, context):
        raise RuntimeError("fixture prediction failed")


class FixtureMarketLayer:
    def evaluate(self, prediction, market_payload, decision_at_utc):
        quote = market_payload[0]
        comparator = MarketComparator(
            market_comparator_id="comparator-1",
            origin_run_id=prediction.origin_run_id,
            canonical_event_id=prediction.canonical_event_id,
            origin=prediction.origin,
            decision_at_utc=decision_at_utc,
            market_policy_version="odds-v1",
            quote_ids=[item.quote_id for item in market_payload],
            r_home_market=Decimal("0.4897435897435897435897435897"),
            p_tie_shared_prior=prediction.p_tie,
            provenance_grade=ProvenanceGrade.A,
        )
        candidate = DisplayedQuoteCandidate(
            candidate_id="candidate-1",
            prediction_id=prediction.prediction_id,
            quote_id=quote.quote_id,
            origin=prediction.origin,
            side="home",
            decision_at_utc=decision_at_utc,
            decimal_price=Decimal(2),
            raw_p_win=Decimal("0.60"),
            buffered_p_win=Decimal("0.57"),
            p_loss=Decimal("0.38"),
            buffered_p_loss=Decimal("0.41"),
            p_push=Decimal("0.02"),
            displayed_ev_per_unit=Decimal("0.16"),
            quarter_kelly_fraction=Decimal("0.04081632653061224489795918368"),
            policy_version="candidate-v1",
            decision_status="candidate",
            reason_codes=[],
        )
        decision = BettingDecision(
            decision_id="decision-1",
            prediction_id=prediction.prediction_id,
            canonical_event_id=prediction.canonical_event_id,
            origin=prediction.origin,
            side="home",
            quote_id=quote.quote_id,
            candidate_id=candidate.candidate_id,
            evaluated_at_utc=decision_at_utc,
            policy_version="candidate-v1",
            status="candidate",
            reason_codes=[],
        )
        return MarketEvaluation(comparator, (decision,), (candidate,), tuple(market_payload))


def test_fixture_source_to_scorecard_chain_never_uses_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts = 0

    def fail_network(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    lineage = FixtureLineageRepository()
    forecast_root = tmp_path / "forecast-ledger"
    forecast_repository = DurableForecastRepository(forecast_root, "prospective")
    source_registry = Registry()
    target_registry = Registry()
    capture = CaptureService(
        tmp_path, lambda _payload, _manifest: None, BuildIdentity(CODE_SHA, LOCK_SHA)
    )
    obligation = OriginObligation(
        event(), ForecastOrigin.for_kickoff(Origin.T60, KICKOFF), "forecast-v1"
    )
    forecast = ForecastWorkflow(
        repositories=ForecastRepositories(forecast_repository),
        required_capture=RequiredCapture(capture, FixtureSource(b'{"strength": 0.25}'), lineage),
        market_capture=FixtureMarketCapture(capture, lineage),
        feature_builder=Builder(lineage),
        lineage_repository=lineage,
        artifact_registry=source_registry,
        predictor=Predictor(),
        market_layer=FixtureMarketLayer(),
        clock=lambda: NOW,
        code_sha=CODE_SHA,
    ).run(
        obligation,
        "fixture",
        ForecastExecutionContext.live(NOW),
    )
    assert forecast is not None
    assert forecast.status == "COMPLETE", forecast.reason_codes
    durable_type = getattr(outcomes_module, "DurableOutcomeReportRepository", None)
    assert durable_type is not None
    reloaded_forecasts = DurableForecastRepository(
        forecast_root,
        "prospective",
        artifact_resolver=source_registry,
    )
    committed_graph = reloaded_forecasts.load_committed_graph(obligation.idempotency_key)
    assert committed_graph.run == forecast
    assert len(committed_graph.candidates) == 1
    assert tuple(item.quote_id for item in committed_graph.quotes) == ("quote-1", "quote-2")
    durable_state = durable_type(tmp_path / "outcome-report-ledger", target_registry)
    durable_state.import_forecast_graph(reloaded_forecasts, obligation.idempotency_key)
    durable_state.import_forecast_graph(reloaded_forecasts, obligation.idempotency_key)
    assert not (tmp_path / "outcome-report-ledger" / "commits" / "workflow-predictions").exists()
    assert not (tmp_path / "outcome-report-ledger" / "commits" / "workflow-candidates").exists()
    imported_graph = durable_type(
        tmp_path / "outcome-report-ledger", target_registry
    ).load_forecast_graph(forecast.origin_run_id)
    imported_candidate = imported_graph.candidates[0]

    def additional_forecast(
        item: OriginObligation,
        at: datetime,
        registry: Registry,
        predictor: Predictor | FailingPredictor | None = None,
    ):
        return ForecastWorkflow(
            repositories=ForecastRepositories(
                DurableForecastRepository(forecast_root, "prospective")
            ),
            required_capture=RequiredCapture(
                capture, FixtureSource(b'{"strength": 0.25}'), lineage
            ),
            market_capture=FixtureMarketCapture(capture, lineage),
            feature_builder=Builder(lineage),
            lineage_repository=lineage,
            artifact_registry=registry,
            predictor=predictor or Predictor(),
            market_layer=FixtureMarketLayer(),
            clock=lambda: at,
            code_sha=CODE_SHA,
        ).run(item, "fixture", ForecastExecutionContext.live(at))

    preopen_obligation = OriginObligation(
        event(), ForecastOrigin.for_kickoff(Origin.T60, KICKOFF), "forecast-preopen"
    )
    preopen_at = preopen_obligation.window.window_opens_at_utc - timedelta(seconds=1)
    assert additional_forecast(preopen_obligation, preopen_at, Registry()) is None
    preopen_record = DurableForecastRepository(forecast_root, "prospective").load_obligation(
        preopen_obligation.idempotency_key
    )
    assert preopen_record is not None
    durable_state.import_forecast_obligation(
        DurableForecastRepository(
            forecast_root,
            "prospective",
            artifact_resolver=source_registry,
        ),
        preopen_obligation.idempotency_key,
    )

    missed_obligation = OriginObligation(
        event(), ForecastOrigin.for_kickoff(Origin.T60, KICKOFF), "forecast-missed"
    )
    missed_at = missed_obligation.window.window_closes_at_utc + timedelta(seconds=1)
    missed = additional_forecast(missed_obligation, missed_at, Registry())
    assert missed is not None and missed.status == "MISSED"
    durable_state.import_forecast_graph(
        DurableForecastRepository(
            forecast_root,
            "prospective",
            artifact_resolver=source_registry,
        ),
        missed_obligation.idempotency_key,
    )

    failed_obligation = OriginObligation(
        event(), ForecastOrigin.for_kickoff(Origin.T60, KICKOFF), "forecast-failed"
    )
    failed = additional_forecast(failed_obligation, NOW, Registry(), FailingPredictor())
    assert failed is not None and failed.status == "FAILED"
    durable_state.import_forecast_graph(
        DurableForecastRepository(
            forecast_root,
            "prospective",
            artifact_resolver=source_registry,
        ),
        failed_obligation.idempotency_key,
    )
    ticket = ActualTicket(
        ticket_id="ticket-1",
        candidate_id=imported_candidate.candidate_id,
        model_attributed=True,
        operator="book-a",
        jurisdiction="IL",
        accepted_at_utc=NOW,
        accepted_decimal_price=Decimal(2),
        stake=Decimal(10),
        currency="USD",
    )
    durable_state.register_ticket(ticket)
    durable_state.register_ticket(
        ActualTicket(
            ticket_id="ticket-report-only",
            candidate_id=None,
            model_attributed=False,
            operator="book-a",
            jurisdiction="IL",
            accepted_at_utc=NOW,
            accepted_decimal_price=Decimal(2),
            stake=Decimal(5),
            currency="USD",
        ),
        canonical_event_id="event-1",
    )
    result_bytes = json.dumps(
        {
            "game_id": "2026_01_GB_CHI",
            "game_status": "final",
            "home_score": 24,
            "away_score": 17,
            "home_team": "CHI",
            "away_team": "GB",
            "finalized_at_utc": NOW.isoformat(),
        },
        sort_keys=True,
    ).encode()
    first_outcome = OutcomeWorkflow(
        OutcomeAdapter(capture, FixtureSource(result_bytes, "nflverse")),
        durable_type(tmp_path / "outcome-report-ledger", target_registry),
    ).run(event(), request={})
    explicit_evidence = OperatorSettlementEvidence(
        "evidence-explicit-standard",
        imported_graph.quotes[0].quote_id,
        event().canonical_event_id,
        first_outcome.outcome.outcome_id,
        first_outcome.outcome.outcome_version,
        imported_graph.quotes[0].book_key,
        imported_graph.quotes[0].settlement_policy_url,
        imported_graph.quotes[0].settlement_policy_version,
        "final",
        "standard_confirmed",
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=1),
    )
    durable_type(tmp_path / "outcome-report-ledger", target_registry).register_operator_evidence(
        explicit_evidence
    )
    outcome_run = OutcomeWorkflow(
        OutcomeAdapter(capture, FixtureSource(result_bytes, "nflverse")),
        durable_type(tmp_path / "outcome-report-ledger", target_registry),
    ).run(event(), request={})
    reloaded_state = durable_type(tmp_path / "outcome-report-ledger", target_registry)
    scorecards = ReportWorkflow(
        reloaded_state,
        evaluation_policy=EVALUATION_POLICY,
        interval_confidence=Decimal("0.95"),
    ).weekly(2026, 1)

    assert forecast is not None and forecast.status == "COMPLETE"
    assert outcome_run.outcome.result == "home"
    assert len(outcome_run.settlements) == 2
    assert outcome_run.report_only_ticket_ids == ("ticket-report-only",)
    probability = next(
        row
        for row in scorecards.probability
        if row.artifact_id == "artifact-1"
        and dict(row.policy_versions)["forecast"] == "forecast-v1"
    )
    winner = next(
        row
        for row in scorecards.winner
        if row.artifact_id == "artifact-1"
        and dict(row.policy_versions)["forecast"] == "forecast-v1"
    )
    market = next(
        row
        for row in scorecards.market
        if row.artifact_id == "artifact-1"
        and dict(row.policy_versions)["forecast"] == "forecast-v1"
    )
    assert probability.n_all_settled == 1
    assert winner.straight_up_accuracy == 1.0
    assert market.quote_ids == ("quote-1", "quote-2")
    assert market.capture_ids == (
        imported_graph.quotes[0].capture_id,
        imported_graph.quotes[1].capture_id,
    )
    assert scorecards.displayed_price[0].candidate_ids == ("candidate-1",)
    assert scorecards.actual_tickets[0].ticket_ids == ("ticket-1",)
    missing_cards = [row for row in scorecards.probability if row.coverage == 0]
    assert len(missing_cards) == 3
    assert all(row.n_missing == 1 and row.n_unresolved == 0 for row in missing_cards)
    assert {
        bundle.run.status if bundle.run is not None else None
        for bundle in reloaded_state.forecast_obligation_bundles()
    } == {None, "COMPLETE", "MISSED", "FAILED"}
    assert len(tuple((tmp_path / "manifests" / "captures").glob("*.json"))) == 6
    assert reloaded_state.load_forecast_graph(forecast.origin_run_id) == imported_graph
    assert reloaded_state.read_prediction("prediction-1") == imported_graph.predictions[0]
    assert reloaded_state.read_candidate("candidate-1") == imported_candidate
    assert reloaded_state.quote_for("quote-1") == imported_graph.quotes[0]
    assert reloaded_state.read_ticket("ticket-1") == ticket
    assert reloaded_state.report_only_ticket_ids() == ("ticket-report-only",)
    assert len(reloaded_state.settlement_envelopes()) == 4
    assert attempts == 0
