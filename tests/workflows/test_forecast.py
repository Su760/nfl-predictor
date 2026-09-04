from __future__ import annotations

import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from nfl_predictor.contracts.attempts import RunAttempt
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import (
    Origin,
    PredictionStatus,
    ProvenanceGrade,
    SnapshotStatus,
)
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.lineage import CaptureManifest, FeatureSnapshot, NormalizedFact
from nfl_predictor.contracts.markets import (
    DisplayedQuoteCandidate,
    MarketComparator,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.markets.budget import BudgetExceeded
from nfl_predictor.markets.comparator import median_market_home
from nfl_predictor.storage.ledger import DataIntegrityError
from nfl_predictor.workflows import forecast as forecast_module
from nfl_predictor.workflows.forecast import (
    ArtifactBinding,
    CaptureBundle,
    ForecastExecutionContext,
    ForecastRepositories,
    ForecastWorkflow,
    MarketCaptureStale,
    MarketEvaluation,
    MarketSchemaDrift,
)

KICKOFF = datetime(2026, 9, 13, 20, 25, tzinfo=UTC)
CODE_SHA = "1" * 40


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self.value

    def set(self, value: datetime) -> None:
        with self._lock:
            self.value = value


def obligation() -> OriginObligation:
    event = EventVersion(
        canonical_event_id="2026_REG_01_GB_CHI",
        event_version=1,
        source_event_ids={"nflverse": "2026_01_GB_CHI"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=KICKOFF,
        neutral_site=False,
        observed_at_utc=KICKOFF - timedelta(days=10),
        available_at_utc=KICKOFF - timedelta(days=10),
        captured_at_utc=KICKOFF - timedelta(days=10),
        raw_payload_sha256="a" * 64,
    )
    return OriginObligation(
        event,
        ForecastOrigin.for_kickoff(Origin.T60, KICKOFF),
        "forecast-v1",
    )


class Repository:
    def __init__(self, clock: MutableClock | None = None, namespace: str = "prospective") -> None:
        self.namespace = namespace
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.terminals: dict[str, object] = {}
        self.records: list[tuple[str, object]] = []
        self.fail_terminal_once = False
        self.clock = clock
        self.advance_on_kind: str | None = None
        self.advance_to: datetime | None = None
        self.obligations: list[tuple[str, OriginObligation, ForecastExecutionContext]] = []
        self.obligation_records: dict[str, forecast_module.ForecastObligationRecord] = {}

    @property
    def storage_identity(self) -> str:
        return f"memory:{id(self)}"

    def transact(self, key: str, operation):
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            return operation()

    def terminal_run(self, key: str):
        return self.terminals.get(key)

    def validated_terminal_run(self, key, obligation, context):
        return self.terminal_run(key)

    def recover_prepared_terminal(self, key, obligation, context, *, deadline, clock):
        return None

    def load_obligation(self, key: str):
        return self.obligation_records.get(key)

    def register_obligation(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
        champion_binding: ArtifactBinding | None,
    ) -> None:
        value = (key, obligation, context)
        if value not in self.obligations:
            self.obligations.append(value)
        self.obligation_records[key] = forecast_module.DurableForecastRepository._obligation_record(
            key,
            obligation,
            context,
            champion_binding,
        )

    def latest_attempt(self, origin_run_id: str):
        attempts = [
            value
            for kind, value in self.records
            if kind == "attempt" and value.origin_run_id == origin_run_id
        ]
        return attempts[-1] if attempts else None

    def append_attempt(self, attempt) -> None:
        self.records.append(("attempt", attempt))

    def append_capture_record(self, record: object) -> None:
        self.records.append(("capture", record))

    def append_artifact_selection(self, selection: object) -> None:
        self.records.append(("artifact_selection", selection))

    def append_feature_snapshot(self, snapshot: FeatureSnapshot) -> None:
        self.records.append(("snapshot", snapshot))
        self._advance("snapshot")

    def append_prediction(self, prediction: Prediction) -> None:
        self.records.append(("prediction", prediction))
        self._advance("prediction")

    def append_market_comparator(self, comparator: object) -> None:
        self.records.append(("comparator", comparator))

    def append_market_validation(self, binding: object) -> None:
        self.records.append(("market_validation", binding))

    def append_betting_decision(self, decision: object) -> None:
        self.records.append(("decision", decision))

    def append_candidate(self, candidate: object) -> None:
        self.records.append(("candidate", candidate))

    def append_origin_run(self, key: str, run) -> None:
        if self.fail_terminal_once:
            self.fail_terminal_once = False
            raise OSError("simulated marker failure")
        self._advance("origin_run")
        self.records.append(("origin_run", run))
        self.terminals[key] = run

    def commit_terminal(self, key, run, attempt, *, deadline, context, clock):
        if self.fail_terminal_once:
            self.fail_terminal_once = False
            raise OSError("simulated marker failure")
        self._advance("origin_run")
        committed_at = clock()
        if context.mode == "live" and committed_at > deadline:
            run = run.model_copy(
                update={
                    "decision_at_utc": None,
                    "status": "MISSED",
                    "prediction_ids": [],
                    "market_comparator_id": None,
                    "reason_codes": ["EXECUTION_CROSSED_WINDOW"],
                    "created_at_utc": committed_at,
                }
            )
            attempt = attempt.model_copy(
                update={
                    "ended_at_utc": committed_at,
                    "status": "FAILED",
                    "safe_error_category": "EXECUTION_CROSSED_WINDOW",
                }
            )
        self.records.append(("attempt", attempt))
        self.records.append(("origin_run", run))
        self.terminals[key] = run
        return run

    def _advance(self, kind: str) -> None:
        if self.advance_on_kind == kind and self.clock is not None and self.advance_to is not None:
            self.clock.set(self.advance_to)


class RequiredCapture:
    def __init__(
        self,
        clock: MutableClock,
        received_at: datetime,
        manifest: CaptureManifest,
        fact: NormalizedFact,
    ) -> None:
        self.clock = clock
        self.received_at = received_at
        self.calls = 0
        self.archive_calls = 0
        self.manifest = manifest
        self.fact = fact
        self.keep_wrong_run_id = False

    def capture_live(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        self.calls += 1
        self.clock.set(self.received_at)
        if not self.keep_wrong_run_id:
            self.manifest = self.manifest.model_copy(update={"run_id": attempt_id})
        return CaptureBundle(records=(self.manifest, self.fact), payload={"football": True})

    capture = capture_live

    def capture_replay(
        self, obligation: OriginObligation, attempt_id: str, cutoff: datetime
    ) -> CaptureBundle:
        self.archive_calls += 1
        if not self.keep_wrong_run_id:
            self.manifest = self.manifest.model_copy(update={"run_id": attempt_id})
        return CaptureBundle(records=(self.manifest, self.fact), payload={"football": True})


class OptionalCapture:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.calls = 0
        self.manifest: CaptureManifest | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"

    def capture(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        self.calls += 1
        errors = {
            "budget": BudgetExceeded("2026-09"),
            "stale": MarketCaptureStale("stale"),
            "schema": MarketSchemaDrift("schema"),
        }
        if self.mode in errors:
            raise errors[self.mode]
        manifest = CaptureManifest(
            capture_id="market-capture-1",
            run_id=attempt_id,
            source="fixture-market",
            request_fingerprint="fixture-market",
            request_started_at_utc=obligation.window.target_at_utc - timedelta(seconds=2),
            response_received_at_utc=obligation.window.target_at_utc,
            http_status=200,
            raw_path="raw/market.bin",
            raw_payload_sha256="f" * 64,
            response_headers_allowlisted={},
            code_sha=CODE_SHA,
            dependency_lock_sha256="e" * 64,
            schema_version="capture-v1",
        )
        self.manifest = manifest
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
                    side="home",
                    team=obligation.event.home_team,
                    decimal_price=Decimal(2),
                    raw_pointer="$[0]",
                ),
                MoneylineSelection(
                    side="away",
                    team=obligation.event.away_team,
                    decimal_price=Decimal("1.9"),
                    raw_pointer="$[1]",
                ),
            ),
            overtime_included=True,
            tie_handling="push",
            market_semantics_version="v1",
            settlement_policy_url="https://example.test/rules",
            settlement_policy_version="v1",
            provenance_grade=ProvenanceGrade.A,
        )
        return CaptureBundle(records=(manifest, quote), payload={"odds": True})


class FeatureBuilder:
    def __init__(self, fact: NormalizedFact) -> None:
        self.fact = fact
        self.cutoffs: list[datetime] = []
        self.changes: dict[str, object] = {}
        self.advance_to: datetime | None = None
        self.clock: MutableClock | None = None
        self.fail = False

    def build(self, event, origin, cutoff, mode):
        self.cutoffs.append(cutoff)
        if self.advance_to is not None and self.clock is not None:
            self.clock.set(self.advance_to)
        if self.fail:
            raise RuntimeError("feature failed")
        snapshot = FeatureSnapshot(
            snapshot_id=f"snapshot-{len(self.cutoffs)}",
            canonical_event_id=event.canonical_event_id,
            event_version=event.event_version,
            origin=origin,
            decision_at_utc=cutoff,
            feature_policy_version="features-v1",
            feature_schema_version="schema-v1",
            values={"strength": 0.25},
            input_manifest_ids=[self.fact.capture_id],
            input_fact_ids=[self.fact.fact_id],
            join_policy_version="asof-v1",
            provenance_grade=(ProvenanceGrade.A if mode == "live" else ProvenanceGrade.C),
            feature_vector_sha256="b" * 64,
            status=SnapshotStatus.COMPLETE,
            reason_codes=[],
        )
        return snapshot.model_copy(update=self.changes)


class Facts:
    def __init__(
        self,
        fact: NormalizedFact,
        required: RequiredCapture,
        optional: OptionalCapture | None = None,
    ) -> None:
        self.fact = fact
        self.required = required
        self.optional = optional
        self.missing_manifest = False

    @property
    def manifest(self) -> CaptureManifest:
        return self.required.manifest

    @manifest.setter
    def manifest(self, value: CaptureManifest) -> None:
        self.required.manifest = value

    def resolve(self, fact_ids: list[str]) -> list[NormalizedFact]:
        assert fact_ids == [self.fact.fact_id]
        return [self.fact]

    def resolve_facts(self, fact_ids: list[str]) -> list[NormalizedFact]:
        return self.resolve(fact_ids)

    def resolve_manifests(self, manifest_ids: list[str]) -> list[CaptureManifest]:
        if self.missing_manifest:
            return []
        available = {self.required.manifest.capture_id: self.required.manifest}
        if self.optional is not None and self.optional.manifest is not None:
            available[self.optional.manifest.capture_id] = self.optional.manifest
        return [available[item] for item in manifest_ids if item in available]


class Registry:
    def __init__(self, bindings: tuple[ArtifactBinding, ...]) -> None:
        self.bindings = bindings
        self.clock: MutableClock | None = None
        self.advance_to: datetime | None = None

    def for_origin(self, origin: Origin) -> tuple[ArtifactBinding, ...]:
        if self.clock is not None and self.advance_to is not None:
            self.clock.set(self.advance_to)
        return self.bindings


class Predictor:
    def __init__(self, fail_artifacts: set[str] | None = None) -> None:
        self.fail_artifacts = fail_artifacts or set()
        self.calls: list[str] = []
        self.changes: dict[str, object] = {}
        self.advance_to: datetime | None = None
        self.clock: MutableClock | None = None
        self.advance_by_artifact: dict[str, datetime] = {}
        self.prediction_ids_by_artifact: dict[str, str] = {}

    def predict(self, snapshot, artifact, *, origin_run_id, obligation, created_at_utc, context):
        self.calls.append(artifact.artifact_id)
        if self.clock is not None and artifact.artifact_id in self.advance_by_artifact:
            self.clock.set(self.advance_by_artifact[artifact.artifact_id])
        if self.advance_to is not None and self.clock is not None:
            self.clock.set(self.advance_to)
        if artifact.artifact_id in self.fail_artifacts:
            raise RuntimeError("artifact failed")
        role = artifact.model_role
        prediction = Prediction(
            prediction_id=self.prediction_ids_by_artifact.get(
                artifact.artifact_id, f"prediction-{artifact.artifact_id}"
            ),
            origin_run_id=origin_run_id,
            canonical_event_id=obligation.event.canonical_event_id,
            event_version=obligation.event.event_version,
            origin=obligation.origin,
            target_at_utc=obligation.window.target_at_utc,
            decision_at_utc=snapshot.decision_at_utc,
            model_lane="football_only",
            model_role=role,
            model_artifact_id=artifact.artifact_id,
            calibrator_artifact_id=f"calibrator-{artifact.artifact_id}",
            feature_snapshot_id=snapshot.snapshot_id,
            market_snapshot_id=None,
            p_home=Decimal("0.55"),
            p_away=Decimal("0.43"),
            p_tie=Decimal("0.02"),
            predicted_winner="home",
            provenance_grade=context.provenance_grade,
            status=PredictionStatus.COMPLETE,
            reason_codes=(
                [] if context.reconstruction_reason is None else [context.reconstruction_reason]
            ),
            code_sha=CODE_SHA,
            policy_versions={"forecast": obligation.policy_version},
            created_at_utc=created_at_utc,
        )
        return prediction.model_copy(update=self.changes)


class MarketLayer:
    def __init__(self) -> None:
        self.prediction_ids: list[str] = []
        self.fail = False
        self.advance_to: datetime | None = None
        self.clock: MutableClock | None = None

    def evaluate(self, prediction, market_payload, decision_at_utc) -> MarketEvaluation:
        self.prediction_ids.append(prediction.prediction_id)
        if self.advance_to is not None and self.clock is not None:
            self.clock.set(self.advance_to)
        if self.fail:
            raise RuntimeError("market evaluation failed")
        return MarketEvaluation(comparator=None, decisions=())


_DEFAULT_CONTEXT = object()


class LiveWorkflowHarness:
    def __init__(self, workflow: ForecastWorkflow) -> None:
        self._workflow = workflow

    def __getattr__(self, name: str):
        return getattr(self._workflow, name)

    def run(self, obligation, trigger, context=_DEFAULT_CONTEXT):
        selected = (
            ForecastExecutionContext.live(obligation.window.window_opens_at_utc)
            if context is _DEFAULT_CONTEXT
            else context
        )
        return self._workflow.run(obligation, trigger, selected)


def build_workflow(
    *,
    optional_mode: str = "ok",
    received_at: datetime | None = None,
    bindings: tuple[ArtifactBinding, ...] | None = None,
    fail_artifacts: set[str] | None = None,
):
    item = obligation()
    received = received_at or item.window.target_at_utc
    clock = MutableClock(item.window.window_opens_at_utc)
    fact = NormalizedFact(
        fact_id="fact-1",
        capture_id="capture-1",
        fact_type="team_strength_prior",
        entity_keys={"team": "CHI"},
        payload={"strength": 0.25},
        raw_pointer="$[0]",
        available_at_utc=received - timedelta(seconds=1),
        captured_at_utc=received - timedelta(seconds=1),
        provenance_grade=ProvenanceGrade.A,
        normalization_schema_version="v1",
        fact_content_sha256="c" * 64,
    )
    manifest = CaptureManifest(
        capture_id="capture-1",
        run_id="fixture-run",
        source="fixture",
        request_fingerprint="fixture",
        request_started_at_utc=received - timedelta(seconds=2),
        response_received_at_utc=received - timedelta(seconds=1),
        http_status=200,
        raw_path="raw/fixture.bin",
        raw_payload_sha256="d" * 64,
        response_headers_allowlisted={},
        code_sha=CODE_SHA,
        dependency_lock_sha256="e" * 64,
        schema_version="capture-v1",
    )
    repository = Repository(clock)
    replay_repository = Repository(clock, namespace="replay")
    required = RequiredCapture(clock, received, manifest, fact)
    optional = OptionalCapture(optional_mode)
    feature_builder = FeatureBuilder(fact)
    feature_builder.clock = clock
    predictor = Predictor(fail_artifacts)
    predictor.clock = clock
    market = MarketLayer()
    market.clock = clock
    selected = (
        (ArtifactBinding("champion-a", Origin.T60, "champion", frozen=True, verified=True),)
        if bindings is None
        else bindings
    )
    registry = Registry(selected)
    registry.clock = clock
    workflow = LiveWorkflowHarness(
        ForecastWorkflow(
            repositories=ForecastRepositories(repository, replay_repository, allow_ephemeral=True),
            required_capture=required,
            market_capture=optional,
            feature_builder=feature_builder,
            lineage_repository=Facts(fact, required, optional),
            artifact_registry=registry,
            predictor=predictor,
            market_layer=market,
            clock=clock,
            code_sha=CODE_SHA,
        )
    )
    return (
        item,
        clock,
        repository,
        required,
        optional,
        feature_builder,
        predictor,
        market,
        workflow,
    )


def test_decision_cutoff_freezes_after_capture_and_resolves_only_eligible_facts() -> None:
    item, _, repository, _, _, builder, _, _, workflow = build_workflow()

    run = workflow.run(item, trigger="fixture")
    prediction = next(value for kind, value in repository.records if kind == "prediction")
    snapshot = next(value for kind, value in repository.records if kind == "snapshot")

    assert run.decision_at_utc == item.window.target_at_utc
    assert builder.cutoffs == [run.decision_at_utc]
    assert prediction.decision_at_utc == run.decision_at_utc
    assert snapshot.decision_at_utc == run.decision_at_utc


@pytest.mark.parametrize("mode", ["disabled", "budget", "stale", "schema"])
def test_optional_market_failures_keep_the_football_prediction(mode: str) -> None:
    item, _, _, _, optional, _, _, _, workflow = build_workflow(optional_mode=mode)

    run = workflow.run(item, trigger="fixture")

    assert run.status == "FOOTBALL_ONLY"
    assert len(run.prediction_ids) == 1
    assert run.market_comparator_id is None
    assert optional.calls == (0 if mode == "disabled" else 1)


def test_capture_finishing_after_close_is_missed_without_feature_or_prediction() -> None:
    item = obligation()
    values = build_workflow(received_at=item.window.window_closes_at_utc + timedelta(seconds=1))
    _, _, repository, _, optional, builder, predictor, _, workflow = values

    run = workflow.run(item, trigger="fixture")

    assert run.status == "MISSED"
    assert run.prediction_ids == []
    assert builder.cutoffs == []
    assert predictor.calls == []
    assert optional.calls == 0
    assert any(kind == "capture" for kind, _ in repository.records)


def test_closed_window_is_missed_without_capture() -> None:
    item, clock, _, required, _, _, _, _, workflow = build_workflow()
    clock.set(item.window.window_closes_at_utc + timedelta(seconds=1))

    run = workflow.run(item, trigger="fixture")

    assert run.status == "MISSED"
    assert run.prediction_ids == []
    assert required.calls == 0


def test_exact_champion_is_required_and_challenger_failure_is_isolated() -> None:
    bindings = (
        ArtifactBinding("champion-a", Origin.T60, "champion", frozen=True, verified=True),
        ArtifactBinding("challenger-b", Origin.T60, "challenger", frozen=True, verified=True),
        ArtifactBinding("challenger-c", Origin.T60, "challenger", frozen=True, verified=True),
    )
    item, _, _, _, _, _, predictor, market, workflow = build_workflow(
        bindings=bindings, fail_artifacts={"challenger-b"}
    )

    run = workflow.run(item, trigger="fixture")

    assert run.status == "FOOTBALL_ONLY"
    assert run.prediction_ids == ["prediction-champion-a", "prediction-challenger-c"]
    assert predictor.calls == ["champion-a", "challenger-b", "challenger-c"]
    assert market.prediction_ids == ["prediction-champion-a"]


@pytest.mark.parametrize(
    "bindings",
    [
        (),
        (
            ArtifactBinding("one", Origin.T60, "champion", frozen=True, verified=True),
            ArtifactBinding("two", Origin.T60, "champion", frozen=True, verified=True),
        ),
        (ArtifactBinding("one", Origin.T72, "champion", frozen=True, verified=True),),
        (ArtifactBinding("one", Origin.T60, "champion", frozen=False, verified=True),),
    ],
)
def test_invalid_champion_registry_fails_closed(bindings: tuple[ArtifactBinding, ...]) -> None:
    item, _, _, _, _, _, _, _, workflow = build_workflow(bindings=bindings)

    run = workflow.run(item, trigger="fixture")

    assert run.status == "FAILED"
    assert run.prediction_ids == []


def test_per_key_concurrent_dispatch_has_one_terminal_winner() -> None:
    item, _, repository, _, _, _, predictor, _, workflow = build_workflow()

    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda _: workflow.run(item, trigger="fixture"), range(24)))

    assert len({run.origin_run_id for run in runs}) == 1
    assert predictor.calls == ["champion-a"]
    assert [kind for kind, _ in repository.records].count("origin_run") == 1


