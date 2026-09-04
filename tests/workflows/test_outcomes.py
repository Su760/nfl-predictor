from __future__ import annotations

import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from nfl_predictor.betting.settlement import (
    OperatorSettlementEvidence,
    settle_displayed_candidate,
)
from nfl_predictor.capture.service import CaptureService
from nfl_predictor.contracts.attempts import RunAttempt
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import (
    Origin,
    PredictionStatus,
    ProvenanceGrade,
    RunStatus,
    SnapshotStatus,
)
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.forecasts import OriginRun, Prediction
from nfl_predictor.contracts.lineage import CaptureManifest, FeatureSnapshot, NormalizedFact
from nfl_predictor.contracts.markets import (
    ActualTicket,
    DisplayedQuoteCandidate,
    MarketComparator,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.markets.comparator import median_market_home
from nfl_predictor.sources.base import BuildIdentity, RawResponse
from nfl_predictor.sources.outcomes import OutcomeAdapter, OutcomeCapture
from nfl_predictor.storage.ledger import DataIntegrityError
from nfl_predictor.workflows import forecast as forecast_module
from nfl_predictor.workflows.forecast import (
    ArtifactBinding,
    ArtifactSelection,
    CommittedForecastGraph,
    DurableForecastRepository,
    ForecastExecutionContext,
    ForecastObligationRecord,
    MarketValidationBinding,
    validate_committed_graph,
    validate_obligation_artifact_trust,
)
from nfl_predictor.workflows.outcomes import (
    DurableOutcomeReportRepository,
    ObservationWatermark,
    OutcomeWorkflow,
    SettlementEnvelope,
)

NOW = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)
KICKOFF = NOW - timedelta(hours=1)
T60_DECISION = KICKOFF - timedelta(minutes=60)
BASE_EXECUTION_KEY = "event-1:T60:forecast-v1"
BASE_ORIGIN_RUN_ID = sha256(f"prospective|{BASE_EXECUTION_KEY}".encode()).hexdigest()


class StaticArtifactResolver:
    def __init__(self, bindings: tuple[ArtifactBinding, ...]) -> None:
        self.bindings = bindings

    def for_origin(self, origin: Origin) -> tuple[ArtifactBinding, ...]:
        return tuple(binding for binding in self.bindings if binding.origin is origin)


class UnfilteredArtifactResolver:
    def __init__(self, bindings: tuple[ArtifactBinding, ...]) -> None:
        self.bindings = bindings

    def for_origin(self, origin: Origin) -> tuple[ArtifactBinding, ...]:
        return self.bindings


def artifact_resolver_for(
    *graphs: CommittedForecastGraph,
) -> StaticArtifactResolver:
    bindings = tuple(
        selection.binding for graph in graphs for selection in graph.artifact_selections
    )
    unique = {
        (binding.origin, binding.model_role, binding.artifact_id): binding for binding in bindings
    }
    return StaticArtifactResolver(tuple(unique.values()))


def outcome_repository(
    root: Path,
    *graphs: CommittedForecastGraph,
) -> DurableOutcomeReportRepository:
    trusted_graphs = graphs or (forecast_graph(),)
    return DurableOutcomeReportRepository(
        root,
        artifact_resolver_for(*trusted_graphs),
    )


class Source:
    source = "nflverse"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.retrieved_at = NOW
        self.source_snapshot_at: datetime | None = None

    def fetch(self, request: dict[str, object]) -> RawResponse:
        return RawResponse(
            source=self.source,
            request_fingerprint="fixture",
            request_started_at_utc=NOW,
            response_received_at_utc=self.retrieved_at,
            http_status=200,
            payload=json.dumps(self.payload, sort_keys=True).encode(),
            allowlisted_headers={},
            source_snapshot_at_utc=self.source_snapshot_at,
        )


def payload(status: str, home: int | None = None, away: int | None = None) -> dict[str, object]:
    return {
        "game_id": "2026_01_GB_CHI",
        "game_status": status,
        "home_score": home,
        "away_score": away,
        "finalized_at_utc": NOW.isoformat() if status == "final" else None,
    }


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
        venue_id="soldier-field",
        neutral_site=False,
        observed_at_utc=NOW - timedelta(days=7),
        available_at_utc=NOW - timedelta(days=7),
        captured_at_utc=NOW - timedelta(days=7),
        raw_payload_sha256="a" * 64,
    )


def candidate() -> DisplayedQuoteCandidate:
    return DisplayedQuoteCandidate(
        candidate_id="candidate-1",
        prediction_id="prediction-1",
        quote_id="quote-1",
        origin=Origin.T60,
        side="home",
        decision_at_utc=T60_DECISION,
        decimal_price=Decimal("2.00"),
        raw_p_win=Decimal("0.55"),
        buffered_p_win=Decimal("0.52"),
        p_loss=Decimal("0.43"),
        buffered_p_loss=Decimal("0.46"),
        p_push=Decimal("0.02"),
        displayed_ev_per_unit=Decimal("0.06"),
        quarter_kelly_fraction=Decimal("0.01530612244897959183673469388"),
        policy_version="candidate-v1",
        decision_status="candidate",
        reason_codes=[],
    )


def quote() -> MoneylineQuote:
    return MoneylineQuote(
        quote_id="quote-1",
        capture_id="capture-1",
        canonical_event_id="event-1",
        source_event_id="2026_01_GB_CHI",
        book_key="book-a",
        book_name="Book A",
        market_key="h2h",
        period="full_game",
        provider_last_update_at_utc=T60_DECISION,
        response_received_at_utc=T60_DECISION,
        capture_lag_seconds=1,
        provider_update_lag_seconds=1,
        selections=(
            MoneylineSelection(
                side="home", team="CHI", decimal_price=Decimal("2.00"), raw_pointer="$[0]"
            ),
            MoneylineSelection(
                side="away", team="GB", decimal_price=Decimal("1.90"), raw_pointer="$[1]"
            ),
        ),
        overtime_included=True,
        tie_handling="push",
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="v1",
        provenance_grade=ProvenanceGrade.A,
    )


def prediction() -> Prediction:
    return Prediction(
        prediction_id="prediction-1",
        origin_run_id=BASE_ORIGIN_RUN_ID,
        canonical_event_id="event-1",
        event_version=1,
        origin=Origin.T60,
        target_at_utc=T60_DECISION,
        decision_at_utc=T60_DECISION,
        model_lane="football_only",
        model_role="champion",
        model_artifact_id="artifact-1",
        calibrator_artifact_id="calibrator-1",
        feature_snapshot_id="snapshot-1",
        market_snapshot_id=None,
        p_home=Decimal("0.55"),
        p_away=Decimal("0.43"),
        p_tie=Decimal("0.02"),
        predicted_winner="home",
        provenance_grade=ProvenanceGrade.A,
        status=PredictionStatus.COMPLETE,
        reason_codes=[],
        code_sha="1" * 40,
        policy_versions={"forecast": "forecast-v1"},
        created_at_utc=T60_DECISION,
    )


def comparator() -> MarketComparator:
    linked_quote = quote()
    return MarketComparator(
        market_comparator_id="comparator-1",
        origin_run_id=BASE_ORIGIN_RUN_ID,
        canonical_event_id="event-1",
        origin=Origin.T60,
        decision_at_utc=T60_DECISION,
        market_policy_version="market-v1",
        quote_ids=["quote-1"],
        r_home_market=median_market_home((linked_quote,)),
        p_tie_shared_prior=Decimal("0.02"),
        provenance_grade=ProvenanceGrade.A,
    )