def test_terminal_marker_is_last_and_orphans_are_invisible_to_retry() -> None:
    item, _, repository, _, _, _, _, _, workflow = build_workflow()
    repository.fail_terminal_once = True

    with pytest.raises(OSError, match="marker failure"):
        workflow.run(item, trigger="fixture")

    assert repository.terminal_run(item.idempotency_key) is None
    assert repository.records[-1][0] == "prediction"
    assert not any(
        kind == "attempt" and value.ended_at_utc is not None for kind, value in repository.records
    )

    run = workflow.run(item, trigger="fixture")

    assert run.status == "FOOTBALL_ONLY"
    assert repository.records[-1] == ("origin_run", run)
    started = [
        value
        for kind, value in repository.records
        if kind == "attempt" and value.status == "STARTED"
    ]
    assert started[1].retry_of_attempt_id == started[0].attempt_id


def test_second_dispatch_returns_terminal_without_new_attempt() -> None:
    item, _, repository, _, _, _, _, _, workflow = build_workflow()
    first = workflow.run(item, trigger="fixture")
    record_count = len(repository.records)

    second = workflow.run(item, trigger="fixture")

    assert second is first
    assert len(repository.records) == record_count


@pytest.mark.parametrize(
    "changes",
    [
        {"canonical_event_id": "wrong-event"},
        {"event_version": 2},
        {"origin": Origin.T72},
        {"feature_policy_version": "wrong-policy"},
    ],
)
def test_snapshot_identity_and_policy_must_match_obligation(changes: dict[str, object]) -> None:
    item, _, _, _, _, builder, _, _, workflow = build_workflow()
    builder.changes = changes

    run = workflow.run(item, trigger="fixture")

    assert run is not None
    assert run.status == "FAILED"
    assert run.prediction_ids == []


def test_missing_or_post_cutoff_manifest_fails_before_prediction() -> None:
    item, _, _, _, _, _, predictor, _, workflow = build_workflow()
    workflow.lineage_repository.missing_manifest = True

    missing = workflow.run(item, trigger="fixture")

    assert missing is not None
    assert missing.status == "FAILED"
    assert predictor.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"model_artifact_id": "wrong-artifact"},
        {"model_role": "challenger"},
        {"feature_snapshot_id": "wrong-snapshot"},
        {"origin_run_id": "wrong-run"},
        {"policy_versions": {"forecast": "wrong"}},
    ],
)
def test_predictor_output_lineage_is_validated_before_append(changes: dict[str, object]) -> None:
    item, _, repository, _, _, _, predictor, _, workflow = build_workflow()
    predictor.changes = changes

    run = workflow.run(item, trigger="fixture")

    assert run is not None
    assert run.status == "FAILED"
    assert not any(kind == "prediction" for kind, _ in repository.records)


def test_before_open_does_not_capture_or_poison_later_due_run() -> None:
    item, clock, repository, required, _, _, _, _, workflow = build_workflow()
    clock.set(item.window.window_opens_at_utc - timedelta(seconds=1))

    early = workflow.run(item, trigger="fixture")

    assert early is None
    assert required.calls == 0
    assert repository.terminal_run(item.idempotency_key) is None
    clock.set(item.window.target_at_utc)
    due = workflow.run(item, trigger="fixture")
    assert due is not None
    assert due.status == "FOOTBALL_ONLY"


@pytest.mark.parametrize("stage", ["feature", "predictor", "market", "append"])
def test_any_work_crossing_close_is_missed_with_zero_visible_predictions(stage: str) -> None:
    item, _, repository, _, _, builder, predictor, market, workflow = build_workflow()
    late = item.window.window_closes_at_utc + timedelta(seconds=1)
    if stage == "feature":
        builder.advance_to = late
    elif stage == "predictor":
        predictor.advance_to = late
    elif stage == "market":
        market.advance_to = late
    else:
        repository.advance_on_kind = "prediction"
        repository.advance_to = late

    run = workflow.run(item, trigger="fixture")

    assert run is not None
    assert run.status == "MISSED"
    assert run.prediction_ids == []


def test_exception_discovered_after_close_is_missed_not_failed() -> None:
    item, _, _, _, _, builder, _, _, workflow = build_workflow()
    builder.advance_to = item.window.window_closes_at_utc + timedelta(seconds=1)
    builder.fail = True

    run = workflow.run(item, trigger="fixture")

    assert run is not None
    assert run.status == "MISSED"


def test_optional_market_evaluation_failure_is_football_only() -> None:
    item, _, _, _, _, _, _, market, workflow = build_workflow()
    market.fail = True

    run = workflow.run(item, trigger="fixture")

    assert run is not None
    assert run.status == "FOOTBALL_ONLY"
    assert run.reason_codes == ["MARKET_EVALUATION_RUNTIMEERROR"]


def test_invalid_duplicate_challengers_are_isolated_sorted_and_attributed() -> None:
    bindings = (
        ArtifactBinding("champion-a", Origin.T60, "champion", True, True),
        ArtifactBinding("z-invalid", Origin.T72, "challenger", True, True),
        ArtifactBinding("b-valid", Origin.T60, "challenger", True, True),
        ArtifactBinding("a-valid", Origin.T60, "challenger", True, True),
        ArtifactBinding("a-valid", Origin.T60, "challenger", True, True),
    )
    item, _, repository, _, _, _, predictor, _, workflow = build_workflow(bindings=bindings)

    run = workflow.run(item, trigger="fixture")

    assert run is not None
    assert run.status == "FOOTBALL_ONLY"
    assert predictor.calls == ["champion-a", "b-valid"]
    failures = [
        value
        for kind, value in repository.records
        if kind == "capture" and hasattr(value, "safe_error_category")
    ]
    assert {failure.artifact_id for failure in failures} == {"a-valid", "z-invalid"}
    assert all(failure.attempt_id == run.attempt_ids[0] for failure in failures)
    assert all(failure.origin_run_id == run.origin_run_id for failure in failures)


def test_replay_requires_workflow_level_context_and_separate_repository() -> None:
    item, _, prospective, _, _, _, _, _, workflow = build_workflow()
    cutoff = item.window.target_at_utc

    run = workflow.run(
        item,
        trigger="fixture",
        context=ForecastExecutionContext.replay(cutoff, "HISTORICAL_RECONSTRUCTION"),
    )

    assert run is not None
    assert run.status == "FOOTBALL_ONLY"
    assert prospective.records == []
    replay = workflow.repositories.replay
    assert replay is not None
    prediction = next(value for kind, value in replay.records if kind == "prediction")
    assert prediction.provenance_grade is ProvenanceGrade.C
    assert prediction.reason_codes == ["HISTORICAL_RECONSTRUCTION"]
    assert workflow.feature_builder.cutoffs == [cutoff]