def forecast_graph() -> CommittedForecastGraph:
    decision_at = prediction().decision_at_utc
    attempt = RunAttempt(
        attempt_id="attempt-1",
        origin_run_id=prediction().origin_run_id,
        retry_of_attempt_id=None,
        trigger="fixture",
        started_at_utc=decision_at,
        ended_at_utc=decision_at,
        status="COMPLETE",
        safe_error_category=None,
    )
    manifest = CaptureManifest(
        capture_id="capture-1",
        run_id=attempt.attempt_id,
        source="fixture",
        request_fingerprint="fixture",
        request_started_at_utc=decision_at,
        response_received_at_utc=decision_at,
        http_status=200,
        raw_path="raw/fixture.bin",
        raw_payload_sha256="d" * 64,
        response_headers_allowlisted={},
        code_sha="1" * 40,
        dependency_lock_sha256="2" * 64,
        schema_version="capture-v1",
    )
    fact = NormalizedFact(
        fact_id="fact-1",
        capture_id=manifest.capture_id,
        fact_type="strength",
        entity_keys={"team": "CHI"},
        payload={"value": 1},
        raw_pointer="$",
        available_at_utc=decision_at,
        captured_at_utc=decision_at,
        provenance_grade=ProvenanceGrade.A,
        normalization_schema_version="v1",
        fact_content_sha256="e" * 64,
    )
    snapshot = FeatureSnapshot(
        snapshot_id="snapshot-1",
        canonical_event_id=event().canonical_event_id,
        event_version=event().event_version,
        origin=Origin.T60,
        decision_at_utc=decision_at,
        feature_policy_version="features-v1",
        feature_schema_version="schema-v1",
        values={"strength": 1},
        input_manifest_ids=[manifest.capture_id],
        input_fact_ids=[fact.fact_id],
        join_policy_version="asof-v1",
        provenance_grade=ProvenanceGrade.A,
        feature_vector_sha256="f" * 64,
        status=SnapshotStatus.COMPLETE,
        reason_codes=[],
    )
    run = OriginRun(
        origin_run_id=prediction().origin_run_id,
        canonical_event_id=event().canonical_event_id,
        event_version=event().event_version,
        origin=Origin.T60,
        forecast_policy_version="forecast-v1",
        target_at_utc=prediction().target_at_utc,
        window_opens_at_utc=prediction().target_at_utc - timedelta(minutes=10),
        window_closes_at_utc=prediction().target_at_utc + timedelta(minutes=10),
        run_started_at_utc=attempt.started_at_utc,
        decision_at_utc=decision_at,
        status="COMPLETE",
        attempt_ids=[attempt.attempt_id],
        prediction_ids=[prediction().prediction_id],
        market_comparator_id=comparator().market_comparator_id,
        reason_codes=[],
        code_sha="1" * 40,
        created_at_utc=decision_at,
    )
    obligation = ForecastObligationRecord(
        execution_key=BASE_EXECUTION_KEY,
        event=event(),
        origin=Origin.T60,
        target_at_utc=run.target_at_utc,
        window_opens_at_utc=run.window_opens_at_utc,
        window_closes_at_utc=run.window_closes_at_utc,
        forecast_policy_version=run.forecast_policy_version,
        mode="live",
        namespace="prospective",
        provenance_grade=ProvenanceGrade.A,
        as_of_utc=None,
        reconstruction_reason=None,
    )
    decision = BettingDecision(
        decision_id="decision-1",
        prediction_id=prediction().prediction_id,
        canonical_event_id=event().canonical_event_id,
        origin=Origin.T60,
        side="home",
        quote_id=quote().quote_id,
        candidate_id=candidate().candidate_id,
        evaluated_at_utc=decision_at,
        policy_version=candidate().policy_version,
        status="candidate",
        reason_codes=[],
    )
    binding = ArtifactBinding(
        "artifact-1",
        Origin.T60,
        "champion",
        True,
        True,
        calibrator_artifact_id="calibrator-1",
    )
    obligation = replace(obligation, champion_binding=binding)
    return CommittedForecastGraph(
        obligation,
        run,
        (attempt,),
        (manifest,),
        (fact,),
        (quote(),),
        (),
        (ArtifactSelection(attempt.attempt_id, run.origin_run_id, binding),),
        snapshot,
        (prediction(),),
        comparator(),
        (decision,),
        (candidate(),),
        MarketValidationBinding(
            comparator().market_comparator_id,
            prediction().prediction_id,
            comparator().market_policy_version,
            candidate().policy_version,
        ),
    )


def retarget_forecast_graph(
    graph: CommittedForecastGraph,
    event_value: EventVersion,
    origin: Origin,
    policy_version: str,
) -> CommittedForecastGraph:
    assert graph.snapshot is not None
    assert graph.comparator is not None
    window = ForecastOrigin.for_kickoff(origin, event_value.kickoff_at_utc)
    decision_at = window.target_at_utc
    execution_key = f"{event_value.canonical_event_id}:{origin.value}:{policy_version}"
    origin_run_id = sha256(f"prospective|{execution_key}".encode()).hexdigest()
    suffix = f"{event_value.canonical_event_id}-{event_value.event_version}-{origin.value}"
    attempt = graph.attempts[0].model_copy(
        update={
            "attempt_id": f"attempt-{suffix}",
            "origin_run_id": origin_run_id,
            "started_at_utc": decision_at,
            "ended_at_utc": decision_at,
        }
    )
    manifest = graph.manifests[0].model_copy(
        update={
            "capture_id": f"capture-{suffix}",
            "run_id": attempt.attempt_id,
            "request_started_at_utc": decision_at,
            "response_received_at_utc": decision_at,
            "raw_path": f"raw/{suffix}.bin",
        }
    )
    fact = graph.facts[0].model_copy(
        update={
            "fact_id": f"fact-{suffix}",
            "capture_id": manifest.capture_id,
            "available_at_utc": decision_at,
            "captured_at_utc": decision_at,
        }
    )
    snapshot = graph.snapshot.model_copy(
        update={
            "snapshot_id": f"snapshot-{suffix}",
            "canonical_event_id": event_value.canonical_event_id,
            "event_version": event_value.event_version,
            "origin": origin,
            "decision_at_utc": decision_at,
            "input_manifest_ids": [manifest.capture_id],
            "input_fact_ids": [fact.fact_id],
        }
    )
    prediction_value = graph.predictions[0].model_copy(
        update={
            "prediction_id": f"prediction-{suffix}",
            "origin_run_id": origin_run_id,
            "canonical_event_id": event_value.canonical_event_id,
            "event_version": event_value.event_version,
            "origin": origin,
            "target_at_utc": window.target_at_utc,
            "decision_at_utc": decision_at,
            "feature_snapshot_id": snapshot.snapshot_id,
            "policy_versions": {"forecast": policy_version},
            "created_at_utc": decision_at,
        }
    )
    quote_value = graph.quotes[0].model_copy(
        update={
            "quote_id": f"quote-{suffix}",
            "capture_id": manifest.capture_id,
            "canonical_event_id": event_value.canonical_event_id,
            "source_event_id": event_value.source_event_ids["nflverse"],
            "provider_last_update_at_utc": decision_at,
            "response_received_at_utc": decision_at,
        }
    )
    comparator_value = graph.comparator.model_copy(
        update={
            "market_comparator_id": f"comparator-{suffix}",
            "origin_run_id": origin_run_id,
            "canonical_event_id": event_value.canonical_event_id,
            "origin": origin,
            "decision_at_utc": decision_at,
            "quote_ids": [quote_value.quote_id],
            "r_home_market": median_market_home((quote_value,)),
        }
    )
    candidate_value = graph.candidates[0].model_copy(
        update={
            "candidate_id": f"candidate-{suffix}",
            "prediction_id": prediction_value.prediction_id,
            "quote_id": quote_value.quote_id,
            "origin": origin,
            "decision_at_utc": decision_at,
        }
    )
    decision_value = graph.decisions[0].model_copy(
        update={
            "decision_id": f"decision-{suffix}",
            "prediction_id": prediction_value.prediction_id,
            "canonical_event_id": event_value.canonical_event_id,
            "origin": origin,
            "quote_id": quote_value.quote_id,
            "candidate_id": candidate_value.candidate_id,
            "evaluated_at_utc": decision_at,
        }
    )
    run = graph.run.model_copy(
        update={
            "origin_run_id": origin_run_id,
            "canonical_event_id": event_value.canonical_event_id,
            "event_version": event_value.event_version,
            "origin": origin,
            "forecast_policy_version": policy_version,
            "target_at_utc": window.target_at_utc,
            "window_opens_at_utc": window.window_opens_at_utc,
            "window_closes_at_utc": window.window_closes_at_utc,
            "run_started_at_utc": decision_at,
            "decision_at_utc": decision_at,
            "attempt_ids": [attempt.attempt_id],
            "prediction_ids": [prediction_value.prediction_id],
            "market_comparator_id": comparator_value.market_comparator_id,
            "created_at_utc": decision_at,
        }
    )
    obligation = replace(
        graph.obligation,
        execution_key=execution_key,
        event=event_value,
        origin=origin,
        target_at_utc=window.target_at_utc,
        window_opens_at_utc=window.window_opens_at_utc,
        window_closes_at_utc=window.window_closes_at_utc,
        forecast_policy_version=policy_version,
    )
    champion = graph.obligation.champion_binding
    assert champion is not None
    champion = replace(champion, origin=origin)
    obligation = replace(obligation, champion_binding=champion)
    selections = (ArtifactSelection(attempt.attempt_id, origin_run_id, champion),)
    return CommittedForecastGraph(
        obligation,
        run,
        (attempt,),
        (manifest,),
        (fact,),
        (quote_value,),
        (),
        selections,
        snapshot,
        (prediction_value,),
        comparator_value,
        (decision_value,),
        (candidate_value,),
        MarketValidationBinding(
            comparator_value.market_comparator_id,
            prediction_value.prediction_id,
            comparator_value.market_policy_version,
            candidate_value.policy_version,
        ),
    )


def missed_forecast_graph(graph: CommittedForecastGraph) -> CommittedForecastGraph:
    ended_at = graph.obligation.window_closes_at_utc + timedelta(seconds=1)
    attempt = graph.attempts[0].model_copy(
        update={
            "started_at_utc": ended_at,
            "ended_at_utc": ended_at,
            "status": "FAILED",
            "safe_error_category": "WINDOW_CLOSED",
        }
    )
    run = graph.run.model_copy(
        update={
            "run_started_at_utc": ended_at,
            "decision_at_utc": None,
            "status": RunStatus.MISSED,
            "attempt_ids": [attempt.attempt_id],
            "prediction_ids": [],
            "market_comparator_id": None,
            "reason_codes": ["WINDOW_CLOSED"],
            "created_at_utc": ended_at,
        }
    )
    return CommittedForecastGraph(
        graph.obligation,
        run,
        (attempt,),
        (),
        (),
        (),
        (),
        graph.artifact_selections,
        None,
        (),
        None,
        (),
        (),
        None,
    )