def test_replay_idempotency_includes_cutoff_and_reconstruction_identity() -> None:
    item, _, _, _, _, _, _, _, workflow = build_workflow()
    first = ForecastExecutionContext.replay(item.window.target_at_utc, "RECONSTRUCTION_A")
    second = ForecastExecutionContext.replay(
        item.window.target_at_utc - timedelta(days=1), "RECONSTRUCTION_B"
    )

    first_run = workflow.run(item, "fixture", first)
    second_run = workflow.run(item, "fixture", second)

    assert first_run is not None and second_run is not None
    assert first_run.origin_run_id != second_run.origin_run_id
    assert first_run.decision_at_utc != second_run.decision_at_utc


def test_context_is_mandatory_and_structural_forgery_is_rejected() -> None:
    parameter = inspect.signature(ForecastWorkflow.run).parameters["context"]
    assert parameter.default is inspect.Parameter.empty
    item, _, _, _, _, _, _, _, workflow = build_workflow()
    forged = SimpleNamespace(
        mode="live",
        namespace="prospective",
        provenance_grade=ProvenanceGrade.A,
        as_of_utc=None,
        reconstruction_reason=None,
    )

    with pytest.raises(TypeError, match="context"):
        workflow.run(item, "fixture", forged)


def test_replay_uses_archive_capture_path_and_never_live_capture() -> None:
    item, _, _, required, _, _, _, _, workflow = build_workflow()

    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.replay(item.window.target_at_utc, "ARCHIVE_RECONSTRUCTION"),
    )

    assert run is not None
    assert required.calls == 0
    assert required.archive_calls == 1


def test_snapshot_must_bind_exact_required_attempt_manifest() -> None:
    item, _, _, required, _, _, predictor, _, workflow = build_workflow()
    wrong = workflow.lineage_repository.manifest.model_copy(
        update={"capture_id": "capture-from-this-attempt"}
    )

    def capture(obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        required.calls += 1
        required.clock.set(required.received_at)
        return CaptureBundle(records=(wrong,), payload={"football": True})

    required.capture_live = capture  # type: ignore[method-assign]
    run = workflow.run(item, "fixture")

    assert run is not None
    assert run.status == "FAILED"
    assert predictor.calls == []


def test_snapshot_accepts_derived_fact_when_all_input_capture_manifests_are_exact() -> None:
    item, _, _, required, _, builder, predictor, _, workflow = build_workflow()
    primary = workflow.lineage_repository.manifest
    schedule = primary.model_copy(update={"capture_id": "schedule-1"})
    derived = workflow.lineage_repository.fact.model_copy(
        update={"input_capture_ids": ("schedule-1", primary.capture_id)}
    )
    workflow.lineage_repository.fact = derived
    builder.fact = derived
    builder.changes = {"input_manifest_ids": ["schedule-1", primary.capture_id]}
    captured: dict[str, CaptureManifest] = {}

    def capture(obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        required.calls += 1
        required.clock.set(required.received_at)
        current = primary.model_copy(update={"run_id": attempt_id})
        captured_schedule = schedule.model_copy(update={"run_id": attempt_id})
        captured.update({captured_schedule.capture_id: captured_schedule, current.capture_id: current})
        return CaptureBundle(
            records=(captured_schedule, current),
            payload={"football": True},
        )

    required.capture_live = capture  # type: ignore[method-assign]
    workflow.lineage_repository.resolve_manifests = lambda ids: [captured[item] for item in ids]

    run = workflow.run(item, "fixture")

    assert run is not None
    assert run.status == "FOOTBALL_ONLY"
    assert predictor.calls == ["champion-a"]


def test_snapshot_rejects_derived_fact_missing_secondary_input_manifest() -> None:
    item, _, _, _, _, builder, predictor, _, workflow = build_workflow()
    derived = workflow.lineage_repository.fact.model_copy(
        update={"input_capture_ids": ("schedule-1", "capture-1")}
    )
    workflow.lineage_repository.fact = derived
    builder.fact = derived

    run = workflow.run(item, "fixture")

    assert run is not None
    assert run.status == "FAILED"
    assert predictor.calls == []


def test_incomplete_snapshot_and_prediction_are_rejected() -> None:
    item, _, _, _, _, builder, predictor, _, workflow = build_workflow()
    builder.changes = {"status": SnapshotStatus.FAILED, "reason_codes": ["BAD"]}
    snapshot_run = workflow.run(item, "fixture")
    assert snapshot_run is not None and snapshot_run.status == "FAILED"
    assert predictor.calls == []

    item2, _, _, _, _, _, predictor2, _, workflow2 = build_workflow()
    predictor2.changes = {"status": PredictionStatus.DEGRADED, "reason_codes": ["BAD"]}
    prediction_run = workflow2.run(item2, "fixture")
    assert prediction_run is not None and prediction_run.status == "FAILED"


def test_replay_rejects_forged_grade_a_snapshot() -> None:
    item, _, _, _, _, builder, _, _, workflow = build_workflow()
    builder.changes = {"provenance_grade": ProvenanceGrade.A}

    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.replay(item.window.target_at_utc, "RECONSTRUCTION"),
    )

    assert run is not None
    assert run.status == "FAILED"


def test_replay_accepts_grade_b_archive_published_before_cutoff_but_captured_later() -> None:
    item, _, _, required, _, builder, _, _, workflow = build_workflow()
    cutoff = item.window.target_at_utc - timedelta(days=2)
    manifest = workflow.lineage_repository.manifest.model_copy(
        update={
            "response_received_at_utc": cutoff + timedelta(days=1),
            "source_snapshot_at_utc": cutoff - timedelta(minutes=1),
        }
    )
    fact = workflow.lineage_repository.fact.model_copy(
        update={
            "available_at_utc": cutoff - timedelta(minutes=1),
            "captured_at_utc": cutoff + timedelta(days=1),
            "archive_published_at_utc": cutoff - timedelta(minutes=1),
            "provenance_grade": ProvenanceGrade.B,
        }
    )
    workflow.lineage_repository.manifest = manifest
    workflow.lineage_repository.fact = fact
    builder.fact = fact

    def replay_capture(
        obligation: OriginObligation, attempt_id: str, historical_cutoff: datetime
    ) -> CaptureBundle:
        required.archive_calls += 1
        current = manifest.model_copy(update={"run_id": attempt_id})
        required.manifest = current
        return CaptureBundle((current, fact), {"football": True})

    required.capture_replay = replay_capture  # type: ignore[method-assign]
    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.replay(cutoff, "ARCHIVE_RECONSTRUCTION"),
    )

    assert run is not None and run.status == "FOOTBALL_ONLY"
    replay = workflow.repositories.replay
    assert replay is not None
    prediction = next(value for kind, value in replay.records if kind == "prediction")
    assert prediction.provenance_grade is ProvenanceGrade.C


def test_registry_crossing_close_skips_optional_capture() -> None:
    item, clock, _, _, optional, _, _, _, workflow = build_workflow()
    registry = workflow.artifact_registry
    registry.clock = clock
    registry.advance_to = item.window.window_closes_at_utc + timedelta(seconds=1)

    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "MISSED"
    assert optional.calls == 0


def test_challenger_failure_crossing_close_stops_before_next_challenger() -> None:
    bindings = (
        ArtifactBinding("champion-a", Origin.T60, "champion", True, True),
        ArtifactBinding("a-fails", Origin.T60, "challenger", True, True),
        ArtifactBinding("b-never", Origin.T60, "challenger", True, True),
    )
    item, _, _, _, _, _, predictor, _, workflow = build_workflow(
        bindings=bindings, fail_artifacts={"a-fails"}
    )
    predictor.advance_by_artifact["a-fails"] = item.window.window_closes_at_utc + timedelta(
        seconds=1
    )

    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "MISSED"
    assert predictor.calls == ["champion-a", "a-fails"]


def test_atomic_terminal_commit_cannot_publish_complete_after_close() -> None:
    item, _, repository, _, _, _, _, _, workflow = build_workflow()
    repository.advance_on_kind = "origin_run"
    repository.advance_to = item.window.window_closes_at_utc + timedelta(seconds=1)

    run = workflow.run(item, "fixture")

    assert run is not None
    assert run.status == "MISSED"
    assert run.prediction_ids == []
    terminal_attempt = [
        value for kind, value in repository.records if kind == "attempt" and value.ended_at_utc
    ][-1]
    assert terminal_attempt.status == "FAILED"
    assert terminal_attempt.safe_error_category == "EXECUTION_CROSSED_WINDOW"


def _market_objects(prediction: Prediction, cutoff: datetime):
    quote = MoneylineQuote(
        quote_id="quote-1",
        capture_id="market-capture-1",
        canonical_event_id=prediction.canonical_event_id,
        source_event_id="2026_01_GB_CHI",
        book_key="book-a",
        book_name="Book A",
        market_key="h2h",
        period="full_game",
        provider_last_update_at_utc=cutoff,
        response_received_at_utc=cutoff,
        capture_lag_seconds=0,
        provider_update_lag_seconds=0,
        selections=(
            MoneylineSelection(
                side="home", team="CHI", decimal_price=Decimal(2), raw_pointer="$[0]"
            ),
            MoneylineSelection(
                side="away", team="GB", decimal_price=Decimal("1.9"), raw_pointer="$[1]"
            ),
        ),
        overtime_included=True,
        tie_handling="push",
        market_semantics_version="v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="v1",
        provenance_grade=ProvenanceGrade.A,
    )
    comparator = MarketComparator(
        market_comparator_id="comparator-1",
        origin_run_id=prediction.origin_run_id,
        canonical_event_id=prediction.canonical_event_id,
        origin=prediction.origin,
        decision_at_utc=cutoff,
        market_policy_version="market-v1",
        quote_ids=[quote.quote_id],
        r_home_market=median_market_home((quote,)),
        p_tie_shared_prior=prediction.p_tie,
        provenance_grade=ProvenanceGrade.A,
    )
    candidate = DisplayedQuoteCandidate(
        candidate_id="candidate-orphan",
        prediction_id=prediction.prediction_id,
        quote_id=quote.quote_id,
        origin=prediction.origin,
        side="home",
        decision_at_utc=cutoff,
        decimal_price=Decimal(2),
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
    return quote, comparator, candidate


def test_orphan_market_candidate_is_optional_failure_and_never_appended() -> None:
    item, _, repository, _, _, _, _, market, workflow = build_workflow()

    def evaluate(prediction, market_payload, cutoff):
        quote, comparator, candidate = _market_objects(prediction, cutoff)
        return MarketEvaluation(comparator, (), (candidate,), (quote,))

    market.evaluate = evaluate  # type: ignore[method-assign]
    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FOOTBALL_ONLY"
    assert "MARKET_EVALUATION_LINEAGE_INVALID" in run.reason_codes
    assert not any(
        kind in {"comparator", "decision", "candidate"} for kind, _ in repository.records
    )


def test_duplicate_challenger_bindings_are_order_independent_and_all_isolated() -> None:
    duplicate_a = ArtifactBinding("duplicate", Origin.T60, "challenger", True, True)
    duplicate_b = ArtifactBinding(
        "duplicate", Origin.T60, "challenger", True, True, feature_policy_version="other"
    )
    champion = ArtifactBinding("champion-a", Origin.T60, "champion", True, True)
    results = []
    for bindings in ((champion, duplicate_a, duplicate_b), (champion, duplicate_b, duplicate_a)):
        item, _, repository, _, _, _, predictor, _, workflow = build_workflow(bindings=bindings)
        run = workflow.run(item, "fixture")
        failures = [
            value
            for kind, value in repository.records
            if kind == "capture" and hasattr(value, "safe_error_category")
        ]
        results.append((run.prediction_ids, predictor.calls, len(failures)))

    assert results == [(["prediction-champion-a"], ["champion-a"], 2)] * 2


def test_durable_forecast_repository_is_available_in_authorized_workflow_surface(
    tmp_path: Path,
) -> None:
    repository_type = getattr(forecast_module, "DurableForecastRepository", None)

    assert repository_type is not None
    first = repository_type(tmp_path, "prospective")
    second = repository_type(tmp_path, "prospective")
    assert first.namespace == second.namespace == "prospective"

    item, clock, _, required, optional, builder, predictor, market, _ = build_workflow()
    first_workflow = ForecastWorkflow(
        repositories=ForecastRepositories(first),
        required_capture=required,
        market_capture=optional,
        feature_builder=builder,
        lineage_repository=Facts(builder.fact, required, optional),
        artifact_registry=Registry(
            (ArtifactBinding("champion-a", Origin.T60, "champion", True, True),)
        ),
        predictor=predictor,
        market_layer=market,
        clock=clock,
        code_sha=CODE_SHA,
    )
    run = first_workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(item.window.window_opens_at_utc),
    )
    assert run is not None
    assert second.terminal_run(item.idempotency_key) == run

    reloaded_workflow = ForecastWorkflow(
        repositories=ForecastRepositories(second),
        required_capture=required,
        market_capture=optional,
        feature_builder=builder,
        lineage_repository=Facts(builder.fact, required, optional),
        artifact_registry=Registry(
            (ArtifactBinding("champion-a", Origin.T60, "champion", True, True),)
        ),
        predictor=predictor,
        market_layer=market,
        clock=clock,
        code_sha=CODE_SHA,
    )
    assert (
        reloaded_workflow.run(
            item,
            "fixture",
            ForecastExecutionContext.live(item.window.window_opens_at_utc),
        )
        == run
    )
    assert predictor.calls == ["champion-a"]


def _accepted_market(prediction: Prediction, cutoff: datetime) -> MarketEvaluation:
    quote, comparator, candidate = _market_objects(prediction, cutoff)
    decision = BettingDecision(
        decision_id="decision-1",
        prediction_id=prediction.prediction_id,
        canonical_event_id=prediction.canonical_event_id,
        origin=prediction.origin,
        side=candidate.side,
        quote_id=quote.quote_id,
        candidate_id=candidate.candidate_id,
        evaluated_at_utc=cutoff,
        policy_version=candidate.policy_version,
        status="candidate",
        reason_codes=[],
    )
    return MarketEvaluation(comparator, (decision,), (candidate,), (quote,))


def test_required_live_manifest_must_belong_to_current_attempt() -> None:
    item, _, repository, required, _, _, predictor, _, workflow = build_workflow()
    required.keep_wrong_run_id = True

    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FAILED"
    assert predictor.calls == []
    assert not any(kind == "prediction" for kind, _ in repository.records)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_capture",
        "future_provider_update",
        "wrong_candidate_price",
        "wrong_candidate_policy",
        "wrong_decision_policy",
        "wrong_decision_status",
    ],
)
def test_every_market_object_must_match_capture_quote_candidate_and_decision(
    mutation: str,
) -> None:
    item, _, repository, _, _, _, _, market, workflow = build_workflow()

    def evaluate(prediction, market_payload, cutoff):
        evaluation = _accepted_market(prediction, cutoff)
        quote = evaluation.quotes[0]
        candidate = evaluation.candidates[0]
        decision = evaluation.decisions[0]
        if mutation == "wrong_capture":
            quote = quote.model_copy(update={"capture_id": "other-capture"})
        elif mutation == "future_provider_update":
            quote = quote.model_copy(
                update={"provider_last_update_at_utc": cutoff + timedelta(seconds=1)}
            )
        elif mutation == "wrong_candidate_price":
            candidate = candidate.model_copy(update={"decimal_price": Decimal("2.1")})
        elif mutation == "wrong_candidate_policy":
            candidate = candidate.model_copy(update={"policy_version": "candidate-v2"})
        elif mutation == "wrong_decision_policy":
            decision = decision.model_copy(update={"policy_version": "candidate-v2"})
        else:
            decision = decision.model_copy(update={"status": "rejected"})
        return MarketEvaluation(
            evaluation.comparator,
            (decision,),
            (candidate,),
            (quote,),
        )

    market.evaluate = evaluate  # type: ignore[method-assign]
    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FOOTBALL_ONLY"
    assert "MARKET_EVALUATION_LINEAGE_INVALID" in run.reason_codes
    assert not any(
        kind in {"comparator", "decision", "candidate"} for kind, _ in repository.records
    )


def test_duplicate_challenger_prediction_id_is_isolated_from_champion() -> None:
    bindings = (
        ArtifactBinding("champion-a", Origin.T60, "champion", True, True),
        ArtifactBinding("challenger-a", Origin.T60, "challenger", True, True),
    )
    item, _, repository, _, _, _, predictor, _, workflow = build_workflow(bindings=bindings)
    predictor.prediction_ids_by_artifact = {
        "champion-a": "duplicate-prediction",
        "challenger-a": "duplicate-prediction",
    }

    run = workflow.run(item, "fixture")

    assert run is not None
    assert run.prediction_ids == ["duplicate-prediction"]
    predictions = [value for kind, value in repository.records if kind == "prediction"]
    assert len(predictions) == 1
    assert predictions[0].model_role == "champion"


def test_preopen_obligation_is_registered_without_terminal_poisoning() -> None:
    item, clock, repository, required, _, _, _, _, workflow = build_workflow()
    clock.set(item.window.window_opens_at_utc - timedelta(seconds=1))

    assert workflow.run(item, "fixture") is None
    assert len(repository.obligations) == 1
    assert repository.terminals == {}
    assert required.calls == 0


def test_live_and_replay_durable_repositories_cannot_share_backing_root(
    tmp_path: Path,
) -> None:
    prospective = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    replay = forecast_module.DurableForecastRepository(tmp_path, "replay")

    with pytest.raises(ValueError, match="physically distinct"):
        ForecastRepositories(prospective, replay)


def test_durable_record_ids_are_logical_and_conflicting_retry_fails_closed(
    tmp_path: Path,
) -> None:
    repository = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    item = obligation()
    prediction = Predictor().predict(
        FeatureBuilder(
            NormalizedFact(
                fact_id="fact",
                capture_id="capture",
                fact_type="team_strength_prior",
                entity_keys={"team": "CHI"},
                payload={"strength": 1},
                raw_pointer="$",
                available_at_utc=item.window.window_opens_at_utc,
                captured_at_utc=item.window.window_opens_at_utc,
                provenance_grade=ProvenanceGrade.A,
                normalization_schema_version="v1",
                fact_content_sha256="a" * 64,
            )
        ).build(item.event, item.origin, item.window.window_opens_at_utc, "live"),
        ArtifactBinding("champion-a", Origin.T60, "champion", True, True),
        origin_run_id="run-1",
        obligation=item,
        created_at_utc=item.window.window_opens_at_utc,
        context=ForecastExecutionContext.live(item.window.window_opens_at_utc),
    )
    repository.append_prediction(prediction)

    with pytest.raises(Exception, match="prediction"):
        repository.append_prediction(prediction.model_copy(update={"p_home": Decimal("0.54")}))