def championless_terminal_graph(status: RunStatus) -> CommittedForecastGraph:
    base = forecast_graph()
    graph = missed_forecast_graph(base)
    if status is RunStatus.FAILED:
        failed_at = graph.obligation.target_at_utc
        attempt = graph.attempts[0].model_copy(
            update={
                "started_at_utc": failed_at,
                "ended_at_utc": failed_at,
                "safe_error_category": "MODEL_FAILURE",
            }
        )
        run = graph.run.model_copy(
            update={
                "run_started_at_utc": failed_at,
                "status": RunStatus.FAILED,
                "reason_codes": ["MODEL_FAILURE"],
                "created_at_utc": failed_at,
            }
        )
        graph = replace(graph, run=run, attempts=(attempt,))
    return replace(
        graph,
        obligation=replace(graph.obligation, champion_binding=None),
        artifact_selections=(),
    )


def invalid_champion_resolver(
    case: str,
    champion: ArtifactBinding,
) -> UnfilteredArtifactResolver:
    if case == "zero":
        bindings: tuple[ArtifactBinding, ...] = ()
    elif case == "multiple":
        bindings = (
            champion,
            replace(
                champion,
                artifact_id="second-frozen-champion",
                calibrator_artifact_id="second-frozen-calibrator",
            ),
        )
    elif case == "wrong_origin":
        bindings = (replace(champion, origin=Origin.T72),)
    else:
        bindings = (replace(champion, verified=False),)
    return UnfilteredArtifactResolver(bindings)


def utc_nanoseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def persist_forecast_obligation(
    root: Path,
    obligation: ForecastObligationRecord,
) -> DurableForecastRepository:
    bindings = () if obligation.champion_binding is None else (obligation.champion_binding,)
    source = DurableForecastRepository(
        root,
        "prospective",
        artifact_resolver=StaticArtifactResolver(bindings),
    )
    obligation_value = OriginObligation(
        obligation.event,
        ForecastOrigin(
            origin=obligation.origin,
            target_at_utc=obligation.target_at_utc,
            window_opens_at_utc=obligation.window_opens_at_utc,
            window_closes_at_utc=obligation.window_closes_at_utc,
        ),
        obligation.forecast_policy_version,
    )
    context = ForecastExecutionContext.live(obligation.target_at_utc)
    source.register_obligation(
        obligation.execution_key,
        obligation_value,
        context,
        obligation.champion_binding,
    )
    return source


def prepare_forecast_graph(
    root: Path,
    graph: CommittedForecastGraph,
    *,
    trusted_bindings: tuple[ArtifactBinding, ...] | None = None,
) -> DurableForecastRepository:
    source = persist_forecast_obligation(root, graph.obligation)
    source.artifact_resolver = (
        artifact_resolver_for(graph)
        if trusted_bindings is None
        else StaticArtifactResolver(trusted_bindings)
    )
    for manifest in graph.manifests:
        source.append_capture_record(manifest)
    for fact in graph.facts:
        source.append_capture_record(fact)
    for quote_value in graph.quotes:
        source.append_capture_record(quote_value)
    for failure in graph.optional_failures:
        source.append_capture_record(failure)
    for selection in graph.artifact_selections:
        source.append_artifact_selection(selection)
    if graph.snapshot is not None:
        source.append_feature_snapshot(graph.snapshot)
    for prediction_value in graph.predictions:
        source.append_prediction(prediction_value)
    if graph.comparator is not None:
        source.append_market_comparator(graph.comparator)
    if graph.market_validation is not None:
        source.append_market_validation(graph.market_validation)
    for decision_value in graph.decisions:
        source.append_betting_decision(decision_value)
    for candidate_value in graph.candidates:
        source.append_candidate(candidate_value)
    return source


def persist_forecast_graph(
    root: Path,
    graph: CommittedForecastGraph,
    *,
    trusted_bindings: tuple[ArtifactBinding, ...] | None = None,
) -> DurableForecastRepository:
    source = prepare_forecast_graph(
        root,
        graph,
        trusted_bindings=trusted_bindings,
    )
    context = ForecastExecutionContext.live(graph.obligation.target_at_utc)
    source.commit_terminal(
        graph.obligation.execution_key,
        graph.run,
        graph.attempts[-1],
        deadline=graph.obligation.window_closes_at_utc,
        context=context,
        clock=lambda: min(
            graph.run.created_at_utc,
            graph.obligation.window_closes_at_utc,
        ),
    )
    return source


def durable_repository(root: Path) -> DurableOutcomeReportRepository:
    graph = forecast_graph()
    repository = outcome_repository(root, graph)
    source = persist_forecast_graph(root / "forecast-source", graph)
    repository.import_forecast_graph(source, graph.obligation.execution_key)
    return repository


def evidence(
    outcome,
    *,
    evidence_id: str,
    resolution: str,
    frozen_at: datetime,
) -> OperatorSettlementEvidence:
    return OperatorSettlementEvidence(
        evidence_id=evidence_id,
        quote_id="quote-1",
        canonical_event_id="event-1",
        outcome_id=outcome.outcome_id,
        outcome_version=outcome.outcome_version,
        operator="book-a",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="v1",
        operator_event_status="final",
        resolution=resolution,
        observed_at_utc=frozen_at,
        frozen_at_utc=frozen_at,
    )


class Repository:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.outcomes: list[object] = []
        self.envelopes: list[object] = []
        self.candidate = candidate()
        self.quote = quote()
        self.tickets = [
            ActualTicket(
                ticket_id="ticket-model",
                candidate_id="candidate-1",
                model_attributed=True,
                operator="book-a",
                jurisdiction="IL",
                accepted_at_utc=NOW - timedelta(hours=5),
                accepted_decimal_price=Decimal("1.95"),
                stake=Decimal(10),
                currency="USD",
            ),
            ActualTicket(
                ticket_id="ticket-discretionary",
                candidate_id=None,
                model_attributed=False,
                operator="book-a",
                jurisdiction="IL",
                accepted_at_utc=NOW - timedelta(hours=5),
                accepted_decimal_price=Decimal("1.95"),
                stake=Decimal(10),
                currency="USD",
            ),
        ]
        self.report_only: list[str] = []
        self.evidence_suffix = ""
        self.fail_after_settlements: int | None = None
        self.quarantines: list[str] = []
        self.watermarks: list[object] = []
        self.capture_manifests: list[CaptureManifest] = []
        self.evidence_frozen_at: datetime | None = None

    def transact(self, key: str, operation):
        with self.lock:
            return operation()

    def latest_outcome(self, event_id: str, source: str):
        return self.outcomes[-1] if self.outcomes else None

    def append_outcome(self, outcome) -> None:
        self.outcomes.append(outcome)

    def append_outcome_capture(self, manifest: CaptureManifest) -> None:
        if manifest not in self.capture_manifests:
            self.capture_manifests.append(manifest)

    def read_outcome(self, outcome_id: str, outcome_version: int):
        return next(
            item
            for item in self.outcomes
            if item.outcome_id == outcome_id and item.outcome_version == outcome_version
        )

    def displayed_candidates(self, event_id: str):
        return (self.candidate,)

    def quote_for(self, quote_id: str):
        return self.quote

    def tickets_for_event(self, event_id: str):
        return tuple(self.tickets)

    def operator_evidence(self, quote, outcome):
        frozen_at = self.evidence_frozen_at or outcome.source_version_or_retrieved_at_utc
        return OperatorSettlementEvidence(
            evidence_id=f"evidence-{outcome.outcome_version}{self.evidence_suffix}",
            quote_id=quote.quote_id,
            canonical_event_id=quote.canonical_event_id,
            outcome_id=outcome.outcome_id,
            outcome_version=outcome.outcome_version,
            operator=quote.book_key,
            settlement_policy_url=quote.settlement_policy_url,
            settlement_policy_version=quote.settlement_policy_version,
            operator_event_status=outcome.game_status,
            resolution="standard_confirmed" if outcome.game_status == "final" else "unresolved",
            observed_at_utc=frozen_at,
            frozen_at_utc=frozen_at,
        )

    def latest_settlement(self, candidate_id: str | None, ticket_id: str | None):
        matches = [
            item.settlement
            for item in self.envelopes
            if item.settlement.candidate_id == candidate_id
            and item.settlement.ticket_id == ticket_id
        ]
        return matches[-1] if matches else None

    def latest_settlement_envelope(self, candidate_id: str | None, ticket_id: str | None):
        matches = [
            item
            for item in self.envelopes
            if item.settlement.candidate_id == candidate_id
            and item.settlement.ticket_id == ticket_id
        ]
        return matches[-1] if matches else None

    def append_settlement(self, envelope) -> None:
        if (
            self.fail_after_settlements is not None
            and len(self.envelopes) >= self.fail_after_settlements
        ):
            self.fail_after_settlements = None
            raise OSError("simulated partial settlement failure")
        if any(
            item.settlement.settlement_id == envelope.settlement.settlement_id
            for item in self.envelopes
        ):
            return
        self.envelopes.append(envelope)

    def mark_ticket_report_only(self, ticket_id: str) -> None:
        if ticket_id not in self.report_only:
            self.report_only.append(ticket_id)

    def append_quarantine(self, reason: str) -> None:
        self.quarantines.append(reason)

    def latest_observation_watermark(self, event_id: str, source: str):
        return self.watermarks[-1] if self.watermarks else None

    def append_observation_watermark(self, watermark) -> None:
        self.watermarks.append(watermark)