def test_corrupt_durable_attempt_marker_raises_integrity_error(tmp_path: Path) -> None:
    repository = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    attempt = RunAttempt(
        attempt_id="attempt-1",
        origin_run_id="run-1",
        retry_of_attempt_id=None,
        trigger="fixture",
        started_at_utc=KICKOFF - timedelta(hours=2),
        ended_at_utc=None,
        status="STARTED",
        safe_error_category=None,
    )
    repository.append_attempt(attempt)
    marker = next((tmp_path / "commits" / "forecast-attempt-prospective").glob("*.json"))
    marker.write_text(json.dumps({"idempotency_key": "run-1|substituted"}))

    with pytest.raises(DataIntegrityError):
        repository.latest_attempt("run-1")


def test_real_durable_terminal_rechecks_clock_after_attempt_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    item, clock, _, required, optional, builder, predictor, market, _ = build_workflow()
    repository = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    append_attempt = repository.append_attempt

    def advancing_append_attempt(attempt: RunAttempt) -> None:
        append_attempt(attempt)
        if attempt.ended_at_utc is not None:
            clock.set(item.window.window_closes_at_utc + timedelta(seconds=1))

    monkeypatch.setattr(repository, "append_attempt", advancing_append_attempt)
    workflow = ForecastWorkflow(
        repositories=ForecastRepositories(repository),
        required_capture=required,
        market_capture=optional,
        feature_builder=builder,
        lineage_repository=Facts(builder.fact, required, optional),
        artifact_registry=Registry(
            (ArtifactBinding("champion-a", Origin.T60, "champion", True, True),)
        ),
        predictor=predictor,
        market_layer=market,
        clock=clock,
        code_sha=CODE_SHA,
    )

    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(item.window.window_opens_at_utc),
    )

    assert run is not None and run.status == "MISSED"
    assert run.prediction_ids == []
    assert (
        repository.latest_attempt(run.origin_run_id).ended_at_utc > item.window.window_closes_at_utc
    )
    graph = forecast_module.DurableForecastRepository(
        tmp_path,
        "prospective",
        artifact_resolver=repository.artifact_resolver,
    ).load_committed_graph(item.idempotency_key)
    assert len(graph.attempts) == 1
    assert graph.attempts[0] == repository.latest_attempt(run.origin_run_id)


def test_durable_committed_graph_reloads_every_typed_reference(tmp_path: Path) -> None:
    item, clock, _, required, optional, builder, predictor, market, _ = build_workflow()
    repository = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    workflow = ForecastWorkflow(
        repositories=ForecastRepositories(repository),
        required_capture=required,
        market_capture=optional,
        feature_builder=builder,
        lineage_repository=Facts(builder.fact, required, optional),
        artifact_registry=Registry(
            (ArtifactBinding("champion-a", Origin.T60, "champion", True, True),)
        ),
        predictor=predictor,
        market_layer=market,
        clock=clock,
        code_sha=CODE_SHA,
    )
    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(item.window.window_opens_at_utc),
    )
    assert run is not None

    graph = forecast_module.DurableForecastRepository(
        tmp_path,
        "prospective",
        artifact_resolver=repository.artifact_resolver,
    ).load_committed_graph(item.idempotency_key)

    assert graph.run == run
    assert tuple(row.prediction_id for row in graph.predictions) == tuple(run.prediction_ids)
    assert graph.snapshot.snapshot_id == graph.predictions[0].feature_snapshot_id
    assert set(graph.snapshot.input_manifest_ids).issubset(
        {row.capture_id for row in graph.manifests}
    )
    assert {row.capture_id for row in graph.manifests} == {
        "capture-1",
        "market-capture-1",
    }


def _durable_workflow(
    tmp_path: Path,
    *,
    bindings: tuple[ArtifactBinding, ...] | None = None,
    optional_mode: str = "ok",
) -> tuple[
    OriginObligation,
    MutableClock,
    RequiredCapture,
    OptionalCapture,
    ForecastWorkflow,
    forecast_module.DurableForecastRepository,
]:
    item, clock, _, required, optional, builder, predictor, market, _ = build_workflow(
        bindings=bindings,
        optional_mode=optional_mode,
    )
    trusted_bindings = (
        (ArtifactBinding("champion-a", Origin.T60, "champion", True, True),)
        if bindings is None
        else bindings
    )
    repository = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    workflow = ForecastWorkflow(
        repositories=ForecastRepositories(repository),
        required_capture=required,
        market_capture=optional,
        feature_builder=builder,
        lineage_repository=Facts(builder.fact, required, optional),
        artifact_registry=Registry(trusted_bindings),
        predictor=predictor,
        market_layer=market,
        clock=clock,
        code_sha=CODE_SHA,
    )
    return item, clock, required, optional, workflow, repository


def test_durable_live_preopen_due_and_later_duplicate_share_stable_obligation(
    tmp_path: Path,
) -> None:
    item, clock, required, _, workflow, repository = _durable_workflow(tmp_path)
    preopen = item.window.window_opens_at_utc - timedelta(seconds=1)
    clock.set(preopen)

    assert workflow.run(item, "fixture", ForecastExecutionContext.live(preopen)) is None

    clock.set(item.window.target_at_utc)
    winner = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(item.window.target_at_utc),
    )
    assert winner is not None

    one_second_later = item.window.target_at_utc + timedelta(seconds=1)
    clock.set(one_second_later)
    duplicate = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(one_second_later),
    )

    assert duplicate == winner
    assert required.calls == 1
    assert repository.load_obligation(item.idempotency_key).execution_key == item.idempotency_key