def workflow_for(raw_payload: dict[str, object], repository: Repository | None = None):
    data_root = Path(tempfile.mkdtemp(prefix="outcome-capture-"))
    capture = CaptureService(
        data_root,
        lambda _payload, _manifest: None,
        BuildIdentity("1" * 40, "2" * 64),
    )
    adapter = OutcomeAdapter(capture, Source(raw_payload))
    repo = repository or Repository()
    return OutcomeWorkflow(adapter, repo), repo, capture


def test_outcome_adapter_writes_raw_before_rejecting_schema() -> None:
    workflow, _, capture = workflow_for({"game_status": "final"})

    with pytest.raises(ValueError, match="game_id"):
        workflow.run(event(), request={})

    assert len(tuple((capture.data_root / "raw" / "nflverse").rglob("*.bin"))) == 1


@pytest.mark.parametrize(
    ("status", "home", "away", "result"),
    [
        ("final", 20, 17, "home"),
        ("final", 20, 20, "tie"),
        ("postponed", None, None, "unresolved"),
        ("cancelled", None, None, "unresolved"),
        ("suspended", None, None, "unresolved"),
        ("unresolved", None, None, "unresolved"),
    ],
)
def test_official_states_normalize_without_guessing_book_rules(
    status: str, home: int | None, away: int | None, result: str
) -> None:
    workflow, _, _ = workflow_for(payload(status, home, away))

    run = workflow.run(event(), request={})

    assert run.outcome.game_status == status
    assert run.outcome.result == result


def test_retry_reuses_outcome_and_correction_appends_stable_version() -> None:
    repository = Repository()
    first_workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    first = first_workflow.run(event(), request={})
    retry = first_workflow.run(event(), request={})
    corrected_workflow, _, _ = workflow_for(payload("final", 21, 17), repository)
    corrected_workflow.adapter.source.retrieved_at = NOW + timedelta(minutes=1)
    corrected = corrected_workflow.run(event(), request={})

    assert retry.outcome is first.outcome
    assert corrected.outcome.outcome_id == first.outcome.outcome_id
    assert corrected.outcome.outcome_version == 2
    assert repository.read_outcome(first.outcome.outcome_id, 1).home_score == 20


def test_settlements_bind_exact_operator_and_outcome_lineage_and_supersede() -> None:
    repository = Repository()
    first_workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    first = first_workflow.run(event(), request={})
    corrected_workflow, _, _ = workflow_for(payload("final", 17, 21), repository)
    corrected_workflow.adapter.source.retrieved_at = NOW + timedelta(minutes=1)
    corrected = corrected_workflow.run(event(), request={})

    assert {(item.outcome_id, item.outcome_version) for item in repository.envelopes} == {
        (first.outcome.outcome_id, 1),
        (first.outcome.outcome_id, 2),
    }
    candidate_versions = [
        item.settlement
        for item in repository.envelopes
        if item.settlement.candidate_id == "candidate-1"
    ]
    assert [item.result for item in candidate_versions] == ["win", "loss"]
    assert candidate_versions[1].settlement_version == 2
    assert candidate_versions[1].supersedes_settlement_id == candidate_versions[0].settlement_id
    assert len(corrected.settlements) == 2


def test_only_model_attributed_linked_tickets_are_settled() -> None:
    workflow, repository, _ = workflow_for(payload("final", 20, 17))

    run = workflow.run(event(), request={})

    ticket_settlements = [
        item.settlement for item in repository.envelopes if item.settlement.ticket_id is not None
    ]
    assert [item.ticket_id for item in ticket_settlements] == ["ticket-model"]
    assert ticket_settlements[0].profit_amount == Decimal("9.50")
    assert run.report_only_ticket_ids == ("ticket-discretionary",)


def test_wrong_nflverse_game_is_rejected_before_outcome_or_settlement() -> None:
    wrong = payload("final", 20, 17)
    wrong["game_id"] = "2026_01_WRONG_GAME"
    workflow, repository, capture = workflow_for(wrong)

    with pytest.raises(ValueError, match="source event"):
        workflow.run(event(), request={})

    assert len(tuple((capture.data_root / "raw" / "nflverse").rglob("*.bin"))) == 1
    assert repository.outcomes == []
    assert repository.envelopes == []


def test_partial_settlement_failure_retries_remaining_work_for_same_outcome() -> None:
    repository = Repository()
    repository.fail_after_settlements = 1
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)

    with pytest.raises(OSError, match="partial settlement"):
        workflow.run(event(), request={})
    retry = workflow.run(event(), request={})

    assert retry.outcome.outcome_version == 1
    assert {
        item.settlement.candidate_id or item.settlement.ticket_id for item in repository.envelopes
    } == {
        "candidate-1",
        "ticket-model",
    }


def test_unchanged_outcome_reconciles_new_model_ticket() -> None:
    repository = Repository()
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    workflow.run(event(), request={})
    repository.tickets.append(
        ActualTicket(
            ticket_id="ticket-new",
            candidate_id="candidate-1",
            model_attributed=True,
            operator="book-a",
            jurisdiction="IL",
            accepted_at_utc=NOW - timedelta(hours=4),
            accepted_decimal_price=Decimal("2.10"),
            stake=Decimal(5),
            currency="USD",
        )
    )

    retry = workflow.run(event(), request={})

    assert retry.outcome.outcome_version == 1
    assert any(item.settlement.ticket_id == "ticket-new" for item in repository.envelopes)


def test_changed_operator_evidence_supersedes_without_new_outcome_version() -> None:
    repository = Repository()
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    first = workflow.run(event(), request={})
    repository.evidence_suffix = "-corrected"
    repository.evidence_frozen_at = NOW + timedelta(minutes=1)

    second = workflow.run(event(), request={})

    assert second.outcome.outcome_version == first.outcome.outcome_version
    candidate_versions = [
        item.settlement
        for item in repository.envelopes
        if item.settlement.candidate_id == "candidate-1"
    ]
    assert len(candidate_versions) == 2
    assert candidate_versions[-1].supersedes_settlement_id == candidate_versions[0].settlement_id


def test_older_observation_cannot_supersede_newer_outcome() -> None:
    repository = Repository()
    newer_workflow, _, _ = workflow_for(payload("final", 21, 17), repository)
    newer_workflow.adapter.source.retrieved_at = NOW + timedelta(hours=1)
    newer = newer_workflow.run(event(), request={})
    older_workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    older_workflow.adapter.source.retrieved_at = NOW

    with pytest.raises(ValueError, match="older"):
        older_workflow.run(event(), request={})

    assert repository.outcomes[-1] is newer.outcome
    assert repository.outcomes[-1].outcome_version == 1


def test_outcome_capture_retains_canonical_manifest_lineage() -> None:
    workflow, _, _ = workflow_for(payload("final", 20, 17))

    run = workflow.run(event(), request={})

    assert run.capture_manifest.capture_id
    assert run.capture_manifest.raw_payload_sha256 == run.outcome.raw_payload_sha256
    assert run.capture_manifest.code_sha == "1" * 40
    assert run.capture_manifest.dependency_lock_sha256 == "2" * 64


def test_source_snapshot_timestamp_is_authoritative_over_later_retrieval() -> None:
    workflow, _, _ = workflow_for(payload("final", 20, 17))
    workflow.adapter.source.source_snapshot_at = NOW - timedelta(hours=1)
    workflow.adapter.source.retrieved_at = NOW + timedelta(hours=2)

    run = workflow.run(event(), request={})

    assert run.outcome.source_version_or_retrieved_at_utc == NOW - timedelta(hours=1)
    assert run.observation_time_basis == "source_snapshot"


def test_identical_later_receipt_advances_watermark_and_blocks_intermediate_correction() -> None:
    repository = Repository()
    first, _, _ = workflow_for(payload("final", 20, 17), repository)
    first.adapter.source.retrieved_at = NOW
    first.run(event(), request={})
    identical, _, _ = workflow_for(payload("final", 20, 17), repository)
    identical.adapter.source.retrieved_at = NOW + timedelta(hours=3)
    identical.run(event(), request={})
    conflicting, _, _ = workflow_for(payload("final", 21, 17), repository)
    conflicting.adapter.source.source_snapshot_at = NOW + timedelta(hours=2)
    conflicting.adapter.source.retrieved_at = NOW + timedelta(hours=4)

    with pytest.raises(ValueError, match="older"):
        conflicting.run(event(), request={})

    assert len(repository.outcomes) == 1
    assert repository.outcomes[0].home_score == 20


@pytest.mark.parametrize("offset", [-1, 0])
def test_stale_or_equal_time_conflicting_operator_evidence_cannot_supersede(offset: int) -> None:
    repository = Repository()
    repository.evidence_frozen_at = NOW + timedelta(hours=2)
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    workflow.run(event(), request={})
    repository.evidence_suffix = "-conflict"
    repository.evidence_frozen_at = NOW + timedelta(hours=2, minutes=offset)

    with pytest.raises(ValueError, match="evidence"):
        workflow.run(event(), request={})

    assert len(repository.envelopes) == 2


def test_evidence_cycle_has_unique_deterministic_settlement_version_chain() -> None:
    repository = Repository()
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    workflow.run(event(), request={})
    repository.evidence_suffix = "-B"
    repository.evidence_frozen_at = NOW + timedelta(minutes=1)
    workflow.run(event(), request={})
    repository.evidence_suffix = ""
    repository.evidence_frozen_at = NOW + timedelta(minutes=2)
    third = workflow.run(event(), request={})

    candidates = [
        item for item in repository.envelopes if item.settlement.candidate_id == "candidate-1"
    ]
    assert len({item.settlement.settlement_id for item in candidates}) == 3
    assert [item.settlement.settlement_version for item in candidates] == [1, 2, 3]
    assert (
        candidates[-1].settlement.supersedes_settlement_id
        == candidates[-2].settlement.settlement_id
    )
    returned = next(item for item in third.settlements if item.settlement.candidate_id)
    assert returned.settlement.settlement_id == candidates[-1].settlement.settlement_id


def test_durable_missing_operator_evidence_is_manual_review_not_synthesized_standard(
    tmp_path: Path,
) -> None:
    repository = durable_repository(tmp_path / "ledger")
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]

    run = workflow.run(event(), request={})

    assert len(run.settlements) == 1
    assert run.settlements[0].settlement.result == "unresolved"
    assert run.settlements[0].settlement.manual_review_required is True


@pytest.mark.parametrize(
    ("resolution", "expected_result", "manual_review"),
    [
        ("standard_confirmed", "win", False),
        ("void_confirmed", "void", False),
        ("conflict", "unresolved", True),
    ],
)
def test_durable_explicit_operator_evidence_round_trips_exact_resolution(
    tmp_path: Path,
    resolution: str,
    expected_result: str,
    manual_review: bool,
) -> None:
    root = tmp_path / resolution
    repository = durable_repository(root)
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    initial = workflow.run(event(), request={})
    explicit = evidence(
        initial.outcome,
        evidence_id=f"evidence-{resolution}",
        resolution=resolution,
        frozen_at=NOW + timedelta(minutes=1),
    )
    repository.register_operator_evidence(explicit)

    reloaded = outcome_repository(root)
    rerun = OutcomeWorkflow(workflow.adapter, reloaded).run(event(), request={})

    assert reloaded.operator_evidence(quote(), initial.outcome) == explicit
    assert rerun.settlements[0].settlement.result == expected_result
    assert rerun.settlements[0].settlement.manual_review_required is manual_review


def test_durable_watermark_orders_by_source_then_receipt_then_raw_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "watermarks"
    repository = outcome_repository(root)
    first = ObservationWatermark("event-1", "nflverse", NOW, NOW, "a" * 64)
    newer_same_receipt = ObservationWatermark(
        "event-1", "nflverse", NOW + timedelta(minutes=1), NOW, "b" * 64
    )
    repository.append_observation_watermark(first)
    repository.append_observation_watermark(newer_same_receipt)
    later_receipt_older_source = ObservationWatermark(
        "event-1",
        "nflverse",
        NOW - timedelta(minutes=1),
        NOW + timedelta(hours=1),
        "c" * 64,
    )
    repository.append_observation_watermark(later_receipt_older_source)

    reloaded = outcome_repository(root)
    assert reloaded.latest_observation_watermark("event-1", "nflverse") == newer_same_receipt


def test_durable_record_enumeration_rejects_marker_key_substitution(tmp_path: Path) -> None:
    root = tmp_path / "integrity"
    durable_repository(root)
    marker = next((root / "commits" / "workflow-forecast-graphs").glob("*.json"))
    value = json.loads(marker.read_text(encoding="utf-8"))
    value["idempotency_key"] = "candidate-substituted"
    marker.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(DataIntegrityError):
        outcome_repository(root).displayed_candidates("event-1")


def test_persisted_operator_evidence_ordering_is_safe_after_reload(tmp_path: Path) -> None:
    root = tmp_path / "evidence-order"
    repository = durable_repository(root)
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    initial = workflow.run(event(), request={})
    accepted = evidence(
        initial.outcome,
        evidence_id="evidence-A",
        resolution="standard_confirmed",
        frozen_at=NOW + timedelta(minutes=2),
    )
    repository.register_operator_evidence(accepted)
    OutcomeWorkflow(workflow.adapter, outcome_repository(root)).run(event(), request={})

    reloaded = outcome_repository(root)
    reloaded.register_operator_evidence(
        evidence(
            initial.outcome,
            evidence_id="evidence-stale",
            resolution="void_confirmed",
            frozen_at=NOW + timedelta(minutes=1),
        )
    )
    unchanged = OutcomeWorkflow(workflow.adapter, reloaded).run(event(), request={})
    assert unchanged.settlements == ()

    reloaded.register_operator_evidence(
        evidence(
            initial.outcome,
            evidence_id="evidence-Z-conflict",
            resolution="void_confirmed",
            frozen_at=NOW + timedelta(minutes=2),
        )
    )
    with pytest.raises(ValueError, match="equal-time conflicting"):
        OutcomeWorkflow(workflow.adapter, outcome_repository(root)).run(event(), request={})


def test_forecast_report_import_has_no_legacy_component_registration_escape(
    tmp_path: Path,
) -> None:
    repository = outcome_repository(tmp_path)

    assert not hasattr(repository, "register_forecast")


def test_preopen_obligation_then_terminal_graph_merges_to_one_denominator(
    tmp_path: Path,
) -> None:
    graph = forecast_graph()
    repository = outcome_repository(tmp_path, graph)
    source = persist_forecast_obligation(tmp_path / "forecast-source", graph.obligation)
    repository.import_forecast_obligation(source, graph.obligation.execution_key)
    assert repository.forecast_obligation_bundles() == (
        repository.forecast_obligation_bundles()[0],
    )
    assert repository.forecast_obligation_bundles()[0].run is None

    persist_forecast_graph(tmp_path / "forecast-source", graph)
    repository.import_forecast_graph(source, graph.obligation.execution_key)
    reloaded = outcome_repository(tmp_path, graph)
    bundles = reloaded.forecast_obligation_bundles()

    assert len(bundles) == 1
    assert bundles[0].obligation.execution_key == graph.obligation.execution_key
    assert bundles[0].run == graph.run


@pytest.mark.parametrize("corruption", ["gap", "tie", "dangling"])
def test_settlement_reload_rejects_non_contiguous_or_dangling_version_graph(
    tmp_path: Path,
    corruption: str,
) -> None:
    repository = durable_repository(tmp_path)
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    first = workflow.run(event(), request={}).settlements[0]
    first_settlement = first.settlement
    if corruption == "gap":
        version = 3
        supersedes = "not-the-prior-settlement"
        outcome_version = first.outcome_version
        evidence_id = first.operator_evidence_id
    elif corruption == "tie":
        version = 1
        supersedes = None
        outcome_version = first.outcome_version
        evidence_id = first.operator_evidence_id
    else:
        version = 2
        supersedes = first_settlement.settlement_id
        outcome_version = 99
        evidence_id = "missing-evidence"
    forged = first_settlement.model_copy(
        update={
            "settlement_id": f"forged-{corruption}",
            "settlement_version": version,
            "supersedes_settlement_id": supersedes,
        }
    )
    repository.ledger.append(
        repository._SETTLEMENTS,
        f"{forged.settlement_id}|{forged.settlement_version}",
        {
            "outcome_id": first.outcome_id,
            "outcome_version": outcome_version,
            "operator_evidence_id": evidence_id,
            "operator_evidence_frozen_at_utc": (first.operator_evidence_frozen_at_utc.isoformat()),
            "capture_id": first.capture_id,
            "raw_payload_sha256": first.raw_payload_sha256,
            "code_sha": first.code_sha,
            "dependency_lock_sha256": first.dependency_lock_sha256,
            "source": first.source,
            "settlement": forged.model_dump(mode="json"),
        },
    )

    with pytest.raises(DataIntegrityError):
        outcome_repository(tmp_path).latest_settlement_envelope(
            first_settlement.candidate_id,
            first_settlement.ticket_id,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capture_id", "forged-capture"),
        ("raw_payload_sha256", "0" * 64),
        ("code_sha", "0" * 40),
        ("dependency_lock_sha256", "0" * 64),
    ],
)
def test_outcome_observation_must_exactly_match_capture_manifest(field: str, value: str) -> None:
    repository = Repository()
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)
    original = workflow.adapter.capture

    def capture(request, run_id):
        result = original(request, run_id)
        return OutcomeCapture(result.manifest, replace(result.observation, **{field: value}))

    workflow.adapter.capture = capture

    with pytest.raises(ValueError, match="manifest"):
        workflow.run(event(), request={})
    assert repository.outcomes == []
    assert repository.envelopes == []