@pytest.mark.parametrize(
    "mutation",
    ["missing_captured_quote", "forged_evaluation_quote", "wrong_manifest_run"],
)
def test_optional_market_capture_and_evaluation_quote_sets_match_exactly(
    mutation: str,
) -> None:
    item, _, repository, _, optional, _, _, market, workflow = build_workflow()
    original_capture = optional.capture

    def capture(obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        bundle = original_capture(obligation, attempt_id)
        manifest = next(row for row in bundle.records if isinstance(row, CaptureManifest))
        quote = next(row for row in bundle.records if isinstance(row, MoneylineQuote))
        if mutation == "wrong_manifest_run":
            manifest = manifest.model_copy(update={"run_id": "another-attempt"})
        records: tuple[object, ...] = (manifest,)
        if mutation != "missing_captured_quote":
            records += (quote,)
        return CaptureBundle(records, bundle.payload)

    optional.capture = capture  # type: ignore[method-assign]

    def evaluate(prediction, market_payload, cutoff):
        evaluation = _accepted_market(prediction, cutoff)
        if mutation != "forged_evaluation_quote":
            return evaluation
        quote = evaluation.quotes[0]
        home, away = quote.selections
        forged = quote.model_copy(
            update={
                "selections": (
                    home.model_copy(update={"team": "GB"}),
                    away,
                )
            }
        )
        return MarketEvaluation(
            evaluation.comparator,
            evaluation.decisions,
            evaluation.candidates,
            (forged,),
        )

    market.evaluate = evaluate  # type: ignore[method-assign]
    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FOOTBALL_ONLY"
    assert any(
        code in run.reason_codes
        for code in ("MARKET_CAPTURE_LINEAGE_INVALID", "MARKET_EVALUATION_LINEAGE_INVALID")
    )
    assert not any(
        kind in {"comparator", "decision", "candidate"} for kind, _ in repository.records
    )


@pytest.mark.parametrize("mutation", ["tie_prior", "probability_side", "forged_policy"])
def test_market_economics_and_policy_must_match_champion_and_frozen_binding(
    mutation: str,
) -> None:
    item, _, repository, _, optional, _, _, market, workflow = build_workflow()
    original_capture = optional.capture

    def capture(obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        return original_capture(obligation, attempt_id)

    optional.capture = capture  # type: ignore[method-assign]

    def evaluate(prediction, market_payload, cutoff):
        evaluation = _accepted_market(prediction, cutoff)
        comparator = evaluation.comparator
        assert comparator is not None
        candidate = evaluation.candidates[0]
        decision = evaluation.decisions[0]
        if mutation == "tie_prior":
            comparator = comparator.model_copy(update={"p_tie_shared_prior": Decimal("0.03")})
        elif mutation == "probability_side":
            candidate = candidate.model_copy(
                update={
                    "side": "away",
                    "decimal_price": Decimal("1.9"),
                    "displayed_ev_per_unit": Decimal("0.008"),
                    "quarter_kelly_fraction": Decimal("0.002267573696145124716553287982"),
                }
            )
            decision = decision.model_copy(update={"side": "away"})
        else:
            comparator = comparator.model_copy(update={"market_policy_version": "forged"})
            candidate = candidate.model_copy(update={"policy_version": "forged"})
            decision = decision.model_copy(update={"policy_version": "forged"})
        return MarketEvaluation(comparator, (decision,), (candidate,), evaluation.quotes)

    market.evaluate = evaluate  # type: ignore[method-assign]
    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FOOTBALL_ONLY"
    assert "MARKET_EVALUATION_LINEAGE_INVALID" in run.reason_codes
    assert not any(
        kind in {"comparator", "decision", "candidate"} for kind, _ in repository.records
    )


def test_decode_committed_graph_rejects_missing_and_extra_prediction_lineage(
    tmp_path: Path,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    clock.set(item.window.target_at_utc)
    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(item.window.target_at_utc),
    )
    assert run is not None
    graph = repository.load_committed_graph(item.idempotency_key)

    missing = forecast_module.DurableForecastRepository.encode_committed_graph(graph)
    missing["predictions"] = []
    with pytest.raises(DataIntegrityError):
        forecast_module.DurableForecastRepository.decode_committed_graph(
            missing,
            artifact_resolver=repository.artifact_resolver,
        )

    dangling = forecast_module.DurableForecastRepository.encode_committed_graph(graph)
    _, _, candidate = _market_objects(graph.predictions[0], graph.run.decision_at_utc)
    dangling["candidates"] = [
        candidate.model_copy(update={"prediction_id": "missing-prediction"}).model_dump(mode="json")
    ]
    with pytest.raises(DataIntegrityError):
        forecast_module.DurableForecastRepository.decode_committed_graph(
            dangling,
            artifact_resolver=repository.artifact_resolver,
        )


def test_repository_isolation_rejects_proxy_aliases_of_same_backing_root(
    tmp_path: Path,
) -> None:
    class IdentityProxy(forecast_module.DurableForecastRepository):
        @property
        def storage_identity(self) -> str:
            return f"proxy:{self.namespace}:{id(self)}"

    prospective = IdentityProxy(tmp_path, "prospective")
    replay = IdentityProxy(tmp_path, "replay")

    with pytest.raises((TypeError, ValueError), match="concrete|physically distinct"):
        ForecastRepositories(prospective, replay)


def test_repository_isolation_rejects_wrappers_that_hide_one_durable_backing(
    tmp_path: Path,
) -> None:
    class HidingProxy:
        def __init__(self, repository, namespace):
            self._repository = repository
            self.namespace = namespace

        @property
        def storage_identity(self):
            return f"forged:{self.namespace}:{id(self)}"

        @property
        def backing_identity(self):
            return (id(self), id(self))

        def __getattr__(self, name):
            return getattr(self._repository, name)

    prospective = HidingProxy(
        forecast_module.DurableForecastRepository(tmp_path, "prospective"), "prospective"
    )
    replay = HidingProxy(forecast_module.DurableForecastRepository(tmp_path, "replay"), "replay")

    with pytest.raises((TypeError, ValueError), match="durable|repository"):
        ForecastRepositories(prospective, replay)


def test_real_terminal_marker_publication_rechecks_clock_at_visibility_boundary(
    tmp_path: Path,
) -> None:
    item, clock, _, _, workflow, _ = _durable_workflow(tmp_path)

    class PublicationBoundaryClock:
        def __call__(self) -> datetime:
            prepared = tmp_path / "commits" / "forecast-terminal-prepared-prospective"
            if prepared.exists() and any(prepared.glob("*.json")):
                clock.set(item.window.window_closes_at_utc + timedelta(seconds=1))
            return clock()

    workflow.clock = PublicationBoundaryClock()
    clock.set(item.window.target_at_utc)
    run = workflow.run(
        item,
        "fixture",
        ForecastExecutionContext.live(item.window.target_at_utc),
    )

    assert run is not None and run.status == "MISSED"
    assert run.prediction_ids == []
    reloaded = forecast_module.DurableForecastRepository(tmp_path, "prospective")
    assert reloaded.terminal_run(item.idempotency_key) == run


def test_decisive_deadline_check_runs_inside_terminal_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    item, clock, required, optional, _, _ = _durable_workflow(tmp_path)
    _, _, _, _, _, builder, predictor, market, _ = build_workflow()
    repository = forecast_module.DurableForecastRepository(tmp_path / "publication", "prospective")
    publish_terminal = repository._publish_terminal

    def advancing_publish_terminal(key, run, digest, **kwargs):
        clock.set(item.window.window_closes_at_utc + timedelta(seconds=1))
        return publish_terminal(key, run, digest, **kwargs)

    monkeypatch.setattr(repository, "_publish_terminal", advancing_publish_terminal)
    workflow = ForecastWorkflow(
        repositories=ForecastRepositories(repository),
        required_capture=required,
        market_capture=optional,
        feature_builder=builder,
        lineage_repository=Facts(builder.fact, required, optional),
        artifact_registry=Registry(
            (ArtifactBinding("champion-a", Origin.T60, "champion", True, True),)
        ),
        predictor=predictor,
        market_layer=market,
        clock=clock,
        code_sha=CODE_SHA,
    )
    clock.set(item.window.target_at_utc)

    run = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

    assert run is not None and run.status == "MISSED"
    assert run.prediction_ids == []
    assert repository.load_committed_graph(item.idempotency_key).run == run


def test_link_boundary_cannot_publish_success_after_window_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    real_link = forecast_module.os.link
    crossed = False

    def delayed_link(source: object, destination: object) -> None:
        nonlocal crossed
        target = Path(destination)  # type: ignore[arg-type]
        if target.parent.name == "forecast-terminal-prospective" and not crossed:
            crossed = True
            clock.set(item.window.window_closes_at_utc + timedelta(microseconds=1))
        real_link(source, destination)

    monkeypatch.setattr(forecast_module.os, "link", delayed_link)
    clock.set(item.window.target_at_utc)

    run = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

    assert crossed is True
    assert run is not None and run.status == "MISSED"
    assert run.prediction_ids == []
    assert repository.terminal_run(item.idempotency_key) == run


@pytest.mark.parametrize("crash_after_fsync", [False, True])
def test_post_link_directory_fsync_crossing_never_leaves_consumable_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    crash_after_fsync: bool,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    real_fsync_directory = forecast_module.LedgerStore._fsync_directory
    crossed = False

    def delayed_fsync_directory(directory: Path) -> None:
        nonlocal crossed
        real_fsync_directory(directory)
        if directory.name == "forecast-terminal-prospective" and not crossed:
            crossed = True
            clock.set(item.window.window_closes_at_utc + timedelta(microseconds=1))
            if crash_after_fsync:
                raise OSError("simulated crash after durable terminal candidate")

    monkeypatch.setattr(
        forecast_module.LedgerStore,
        "_fsync_directory",
        staticmethod(delayed_fsync_directory),
    )
    clock.set(item.window.target_at_utc)

    if crash_after_fsync:
        with pytest.raises(OSError, match="durable terminal candidate"):
            workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
        assert crossed is True
        reloaded = forecast_module.DurableForecastRepository(tmp_path, "prospective")
        assert reloaded.terminal_run(item.idempotency_key) is None

        recovered = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

        assert recovered is not None and recovered.status == "MISSED"
        assert recovered.prediction_ids == []
        assert reloaded.terminal_run(item.idempotency_key) == recovered
        return

    run = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

    assert crossed is True
    assert run is not None and run.status == "MISSED"
    assert run.prediction_ids == []
    assert repository.terminal_run(item.idempotency_key) == run


@pytest.mark.parametrize("crash_after_receipt_link", [False, True])
def test_receipt_publication_crossing_never_exposes_success_after_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    crash_after_receipt_link: bool,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    real_link = forecast_module.os.link
    real_stat = Path.stat
    crossed = False
    receipt_marker: Path | None = None
    late_publication = item.window.window_closes_at_utc + timedelta(microseconds=1)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    late_delta = late_publication - epoch
    late_publication_ns = (
        late_delta.days * 86_400 + late_delta.seconds
    ) * 1_000_000_000 + late_delta.microseconds * 1_000

    def delayed_receipt_link(source: object, destination: object) -> None:
        nonlocal crossed, receipt_marker
        target = Path(destination)  # type: ignore[arg-type]
        if target.parent.name == "forecast-terminal-receipt-prospective" and not crossed:
            crossed = True
            clock.set(late_publication)
            real_link(source, destination)
            receipt_marker = target
            if crash_after_receipt_link:
                raise OSError("simulated crash after receipt marker publication")
            return
        real_link(source, destination)

    def simulated_receipt_ctime(path: Path, *args: object, **kwargs: object):
        result = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if receipt_marker is not None and path == receipt_marker:
            return SimpleNamespace(
                st_ctime_ns=late_publication_ns,
                st_mtime_ns=result.st_mtime_ns,
            )
        return result

    monkeypatch.setattr(forecast_module.os, "link", delayed_receipt_link)
    monkeypatch.setattr(Path, "stat", simulated_receipt_ctime)
    clock.set(item.window.target_at_utc)

    if crash_after_receipt_link:
        with pytest.raises(OSError, match="receipt marker publication"):
            workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
        assert crossed is True
        reloaded = forecast_module.DurableForecastRepository(
            tmp_path,
            "prospective",
            artifact_resolver=repository.artifact_resolver,
        )
        assert reloaded.terminal_run(item.idempotency_key) is None

        recovered = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

        assert recovered is not None and recovered.status == "MISSED"
        assert recovered.prediction_ids == []
        assert reloaded.terminal_run(item.idempotency_key) == recovered
        return

    run = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
    reloaded = forecast_module.DurableForecastRepository(
        tmp_path,
        "prospective",
        artifact_resolver=repository.artifact_resolver,
    )

    assert crossed is True
    assert run is not None and run.status == "MISSED"
    assert run.prediction_ids == []
    assert reloaded.terminal_run(item.idempotency_key) == run


@pytest.mark.parametrize("invalid_kind", ["unverified", "wrong_origin"])
def test_durable_invalid_optional_challenger_preserves_champion_terminal(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    invalid = ArtifactBinding(
        "optional-invalid",
        Origin.T72 if invalid_kind == "wrong_origin" else Origin.T60,
        "challenger",
        True,
        invalid_kind != "unverified",
    )
    bindings = (
        ArtifactBinding("champion-a", Origin.T60, "champion", True, True),
        invalid,
    )
    item, clock, _, _, workflow, repository = _durable_workflow(
        tmp_path,
        bindings=bindings,
        optional_mode="disabled",
    )
    clock.set(item.window.target_at_utc)

    run = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

    assert run is not None and run.status == "FOOTBALL_ONLY"
    graph = repository.load_committed_graph(item.idempotency_key)
    assert tuple(row.model_artifact_id for row in graph.predictions) == ("champion-a",)
    assert tuple(row.binding.artifact_id for row in graph.artifact_selections) == ("champion-a",)
    assert len(graph.optional_failures) == 1
    assert graph.optional_failures[0].artifact_id == "optional-invalid"
    assert graph.optional_failures[0].safe_error_category == "VALUEERROR"


def test_completed_pre_marker_orphan_is_recovered_without_recapture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    item, clock, required, _, workflow, repository = _durable_workflow(tmp_path)
    real_link = forecast_module.os.link
    fail_terminal_once = True

    def flaky_link(source: object, destination: object) -> None:
        nonlocal fail_terminal_once
        target = Path(destination)  # type: ignore[arg-type]
        if target.parent.name == "forecast-terminal-prospective" and fail_terminal_once:
            fail_terminal_once = False
            raise OSError("simulated terminal publication failure")
        real_link(source, destination)

    monkeypatch.setattr(forecast_module.os, "link", flaky_link)
    clock.set(item.window.target_at_utc)
    with pytest.raises(OSError, match="terminal publication failure"):
        workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

    origin_run_id = sha256(f"prospective|{item.idempotency_key}".encode()).hexdigest()
    completed = repository.latest_attempt(origin_run_id)
    assert completed is not None and completed.status == "COMPLETE"
    assert repository.terminal_run(item.idempotency_key) is None
    assert required.calls == 1

    recovered = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))

    assert recovered is not None and recovered.status == "FOOTBALL_ONLY"
    assert recovered.prediction_ids == ["prediction-champion-a"]
    assert required.calls == 1


@pytest.mark.parametrize("mutation", ["execution_identity", "late_fact", "artifact_lineage"])
def test_committed_graph_rejects_untrusted_prospective_claims(
    tmp_path: Path,
    mutation: str,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    clock.set(item.window.target_at_utc)
    workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
    graph = repository.load_committed_graph(item.idempotency_key)
    if mutation == "execution_identity":
        corrupted = replace(
            graph,
            obligation=replace(graph.obligation, execution_key="forged-live-key"),
            run=graph.run.model_copy(update={"origin_run_id": "forged-origin-run"}),
            attempts=tuple(
                row.model_copy(update={"origin_run_id": "forged-origin-run"})
                for row in graph.attempts
            ),
            predictions=tuple(
                row.model_copy(update={"origin_run_id": "forged-origin-run"})
                for row in graph.predictions
            ),
        )
    elif mutation == "late_fact":
        corrupted = replace(
            graph,
            facts=tuple(
                row.model_copy(
                    update={
                        "available_at_utc": graph.run.decision_at_utc + timedelta(seconds=1),
                        "captured_at_utc": graph.run.decision_at_utc + timedelta(seconds=1),
                        "provenance_grade": ProvenanceGrade.C,
                    }
                )
                for row in graph.facts
            ),
        )
    else:
        corrupted = replace(
            graph,
            predictions=tuple(
                row.model_copy(
                    update={
                        "model_artifact_id": "unregistered-artifact",
                        "calibrator_artifact_id": "unregistered-calibrator",
                        "policy_versions": {"forecast": "unregistered-policy"},
                    }
                )
                for row in graph.predictions
            ),
        )

    with pytest.raises(DataIntegrityError):
        forecast_module.validate_committed_graph(
            corrupted,
            artifact_resolver=repository.artifact_resolver,
        )


def test_durable_subclasses_cannot_hide_shared_live_and_replay_storage(
    tmp_path: Path,
) -> None:
    class DelegatingDurable(forecast_module.DurableForecastRepository):
        pass

    prospective = DelegatingDurable(tmp_path / "prospective", "prospective")
    replay = DelegatingDurable(tmp_path / "replay", "replay")
    replay.ledger = prospective.ledger

    with pytest.raises((TypeError, ValueError), match="concrete|distinct"):
        ForecastRepositories(prospective, replay)


def test_blank_artifact_binding_fails_closed() -> None:
    with pytest.raises(ValueError, match="artifact"):
        ArtifactBinding("", Origin.T60, "challenger", True, True)


def test_blank_optional_failure_attribution_fails_closed(
    tmp_path: Path,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    clock.set(item.window.target_at_utc)
    workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
    graph = repository.load_committed_graph(item.idempotency_key)
    encoded = repository.encode_committed_graph(graph)
    encoded["optional_failures"] = [
        {
            "attempt_id": graph.attempts[-1].attempt_id,
            "origin_run_id": graph.run.origin_run_id,
            "origin": graph.run.origin.value,
            "artifact_id": "",
            "safe_error_category": "",
        }
    ]

    with pytest.raises(DataIntegrityError):
        repository.decode_committed_graph(
            encoded,
            artifact_resolver=repository.artifact_resolver,
        )


@pytest.mark.parametrize("corruption", ["status", "dangling", "branch"])
def test_committed_graph_rejects_invalid_terminal_retry_chain(
    tmp_path: Path, corruption: str
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    clock.set(item.window.target_at_utc)
    run = workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
    assert run is not None
    graph = repository.load_committed_graph(item.idempotency_key)
    terminal = graph.attempts[-1]
    if corruption == "status":
        changed = terminal.model_copy(update={"status": "FAILED", "safe_error_category": "FORGED"})
        corrupted = replace(graph, attempts=(changed,))
    else:
        extra = terminal.model_copy(
            update={
                "attempt_id": f"attempt-{corruption}",
                "retry_of_attempt_id": (
                    "missing-attempt" if corruption == "dangling" else terminal.retry_of_attempt_id
                ),
            }
        )
        corrupted = replace(graph, attempts=graph.attempts + (extra,))
    with pytest.raises(DataIntegrityError):
        forecast_module.validate_committed_graph(
            corrupted,
            artifact_resolver=repository.artifact_resolver,
        )


def test_committed_graph_decoder_rejects_extra_fields_and_primitive_coercion(
    tmp_path: Path,
) -> None:
    item, clock, _, _, workflow, repository = _durable_workflow(tmp_path)
    clock.set(item.window.target_at_utc)
    workflow.run(item, "fixture", ForecastExecutionContext.live(clock()))
    encoded = repository.encode_committed_graph(
        repository.load_committed_graph(item.idempotency_key)
    )
    encoded["unexpected"] = True
    encoded["obligation"]["execution_key"] = 17

    with pytest.raises(DataIntegrityError):
        repository.decode_committed_graph(
            encoded,
            artifact_resolver=repository.artifact_resolver,
        )


def test_resolved_required_manifest_must_equal_captured_manifest() -> None:
    item, _, _, required, _, _, predictor, _, workflow = build_workflow()
    original = workflow.lineage_repository.resolve_manifests

    def resolve(ids):
        return [row.model_copy(update={"raw_payload_sha256": "0" * 64}) for row in original(ids)]

    workflow.lineage_repository.resolve_manifests = resolve
    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FAILED"
    assert predictor.calls == []
    assert required.calls == 1


@pytest.mark.parametrize("mutation", ["comparator_math", "candidate_math", "duplicate_book"])
def test_market_truth_is_recomputed_from_exact_unique_book_quotes(mutation: str) -> None:
    item, _, repository, _, optional, _, _, market, workflow = build_workflow()
    original_capture = optional.capture

    def capture(obligation, attempt_id):
        bundle = original_capture(obligation, attempt_id)
        if mutation != "duplicate_book":
            return bundle
        quote = next(row for row in bundle.records if isinstance(row, MoneylineQuote))
        duplicate = quote.model_copy(update={"quote_id": "quote-duplicate"})
        return CaptureBundle(bundle.records + (duplicate,), bundle.payload)

    optional.capture = capture

    def evaluate(prediction, market_payload, cutoff):
        evaluation = _accepted_market(prediction, cutoff)
        comparator = evaluation.comparator
        assert comparator is not None
        candidate = evaluation.candidates[0]
        if mutation == "comparator_math":
            comparator = comparator.model_copy(update={"r_home_market": Decimal("0.5")})
        elif mutation == "candidate_math":
            candidate = candidate.model_copy(update={"displayed_ev_per_unit": Decimal("0.07")})
        else:
            quote = evaluation.quotes[0]
            duplicate = quote.model_copy(update={"quote_id": "quote-duplicate"})
            comparator = comparator.model_copy(
                update={"quote_ids": [quote.quote_id, duplicate.quote_id]}
            )
            return MarketEvaluation(
                comparator, evaluation.decisions, evaluation.candidates, (quote, duplicate)
            )
        return MarketEvaluation(comparator, evaluation.decisions, (candidate,), evaluation.quotes)

    market.evaluate = evaluate
    run = workflow.run(item, "fixture")

    assert run is not None and run.status == "FOOTBALL_ONLY"
    assert "MARKET_EVALUATION_LINEAGE_INVALID" in run.reason_codes
    assert not any(kind == "comparator" for kind, _ in repository.records)