def test_durable_outcome_stream_rejects_forged_identity_and_version_before_visibility(
    tmp_path: Path,
) -> None:
    repository = outcome_repository(tmp_path)
    workflow, _, _ = workflow_for(payload("final", 20, 17), Repository())
    valid = workflow.run(event(), request={}).outcome
    forged = valid.model_copy(update={"outcome_id": "forged", "outcome_version": 7})

    with pytest.raises(DataIntegrityError):
        repository.append_outcome(forged)
    assert repository.latest_outcome(event().canonical_event_id, "nflverse") is None


def test_settlement_append_recomputes_truth_instead_of_accepting_arbitrary_profit(
    tmp_path: Path,
) -> None:
    repository = durable_repository(tmp_path)
    workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    initial = workflow.run(event(), request={}).settlements[0]
    prior = initial.settlement
    forged_settlement = prior.model_copy(
        update={
            "settlement_id": "forged-settlement",
            "settlement_version": 2,
            "supersedes_settlement_id": prior.settlement_id,
            "result": "win",
            "profit_units": Decimal(99),
            "settled_at_utc": NOW,
            "manual_review_required": False,
        }
    )
    forged = SettlementEnvelope(
        initial.outcome_id,
        initial.outcome_version,
        initial.operator_evidence_id,
        initial.operator_evidence_frozen_at_utc,
        initial.capture_id,
        initial.raw_payload_sha256,
        initial.code_sha,
        initial.dependency_lock_sha256,
        initial.source,
        forged_settlement,
    )

    with pytest.raises(DataIntegrityError):
        repository.append_settlement(forged)


def test_invalid_obligation_import_leaves_no_visibility_marker(tmp_path: Path) -> None:
    repository = outcome_repository(tmp_path)
    invalid = replace(forecast_graph().obligation, namespace="replay")

    with pytest.raises(TypeError):
        repository.import_forecast_obligation(invalid, invalid.execution_key)  # type: ignore[arg-type]
    assert repository.forecast_obligation_bundles() == ()


def test_outcome_admission_requires_a_persisted_capture_manifest(tmp_path: Path) -> None:
    repository = durable_repository(tmp_path)
    workflow, _, _ = workflow_for(payload("final", 20, 17), Repository())
    observed = workflow.run(event(), request={}).outcome
    watermark = ObservationWatermark(
        observed.canonical_event_id,
        observed.source,
        observed.source_version_or_retrieved_at_utc,
        observed.source_version_or_retrieved_at_utc,
        observed.raw_payload_sha256,
    )
    repository.append_observation_watermark(watermark)

    with pytest.raises(DataIntegrityError, match="capture"):
        repository.append_outcome(observed)

    assert repository.latest_outcome(observed.canonical_event_id, observed.source) is None
    assert repository.prediction_score_inputs(2026, 1)[0].outcome is None


def test_obsolete_outcome_cannot_supersede_newer_settlement_lineage(
    tmp_path: Path,
) -> None:
    repository = durable_repository(tmp_path)
    first_workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    first = first_workflow.run(event(), request={})
    corrected_workflow, _, _ = workflow_for(payload("final", 17, 21), repository)  # type: ignore[arg-type]
    corrected_workflow.adapter.source.retrieved_at = NOW + timedelta(minutes=1)
    corrected = corrected_workflow.run(event(), request={})
    stale_evidence = evidence(
        first.outcome,
        evidence_id="late-evidence-for-obsolete-outcome",
        resolution="standard_confirmed",
        frozen_at=NOW + timedelta(minutes=2),
    )
    repository.register_operator_evidence(stale_evidence)
    obsolete = settle_displayed_candidate(
        candidate(),
        quote(),
        first.outcome,
        stale_evidence,
    )

    with pytest.raises(ValueError, match="outcome"):
        first_workflow._versioned_envelope(
            obsolete,
            first.outcome,
            stale_evidence,
            first.capture_manifest,
        )

    latest = repository.latest_settlement_envelope(candidate().candidate_id, None)
    assert latest is not None
    assert latest.outcome_version == corrected.outcome.outcome_version


@pytest.mark.parametrize(
    ("operator", "accepted_at"),
    [
        ("wrong-book", T60_DECISION + timedelta(minutes=1)),
        ("book-a", KICKOFF + timedelta(seconds=1)),
    ],
)
def test_ticket_admission_rejects_wrong_operator_and_post_kickoff_poison(
    tmp_path: Path,
    operator: str,
    accepted_at: datetime,
) -> None:
    repository = durable_repository(tmp_path)
    ticket = ActualTicket(
        ticket_id=f"ticket-{operator}-{accepted_at.timestamp()}",
        candidate_id=candidate().candidate_id,
        model_attributed=True,
        operator=operator,
        jurisdiction="IL",
        accepted_at_utc=accepted_at,
        accepted_decimal_price=Decimal("1.95"),
        stake=Decimal(10),
        currency="USD",
    )

    with pytest.raises(ValueError, match="operator|accepted|kickoff"):
        repository.register_ticket(ticket)

    with pytest.raises(KeyError):
        repository.read_ticket(ticket.ticket_id)


def test_operator_evidence_id_is_globally_unique_across_outcome_versions(
    tmp_path: Path,
) -> None:
    repository = durable_repository(tmp_path)
    first_workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    first = first_workflow.run(event(), request={})
    corrected_workflow, _, _ = workflow_for(payload("final", 17, 21), repository)  # type: ignore[arg-type]
    corrected_workflow.adapter.source.retrieved_at = NOW + timedelta(minutes=1)
    corrected = corrected_workflow.run(event(), request={})
    shared_id = "globally-reused-evidence-id"
    repository.register_operator_evidence(
        evidence(
            first.outcome,
            evidence_id=shared_id,
            resolution="standard_confirmed",
            frozen_at=NOW + timedelta(minutes=2),
        )
    )

    with pytest.raises(ValueError, match="evidence.*ID|unique"):
        repository.register_operator_evidence(
            evidence(
                corrected.outcome,
                evidence_id=shared_id,
                resolution="standard_confirmed",
                frozen_at=NOW + timedelta(minutes=3),
            )
        )


def test_concurrent_operator_evidence_id_reuse_commits_exactly_one_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = durable_repository(tmp_path)
    first_workflow, _, _ = workflow_for(payload("final", 20, 17), repository)  # type: ignore[arg-type]
    first = first_workflow.run(event(), request={})
    corrected_workflow, _, _ = workflow_for(payload("final", 17, 21), repository)  # type: ignore[arg-type]
    corrected_workflow.adapter.source.retrieved_at = NOW + timedelta(minutes=1)
    corrected = corrected_workflow.run(event(), request={})
    shared_id = "concurrently-reused-evidence-id"
    proposed = (
        evidence(
            first.outcome,
            evidence_id=shared_id,
            resolution="standard_confirmed",
            frozen_at=NOW + timedelta(minutes=2),
        ),
        evidence(
            corrected.outcome,
            evidence_id=shared_id,
            resolution="standard_confirmed",
            frozen_at=NOW + timedelta(minutes=3),
        ),
    )
    evidence_directory = tmp_path / "commits" / repository._OPERATOR_EVIDENCE
    before = set(evidence_directory.glob("*.json"))
    append = repository.ledger.append
    barrier = threading.Barrier(2)

    def synchronized_append(namespace: str, key: str, record: object):
        if namespace == repository._OPERATOR_EVIDENCE:
            try:
                barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
        return append(namespace, key, record)

    monkeypatch.setattr(repository.ledger, "append", synchronized_append)

    def register(item: OperatorSettlementEvidence) -> Exception | None:
        try:
            repository.register_operator_evidence(item)
        except Exception as error:  # noqa: BLE001 - concurrent result is asserted below
            return error
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(register, proposed))

    after = set(evidence_directory.glob("*.json"))
    assert len(after - before) == 1
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    winner = proposed[results.index(None)]
    repository.register_operator_evidence(winner)
    assert set(evidence_directory.glob("*.json")) == after
    assert (
        len([item for item in repository._typed_evidence() if item.evidence_id == shared_id]) == 1
    )


def test_forecast_import_rejects_coherently_self_attested_artifact_lineage(
    tmp_path: Path,
) -> None:
    graph = forecast_graph()
    trusted = graph.obligation.champion_binding
    assert trusted is not None
    forged = replace(
        trusted,
        artifact_id="unregistered-artifact",
        calibrator_artifact_id="unregistered-calibrator",
    )
    forged_graph = replace(
        graph,
        obligation=replace(graph.obligation, champion_binding=forged),
        artifact_selections=(
            ArtifactSelection(
                graph.artifact_selections[0].attempt_id,
                graph.artifact_selections[0].origin_run_id,
                forged,
            ),
        ),
        predictions=tuple(
            item.model_copy(
                update={
                    "model_artifact_id": forged.artifact_id,
                    "calibrator_artifact_id": forged.calibrator_artifact_id,
                }
            )
            for item in graph.predictions
        ),
    )
    source = persist_forecast_graph(tmp_path / "forged-source", forged_graph)
    source.artifact_resolver = StaticArtifactResolver((forged,))
    repository = DurableOutcomeReportRepository(
        tmp_path / "outcomes",
        StaticArtifactResolver((trusted,)),
    )

    with pytest.raises(DataIntegrityError, match="artifact|trust"):
        repository.import_forecast_graph(source, graph.obligation.execution_key)

    assert repository.forecast_obligation_bundles() == ()


def test_outcome_repository_requires_independent_artifact_resolver_at_construction(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="artifact|resolver|registry"):
        DurableOutcomeReportRepository(tmp_path / "outcomes")  # type: ignore[call-arg]


def fractional_close_forecast_graph() -> CommittedForecastGraph:
    shifted_event = event().model_copy(
        update={"kickoff_at_utc": event().kickoff_at_utc + timedelta(microseconds=750_000)}
    )
    return retarget_forecast_graph(
        forecast_graph(),
        shifted_event,
        Origin.T60,
        "forecast-v1",
    )


@pytest.mark.parametrize(
    "stop_boundary",
    ["after_marker_link", "after_directory_fsync"],
)
def test_receipt_process_stop_rejects_coarse_after_close_link_and_recovers_digest_specific_miss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_boundary: str,
) -> None:
    graph = fractional_close_forecast_graph()
    source = prepare_forecast_graph(tmp_path / "forecast-source", graph)
    deadline = graph.obligation.window_closes_at_utc
    coarse_after_close_tick = deadline.replace(microsecond=0)
    assert coarse_after_close_tick < deadline
    receipt_marker: Path | None = None
    process_stopped = False
    real_link = forecast_module.os.link
    real_unlink = Path.unlink
    real_stat = Path.stat
    real_fsync_directory = forecast_module.LedgerStore._fsync_directory

    def stopping_link(source_path: object, destination: object) -> None:
        nonlocal process_stopped, receipt_marker
        target = Path(destination)  # type: ignore[arg-type]
        real_link(source_path, destination)
        if target.parent.name != source._terminal_receipt_namespace or receipt_marker is not None:
            return
        receipt_marker = target
        if stop_boundary == "after_marker_link":
            process_stopped = True
            raise OSError("simulated process stop after receipt marker link")

    def stopping_fsync_directory(directory: Path) -> None:
        nonlocal process_stopped
        real_fsync_directory(directory)
        if (
            stop_boundary == "after_directory_fsync"
            and not process_stopped
            and receipt_marker is not None
            and directory == receipt_marker.parent
        ):
            process_stopped = True
            raise OSError("simulated process stop after receipt directory fsync")

    def no_post_publication_cleanup(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if (
            receipt_marker is not None
            and path != receipt_marker
            and path.parent == receipt_marker.parent
        ):
            return
        real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    def coarse_receipt_metadata(path: Path, *args: object, **kwargs: object):
        result = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if receipt_marker is not None and path == receipt_marker:
            tick_ns = utc_nanoseconds(coarse_after_close_tick)
            return SimpleNamespace(
                st_ctime_ns=tick_ns,
                st_mtime_ns=tick_ns,
                st_nlink=result.st_nlink,
            )
        return result

    monkeypatch.setattr(forecast_module.os, "link", stopping_link)
    monkeypatch.setattr(
        forecast_module.LedgerStore,
        "_fsync_directory",
        staticmethod(stopping_fsync_directory),
    )
    monkeypatch.setattr(Path, "unlink", no_post_publication_cleanup)
    monkeypatch.setattr(Path, "stat", coarse_receipt_metadata)

    with pytest.raises(OSError, match="process stop after receipt"):
        source.commit_terminal(
            graph.obligation.execution_key,
            graph.run,
            graph.attempts[-1],
            deadline=deadline,
            context=ForecastExecutionContext.live(graph.obligation.target_at_utc),
            clock=lambda: graph.obligation.target_at_utc,
        )

    assert receipt_marker is not None and receipt_marker.exists()
    assert source.terminal_run(graph.obligation.execution_key) is None
    reloaded = DurableForecastRepository(
        tmp_path / "forecast-source",
        "prospective",
        artifact_resolver=artifact_resolver_for(graph),
    )
    assert reloaded.terminal_run(graph.obligation.execution_key) is None
    target = DurableOutcomeReportRepository(
        tmp_path / "outcomes",
        artifact_resolver_for(graph),
    )
    with pytest.raises(DataIntegrityError, match="terminal|committed"):
        target.import_forecast_graph(reloaded, graph.obligation.execution_key)
    assert target.ledger.read(target._FORECAST_GRAPHS, graph.run.origin_run_id) is None

    obligation_value = OriginObligation(
        graph.obligation.event,
        ForecastOrigin(
            origin=graph.obligation.origin,
            target_at_utc=graph.obligation.target_at_utc,
            window_opens_at_utc=graph.obligation.window_opens_at_utc,
            window_closes_at_utc=deadline,
        ),
        graph.obligation.forecast_policy_version,
    )
    recovered = reloaded.recover_prepared_terminal(
        graph.obligation.execution_key,
        obligation_value,
        ForecastExecutionContext.live(graph.obligation.target_at_utc),
        deadline=deadline,
        clock=lambda: deadline + timedelta(seconds=1),
    )

    assert recovered is not None and recovered.status is RunStatus.MISSED
    assert recovered.prediction_ids == []
    assert reloaded.terminal_run(graph.obligation.execution_key) == recovered
    target.import_forecast_graph(reloaded, graph.obligation.execution_key)
    assert target.load_forecast_graph(recovered.origin_run_id).run == recovered


def test_pre_close_receipt_link_metadata_is_immutable_and_graph_stays_importable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph = forecast_graph()
    source = prepare_forecast_graph(tmp_path / "forecast-source", graph)
    real_link = forecast_module.os.link
    linked_marker: Path | None = None
    link_metadata: tuple[int, int] | None = None

    def recording_link(source_path: object, destination: object) -> None:
        nonlocal linked_marker, link_metadata
        target = Path(destination)  # type: ignore[arg-type]
        real_link(source_path, destination)
        if target.parent.name == source._terminal_receipt_namespace:
            linked_marker = target
            metadata = target.stat()
            link_metadata = metadata.st_ctime_ns, metadata.st_nlink

    monkeypatch.setattr(forecast_module.os, "link", recording_link)

    published = source.commit_terminal(
        graph.obligation.execution_key,
        graph.run,
        graph.attempts[-1],
        deadline=graph.obligation.window_closes_at_utc,
        context=ForecastExecutionContext.live(graph.obligation.target_at_utc),
        clock=lambda: graph.obligation.target_at_utc,
    )

    assert published == graph.run
    assert linked_marker is not None and link_metadata is not None
    final_metadata = linked_marker.stat()
    assert (final_metadata.st_ctime_ns, final_metadata.st_nlink) == link_metadata
    assert source.terminal_run(graph.obligation.execution_key) == graph.run
    reloaded = DurableForecastRepository(
        tmp_path / "forecast-source",
        "prospective",
        artifact_resolver=artifact_resolver_for(graph),
    )
    assert reloaded.load_committed_graph(graph.obligation.execution_key) == graph
    target = DurableOutcomeReportRepository(
        tmp_path / "outcomes",
        artifact_resolver_for(graph),
    )
    target.import_forecast_graph(reloaded, graph.obligation.execution_key)
    assert target.load_forecast_graph(graph.run.origin_run_id) == graph


@pytest.mark.parametrize(
    "metadata_case",
    ["coarse_prior_tick", "backdated_after_mutation"],
)
def test_receipt_metadata_must_fail_closed_for_live_reload_and_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_case: str,
) -> None:
    graph = (
        fractional_close_forecast_graph()
        if metadata_case == "coarse_prior_tick"
        else forecast_graph()
    )
    source = persist_forecast_graph(tmp_path / "forecast-source", graph)
    assert source.terminal_run(graph.obligation.execution_key) == graph.run
    marker = next(
        (tmp_path / "forecast-source" / "commits" / source._terminal_receipt_namespace).glob(
            "*.json"
        )
    )
    deadline = graph.obligation.window_closes_at_utc
    if metadata_case == "coarse_prior_tick":
        after_close = deadline + timedelta(microseconds=100_000)
        prior_tick = after_close.replace(microsecond=0)
        assert prior_tick < deadline < after_close
        mtime_ns = utc_nanoseconds(prior_tick)
        ctime_ns = utc_nanoseconds(prior_tick)
    else:
        backdated = deadline - timedelta(seconds=1)
        mtime_ns = utc_nanoseconds(backdated)
        os.utime(marker, ns=(mtime_ns, mtime_ns), follow_symlinks=False)
        ctime_ns = utc_nanoseconds(deadline + timedelta(seconds=1))
    real_stat = Path.stat

    def simulated_receipt_metadata(path: Path, *args: object, **kwargs: object):
        result = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if path == marker:
            return SimpleNamespace(st_mtime_ns=mtime_ns, st_ctime_ns=ctime_ns)
        return result

    monkeypatch.setattr(Path, "stat", simulated_receipt_metadata)

    assert source.terminal_run(graph.obligation.execution_key) is None
    reloaded = DurableForecastRepository(
        tmp_path / "forecast-source",
        "prospective",
        artifact_resolver=artifact_resolver_for(graph),
    )
    assert reloaded.terminal_run(graph.obligation.execution_key) is None
    target = DurableOutcomeReportRepository(
        tmp_path / "outcomes",
        artifact_resolver_for(graph),
    )
    with pytest.raises(DataIntegrityError, match="terminal|committed"):
        target.import_forecast_graph(reloaded, graph.obligation.execution_key)
    assert target.ledger.read(target._FORECAST_GRAPHS, graph.run.origin_run_id) is None


@pytest.mark.parametrize("boundary", ["import", "reload"])
def test_ambiguous_independent_champions_cannot_select_graph_champion(
    tmp_path: Path,
    boundary: str,
) -> None:
    graph = forecast_graph()
    champion = graph.obligation.champion_binding
    assert champion is not None
    ambiguous = StaticArtifactResolver(
        (
            champion,
            replace(
                champion,
                artifact_id="second-frozen-champion",
                calibrator_artifact_id="second-frozen-calibrator",
            ),
        )
    )
    source = persist_forecast_graph(tmp_path / "forecast-source", graph)
    repository = DurableOutcomeReportRepository(tmp_path / "outcomes", ambiguous)

    if boundary == "reload":
        trusted = DurableOutcomeReportRepository(
            tmp_path / "outcomes",
            StaticArtifactResolver((champion,)),
        )
        trusted.import_forecast_graph(source, graph.obligation.execution_key)
        with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
            repository.load_forecast_graph(graph.run.origin_run_id)
        return

    with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
        repository.import_forecast_graph(source, graph.obligation.execution_key)

    assert repository.ledger.read(repository._FORECAST_GRAPHS, graph.run.origin_run_id) is None


@pytest.mark.parametrize("boundary", ["validation", "import", "reload"])
@pytest.mark.parametrize("registry_case", ["zero", "multiple", "wrong_origin", "unverified"])
@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.MISSED])
def test_championless_terminal_requires_one_verified_registry_champion(
    tmp_path: Path,
    boundary: str,
    registry_case: str,
    status: RunStatus,
) -> None:
    graph = championless_terminal_graph(status)
    champion = forecast_graph().obligation.champion_binding
    assert champion is not None
    invalid = invalid_champion_resolver(registry_case, champion)
    if boundary == "validation":
        with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
            validate_committed_graph(graph, artifact_resolver=invalid)
        return

    source = persist_forecast_graph(
        tmp_path / "forecast-source",
        graph,
        trusted_bindings=(champion,),
    )
    repository = DurableOutcomeReportRepository(tmp_path / "outcomes", invalid)
    if boundary == "import":
        with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
            repository.import_forecast_graph(source, graph.obligation.execution_key)
        assert repository.ledger.read(repository._FORECAST_GRAPHS, graph.run.origin_run_id) is None
        return

    trusted = DurableOutcomeReportRepository(
        tmp_path / "outcomes",
        StaticArtifactResolver((champion,)),
    )
    trusted.import_forecast_graph(source, graph.obligation.execution_key)
    with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
        repository.load_forecast_graph(graph.run.origin_run_id)


@pytest.mark.parametrize("boundary", ["validation", "import", "reload"])
@pytest.mark.parametrize("registry_case", ["zero", "multiple", "wrong_origin", "unverified"])
def test_championless_obligation_requires_one_verified_registry_champion(
    tmp_path: Path,
    boundary: str,
    registry_case: str,
) -> None:
    graph = forecast_graph()
    champion = graph.obligation.champion_binding
    assert champion is not None
    obligation = replace(graph.obligation, champion_binding=None)
    invalid = invalid_champion_resolver(registry_case, champion)
    if boundary == "validation":
        with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
            validate_obligation_artifact_trust(obligation, invalid)
        return

    source = persist_forecast_obligation(tmp_path / "forecast-source", obligation)
    repository = DurableOutcomeReportRepository(tmp_path / "outcomes", invalid)
    if boundary == "import":
        with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
            repository.import_forecast_obligation(source, obligation.execution_key)
        assert (
            repository.ledger.read(repository._FORECAST_OBLIGATIONS, obligation.execution_key)
            is None
        )
        return

    trusted = DurableOutcomeReportRepository(
        tmp_path / "outcomes",
        StaticArtifactResolver((champion,)),
    )
    trusted.import_forecast_obligation(source, obligation.execution_key)
    with pytest.raises(DataIntegrityError, match="champion|ambiguous|trust"):
        repository.forecast_obligation_bundles()


def test_schedule_flex_keeps_origin_versions_and_resolves_latest_official_event(
    tmp_path: Path,
) -> None:
    version_one = event()
    version_two = event().model_copy(
        update={
            "event_version": 2,
            "kickoff_at_utc": event().kickoff_at_utc + timedelta(hours=1),
            "observed_at_utc": NOW - timedelta(days=2),
            "available_at_utc": NOW - timedelta(days=2),
            "captured_at_utc": NOW - timedelta(days=2),
            "raw_payload_sha256": "b" * 64,
        }
    )
    t72 = retarget_forecast_graph(
        forecast_graph(),
        version_one,
        Origin.T72,
        "forecast-flex-v1",
    )
    t60 = retarget_forecast_graph(
        forecast_graph(),
        version_two,
        Origin.T60,
        "forecast-flex-v2",
    )
    repository = outcome_repository(tmp_path, t72, t60)
    source = persist_forecast_graph(tmp_path / "forecast-source", t72)
    persist_forecast_graph(tmp_path / "forecast-source", t60)
    source.artifact_resolver = artifact_resolver_for(t72, t60)
    repository.import_forecast_graph(source, t72.obligation.execution_key)
    repository.import_forecast_graph(source, t60.obligation.execution_key)

    assert repository._event(version_one.canonical_event_id) == version_two
    imported = repository.forecast_obligation_bundles()
    assert {(row.obligation.origin, row.obligation.event.event_version) for row in imported} == {
        (Origin.T72, 1),
        (Origin.T60, 2),
    }


def test_schedule_flex_uses_newer_obligation_only_event_and_rejects_version_conflict(
    tmp_path: Path,
) -> None:
    version_one = event()
    version_two = event().model_copy(
        update={
            "event_version": 2,
            "kickoff_at_utc": event().kickoff_at_utc + timedelta(hours=1),
            "observed_at_utc": NOW - timedelta(days=2),
            "available_at_utc": NOW - timedelta(days=2),
            "captured_at_utc": NOW - timedelta(days=2),
            "raw_payload_sha256": "b" * 64,
        }
    )
    t72 = retarget_forecast_graph(
        forecast_graph(),
        version_one,
        Origin.T72,
        "forecast-flex-terminal-v1",
    )
    t60_missing = retarget_forecast_graph(
        forecast_graph(),
        version_two,
        Origin.T60,
        "forecast-flex-obligation-v2",
    )
    repository = outcome_repository(tmp_path, t72, t60_missing)
    source = persist_forecast_graph(tmp_path / "forecast-source", t72)
    persist_forecast_obligation(tmp_path / "forecast-source", t60_missing.obligation)
    repository.import_forecast_graph(source, t72.obligation.execution_key)
    repository.import_forecast_obligation(source, t60_missing.obligation.execution_key)

    assert repository._event(version_one.canonical_event_id) == version_two
    assert {
        (bundle.obligation.event.event_version, None if bundle.run is None else bundle.run.status)
        for bundle in repository.forecast_obligation_bundles()
    } == {(1, RunStatus.COMPLETE), (2, None)}

    conflicting_version_two = version_two.model_copy(
        update={
            "kickoff_at_utc": version_two.kickoff_at_utc + timedelta(hours=1),
            "raw_payload_sha256": "c" * 64,
        }
    )
    conflict = retarget_forecast_graph(
        forecast_graph(),
        conflicting_version_two,
        Origin.T60,
        "forecast-flex-conflict-v2",
    )
    persist_forecast_obligation(tmp_path / "forecast-source", conflict.obligation)
    repository.import_forecast_obligation(source, conflict.obligation.execution_key)

    with pytest.raises(DataIntegrityError, match="conflicting.*event version|schedule"):
        repository._event(version_one.canonical_event_id)


def test_missing_obligation_inherits_frozen_champion_lineage_for_coverage(
    tmp_path: Path,
) -> None:
    successful = forecast_graph()
    missing_event = event().model_copy(
        update={
            "canonical_event_id": "event-2",
            "source_event_ids": {"nflverse": "2026_01_OTHER_GAME"},
            "home_team": "DET",
            "away_team": "MIN",
            "raw_payload_sha256": "c" * 64,
        }
    )
    missing = missed_forecast_graph(
        retarget_forecast_graph(
            forecast_graph(),
            missing_event,
            Origin.T60,
            "forecast-v1",
        )
    )
    repository = outcome_repository(tmp_path, successful, missing)
    source = persist_forecast_graph(tmp_path / "forecast-source", successful)
    persist_forecast_graph(tmp_path / "forecast-source", missing)
    repository.import_forecast_graph(source, successful.obligation.execution_key)
    repository.import_forecast_graph(source, missing.obligation.execution_key)

    obligations = repository.forecast_obligation_inputs(2026, 1)

    assert {
        (row.canonical_event_id, row.artifact_id) for row in obligations if row.origin is Origin.T60
    } == {
        ("event-1", "artifact-1"),
        ("event-2", "artifact-1"),
    }
