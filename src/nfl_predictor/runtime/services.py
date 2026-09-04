"""Fail-closed production composition for every command-line service."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cached_property
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from nfl_predictor.betting.policy import (
    CalibrationBins,
    CandidateContext,
    CandidateEvaluation,
    CandidatePolicy,
    InMemoryDecisionRepository,
    OddsPolicy,
    OfficialEventState,
    TrustedEventSourceMatch,
    load_odds_policy,
)
from nfl_predictor.capture.service import CaptureService
from nfl_predictor.cli import SchedulerFreshnessPolicy, ServiceRegistry
from nfl_predictor.config import AppConfig, load_app_config
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.markets import DisplayedQuoteCandidate, MoneylineQuote
from nfl_predictor.evaluation.promotion import load_evaluation_policy
from nfl_predictor.features.builder import FeatureBuilder
from nfl_predictor.features.policy import load_feature_policy
from nfl_predictor.features.team_strength import PointInTimeRatingService
from nfl_predictor.features.venue import VenueStore
from nfl_predictor.identity.events import EventReconciler, ScheduleFact
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.markets.budget import FileLockBudgetRepository, OddsBudget
from nfl_predictor.runtime.artifacts import VerifiedArtifactRegistry, VerifiedForecastPredictor
from nfl_predictor.runtime.capture import (
    ArrowOutcomeAdapter,
    NflverseFootballNormalizer,
    OptionalOddsCapture,
    RequiredFootballCapture,
)
from nfl_predictor.runtime.lineage import DurableLineageRepository, RuntimePaths
from nfl_predictor.runtime.markets import DurableCalibrationBins, ProductionMarketLayer
from nfl_predictor.sources.base import BuildIdentity
from nfl_predictor.sources.nflverse import LOADERS, NflverseAdapter
from nfl_predictor.sources.odds_api import OddsApiAdapter
from nfl_predictor.storage import DataIntegrityError, schema_for, write_contracts
from nfl_predictor.storage.ledger import canonical_json_bytes
from nfl_predictor.workflows.dispatch import (
    DispatchAuthorization,
    JobProjection,
    build_dispatch_payload,
    plan_private_usage,
    repository_dispatch_request,
)
from nfl_predictor.workflows.forecast import (
    DurableForecastRepository,
    ForecastExecutionContext,
    ForecastRepositories,
    ForecastWorkflow,
    SnapshotBuilder,
)
from nfl_predictor.workflows.outcomes import DurableOutcomeReportRepository, OutcomeWorkflow
from nfl_predictor.workflows.report import ReportWorkflow
from nfl_predictor.workflows.schedule_windows import due_clusters, exact_cron_entries, target_for

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE_SHA = re.compile(r"^[0-9a-f]{40}$")
_POLICY_FILES = (
    "evaluation_policy_v1.toml",
    "feature_policy_v1.toml",
    "forecast_policy_v1.toml",
    "model_policy_v1.toml",
    "odds_policy_v1.toml",
    "venues_v1.csv",
)


class RepositoryDispatchHttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
    ) -> RepositoryDispatchResponse: ...


class RepositoryDispatchResponse(Protocol):
    status_code: int


@dataclass(frozen=True)
class RuntimeClients:
    nflverse_loaders: Mapping[str, Callable[..., Any]] | None = None
    odds_http_client: httpx.Client | None = None
    repository_dispatch_http_client: RepositoryDispatchHttpClient | None = None


@dataclass(frozen=True)
class ProductionRuntime:
    services: ServiceRegistry
    scheduler_policy: SchedulerFreshnessPolicy


class _CandidateIds:
    @staticmethod
    def candidate(**values: Any) -> DisplayedQuoteCandidate:
        prediction = values["prediction"]
        quote = values["quote"]
        identity = (
            f"{prediction.prediction_id}|{quote.quote_id}|{values['side']}|"
            f"{values['policy_version']}"
        )
        return DisplayedQuoteCandidate(
            candidate_id=hashlib.sha256(identity.encode()).hexdigest(),
            prediction_id=prediction.prediction_id,
            quote_id=quote.quote_id,
            origin=prediction.origin,
            side=values["side"],
            decision_at_utc=prediction.decision_at_utc,
            decimal_price=values["price"],
            raw_p_win=values["raw_p_win"],
            buffered_p_win=values["buffered_p_win"],
            p_loss=values["raw_p_loss"],
            buffered_p_loss=values["buffered_p_loss"],
            p_push=values["p_push"],
            displayed_ev_per_unit=values["ev"],
            quarter_kelly_fraction=values["kelly"],
            policy_version=values["policy_version"],
            decision_status="candidate",
            reason_codes=[],
        )

    @staticmethod
    def decision(**values: Any) -> BettingDecision:
        prediction = values["prediction"]
        identity = (
            f"{prediction.prediction_id}|{values['side']}|{values['policy_version']}"
        )
        return BettingDecision(
            decision_id=hashlib.sha256(identity.encode()).hexdigest(),
            prediction_id=prediction.prediction_id,
            canonical_event_id=prediction.canonical_event_id,
            origin=prediction.origin,
            side=values["side"],
            quote_id=values["quote_id"],
            candidate_id=values["candidate_id"],
            evaluated_at_utc=prediction.decision_at_utc,
            policy_version=values["policy_version"],
            status=values["status"],
            reason_codes=values["reason_codes"],
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_of(context: ForecastExecutionContext) -> datetime:
    if context.as_of_utc is None:
        raise ValueError("forecast execution context is missing its UTC time")
    return context.as_of_utc


def _forecast_policy_version(code_root: Path) -> str:
    value = tomllib.loads((code_root / "configs" / "forecast_policy_v1.toml").read_text())
    version = value.get("policy_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("forecast policy version is missing")
    return version


def _policy_hashes(code_root: Path) -> dict[str, str]:
    return {name: _sha256(code_root / "configs" / name) for name in _POLICY_FILES}


def _validated_registry_sha(environment: Mapping[str, str]) -> str:
    value = environment.get("NFL_V2_ARTIFACT_REGISTRY_SHA256")
    if value is None or _SHA256.fullmatch(value) is None:
        raise ValueError("explicit reviewed artifact registry SHA is required")
    return value


def _reviewed_champions(
    registry_path: Path, expected_sha256: str
) -> Mapping[Origin, tuple[str, Mapping[str, str]]]:
    try:
        payload = registry_path.read_bytes()
    except OSError as error:
        raise ValueError("reviewed artifact registry is unreadable") from error
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("artifact registry bytes do not match the reviewed SHA")
    try:
        document = json.loads(payload)
        entries = document["entries"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("reviewed artifact registry is invalid") from error
    if not isinstance(entries, list):
        raise TypeError("reviewed artifact registry entries must be a list")
    champions: dict[Origin, tuple[str, Mapping[str, str]]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("model_role") != "champion":
            continue
        artifact_id = entry.get("artifact_id")
        origin_value = entry.get("origin")
        policy_versions = entry.get("policy_versions")
        if (
            isinstance(artifact_id, str)
            and artifact_id.strip()
            and isinstance(policy_versions, dict)
            and all(
                isinstance(key, str)
                and key.strip()
                and isinstance(value, str)
                and value.strip()
                for key, value in policy_versions.items()
            )
        ):
            if not isinstance(origin_value, str):
                raise ValueError("reviewed champion has an invalid origin")
            try:
                origin = Origin(origin_value)
            except (TypeError, ValueError) as error:
                raise ValueError("reviewed champion has an invalid origin") from error
            if origin in champions:
                raise ValueError("reviewed registry has multiple champions for one origin")
            champions[origin] = (artifact_id, cast(dict[str, str], policy_versions))
    if not champions:
        raise ValueError("reviewed artifact registry has no champion")
    return champions


class _OriginCandidatePolicy(CandidatePolicy):
    """Routes each prediction to its origin's concrete frozen candidate policy."""

    def __init__(self, policies: Mapping[Origin, CandidatePolicy]) -> None:
        self._policies = dict(policies)

    def evaluate(
        self,
        prediction: Prediction,
        event_state: OfficialEventState,
        quotes: Iterable[MoneylineQuote],
        calibration_bins: CalibrationBins,
        source_match: TrustedEventSourceMatch,
    ) -> tuple[CandidateEvaluation, ...]:
        try:
            policy = self._policies[prediction.origin]
        except KeyError as error:
            raise ValueError("prediction origin has no reviewed champion policy") from error
        return policy.evaluate(
            prediction,
            event_state,
            quotes,
            calibration_bins,
            source_match,
        )


def _private_config(paths: RuntimePaths) -> dict[str, Any]:
    path = paths.data_root / "config" / "data_repo.toml"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("private runtime configuration is unreadable") from error
    if not isinstance(value, dict):
        raise TypeError("private runtime configuration must be a table")
    return value


@dataclass(frozen=True)
class RuntimeComponents:
    app: AppConfig
    paths: RuntimePaths
    clock: Callable[[], datetime]
    clients: RuntimeClients = field(repr=False)
    lineage: DurableLineageRepository
    artifact_registry: VerifiedArtifactRegistry
    required_capture: RequiredFootballCapture
    optional_odds_capture: OptionalOddsCapture
    predictor: VerifiedForecastPredictor
    market_layer: ProductionMarketLayer
    feature_builder: FeatureBuilder
    odds_policy: OddsPolicy
    outcome_adapter: ArrowOutcomeAdapter
    outcome_repository: DurableOutcomeReportRepository
    outcome_workflow: OutcomeWorkflow
    report_workflow: ReportWorkflow
    scheduler_freshness_policy: SchedulerFreshnessPolicy
    dependency_lock_sha256: str
    forecast_policy_version: str
    code_sha: str
    expected_policy_sha256: Mapping[str, str]

    @classmethod
    def build(
        cls,
        *,
        app: AppConfig,
        paths: RuntimePaths,
        lineage: DurableLineageRepository,
        artifact_registry: VerifiedArtifactRegistry,
        feature_builder: FeatureBuilder,
        environment: Mapping[str, str],
        clock: Callable[[], datetime],
        clients: RuntimeClients | None,
    ) -> RuntimeComponents:
        active_clients = clients or RuntimeClients()
        config = _private_config(paths)
        authorization = config.get("authorization")
        if not isinstance(authorization, dict):
            raise TypeError("private authorization configuration is missing")
        code_sha = authorization.get("approved_public_code_sha")
        if not isinstance(code_sha, str) or _CODE_SHA.fullmatch(code_sha) is None:
            raise ValueError("reviewed public code SHA is required")
        lock_path = app.code_root / "uv.lock"
        dependency_lock_sha256 = _sha256(lock_path)
        build_identity = BuildIdentity(code_sha, dependency_lock_sha256)
        capture_service = CaptureService(paths.data_root, lambda *_: None, build_identity)
        feature_policy = load_feature_policy(app.code_root / "configs" / "feature_policy_v1.toml")
        normalizer = NflverseFootballNormalizer(feature_policy)
        nflverse = NflverseAdapter(active_clients.nflverse_loaders or LOADERS, clock)
        required_capture = RequiredFootballCapture(
            capture_service=capture_service,
            adapter=nflverse,
            normalizer=normalizer,
            lineage=lineage,
        )
        odds_policy = load_odds_policy(app.code_root / "configs" / "odds_policy_v1.toml")
        budget = OddsBudget(
            FileLockBudgetRepository(paths.data_root / "ledger" / "odds-budget.json"),
            odds_policy.monthly_hard_stop,
        )

        def active_events() -> tuple[EventVersion, ...]:
            return tuple(cls._load_active_events(paths, lineage))

        optional_odds_capture = OptionalOddsCapture(
            policy=odds_policy,
            api_key=environment.get("ODDS_API_KEY"),
            budget=budget,
            capture_service=capture_service,
            adapter_factory=lambda key: OddsApiAdapter(
                key, client=active_clients.odds_http_client, clock=clock
            ),
            active_events=active_events,
            month=lambda obligation: obligation.window.target_at_utc.strftime("%Y-%m"),
            lineage=lineage,
        )
        reviewed_champions = _reviewed_champions(
            paths.data_root / "config" / "artifact-registry.json",
            _validated_registry_sha(environment),
        )
        candidate_policies: dict[Origin, CandidatePolicy] = {}
        for origin, (champion_id, champion_policy_versions) in reviewed_champions.items():
            model_policy_version = champion_policy_versions.get("model")
            if model_policy_version is None:
                raise ValueError("reviewed champion is missing its model policy binding")
            candidate_policies[origin] = CandidatePolicy(
                odds_policy,
                odds_policy,
                _CandidateIds(),
                CandidateContext(
                    champion_id,
                    {
                        "forecast": _forecast_policy_version(app.code_root),
                        "model": model_policy_version,
                    },
                ),
                InMemoryDecisionRepository(),
            )
        candidate_policy = _OriginCandidatePolicy(candidate_policies)
        calibration_bins = DurableCalibrationBins(
            paths.data_root,
            paths.data_root / "config" / "calibration-bins.json",
        )
        market_layer = ProductionMarketLayer(
            active_event=lambda event_id: next(
                (event for event in active_events() if event.canonical_event_id == event_id), None
            ),
            odds_policy=odds_policy,
            candidate_policy=candidate_policy,
            calibration_bins=calibration_bins,
            comparator_id=lambda prediction, quote_ids: hashlib.sha256(
                f"{prediction.prediction_id}|{'|'.join(quote_ids)}".encode()
            ).hexdigest(),
        )
        predictor = VerifiedForecastPredictor(artifact_registry)
        outcome_adapter = ArrowOutcomeAdapter(capture_service, nflverse)
        outcome_repository = DurableOutcomeReportRepository(
            paths.outcome_report_root, artifact_registry
        )
        outcome_workflow = OutcomeWorkflow(outcome_adapter, outcome_repository)
        report_workflow = ReportWorkflow(
            outcome_repository,
            evaluation_policy=load_evaluation_policy(
                app.code_root / "configs" / "evaluation_policy_v1.toml"
            ),
            interval_confidence=Decimal("0.95"),
        )
        return cls(
            app=app,
            paths=paths,
            clock=clock,
            clients=active_clients,
            lineage=lineage,
            artifact_registry=artifact_registry,
            required_capture=required_capture,
            optional_odds_capture=optional_odds_capture,
            predictor=predictor,
            market_layer=market_layer,
            feature_builder=feature_builder,
            odds_policy=odds_policy,
            outcome_adapter=outcome_adapter,
            outcome_repository=outcome_repository,
            outcome_workflow=outcome_workflow,
            report_workflow=report_workflow,
            scheduler_freshness_policy=SchedulerFreshnessPolicy(
                "scheduler-freshness-v1", timedelta(minutes=5)
            ),
            dependency_lock_sha256=dependency_lock_sha256,
            forecast_policy_version=_forecast_policy_version(app.code_root),
            code_sha=code_sha,
            expected_policy_sha256=_policy_hashes(app.code_root),
        )

    @cached_property
    def prospective_repository(self) -> DurableForecastRepository:
        return DurableForecastRepository(
            self.paths.prospective_forecast_root,
            "prospective",
            artifact_resolver=self.artifact_registry,
        )

    @cached_property
    def replay_repository(self) -> DurableForecastRepository:
        return DurableForecastRepository(
            self.paths.replay_forecast_root,
            "replay",
            artifact_resolver=self.artifact_registry,
        )

    @cached_property
    def forecast_workflow(self) -> ForecastWorkflow:
        return ForecastWorkflow(
            repositories=ForecastRepositories(
                self.prospective_repository, self.replay_repository
            ),
            required_capture=self.required_capture,
            market_capture=self.optional_odds_capture,
            feature_builder=cast(SnapshotBuilder, self.feature_builder),
            lineage_repository=self.lineage,
            artifact_registry=self.artifact_registry,
            predictor=self.predictor,
            market_layer=self.market_layer,
            clock=self.clock,
            code_sha=self.code_sha,
        )

    @staticmethod
    def _load_active_events(
        paths: RuntimePaths, lineage: DurableLineageRepository
    ) -> list[EventVersion]:
        config = _private_config(paths)
        schedule = config.get("schedule")
        if not isinstance(schedule, dict):
            raise TypeError("private schedule configuration is missing")
        expected = schedule.get("active_event_version_manifest_sha256")
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            raise ValueError("active event manifest SHA is missing or invalid")
        return lineage.load_active_events(paths.data_root / "active-events.json", expected)

    def service_registry(self) -> ServiceRegistry:
        return ServiceRegistry(
            schedule_sync=self.schedule_sync,
            forecast_due=self.forecast_due,
            forecast_run=self.forecast_run,
            outcomes_sync=self.outcomes_sync,
            settle=self.settle,
            report_weekly=self.report_weekly,
            odds_budget_plan=self.odds_budget_plan,
            readiness_check=self.readiness_check,
            schedule_render_dispatch=self.schedule_render_dispatch,
            dispatch_due=self.dispatch_due,
        )

    def active_events(self) -> tuple[EventVersion, ...]:
        return tuple(self._load_active_events(self.paths, self.lineage))

    @staticmethod
    def _verified_candidate_sha(
        path: Path, expected_events: tuple[EventVersion, ...]
    ) -> str:
        try:
            payload = path.read_bytes()
            parquet = pq.ParquetFile(BytesIO(payload))
            if not parquet.schema_arrow.equals(schema_for(EventVersion), check_metadata=True):
                raise DataIntegrityError("candidate event schema does not match EventVersion V1")
            events = tuple(
                DurableLineageRepository._event_from_parquet_row(row)
                for row in parquet.read().to_pylist()
            )
        except DataIntegrityError:
            raise
        except Exception as error:
            raise DataIntegrityError("candidate event bytes are unreadable") from error
        if events != expected_events:
            raise DataIntegrityError("candidate event content conflicts with intended records")
        return hashlib.sha256(payload).hexdigest()

    def schedule_sync(
        self, *, season: int, context: ForecastExecutionContext
    ) -> dict[str, object]:
        existing = list(self.active_events())
        adapter = self.required_capture.adapter
        service = self.required_capture.capture_service
        manifest = service.capture(
            adapter,
            {"dataset": "schedules", "seasons": [season]},
            f"schedule-sync-{season}-{_as_of(context).isoformat()}",
        )
        raw_path = self.paths.data_root / manifest.raw_path
        proposed = self.required_capture.normalizer.normalize_events(raw_path.read_bytes(), manifest)
        reconciler = EventReconciler()
        candidates: list[EventVersion] = []
        quarantined: list[str] = []
        for proposed_event in proposed:
            fact = ScheduleFact(
                source="nflverse",
                source_event_id=proposed_event.source_event_ids["nflverse"],
                season=proposed_event.season,
                season_type=proposed_event.season_type,
                week=proposed_event.week,
                home_team=proposed_event.home_team,
                away_team=proposed_event.away_team,
                kickoff_at_utc=proposed_event.kickoff_at_utc,
                venue_id=proposed_event.venue_id,
                neutral_site=proposed_event.neutral_site,
                observed_at_utc=proposed_event.observed_at_utc,
                available_at_utc=proposed_event.available_at_utc,
                captured_at_utc=proposed_event.captured_at_utc,
                raw_payload_sha256=proposed_event.raw_payload_sha256,
            )
            reconciled = reconciler.reconcile(fact, [*existing, *candidates])
            if isinstance(reconciled, EventVersion):
                candidates.append(reconciled)
            else:
                quarantined.append(reconciled.reason_code)
        ordered = tuple(sorted(candidates, key=lambda item: item.canonical_event_id))
        semantic_sha = hashlib.sha256(
            canonical_json_bytes([event.model_dump(mode="json") for event in ordered])
        ).hexdigest()
        event_path = self.paths.data_root / "candidates" / f"active-events-{season}-{semantic_sha}.parquet"
        if event_path.exists():
            event_sha = self._verified_candidate_sha(event_path, ordered)
        else:
            event_sha = write_contracts(event_path, ordered, schema_for(EventVersion))
        event_keys = [
            {
                "canonical_event_id": event.canonical_event_id,
                "event_version": event.event_version,
            }
            for event in ordered
        ]
        candidate_manifest = {
            "schema_version": "active-events-v1",
            "season": season,
            "files": [
                {
                    "path": event_path.relative_to(self.paths.data_root).as_posix(),
                    "sha256": event_sha,
                    "events": event_keys,
                }
            ],
        }
        manifest_bytes = canonical_json_bytes(candidate_manifest)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        candidate_path = self.paths.data_root / "candidates" / f"active-events-{season}-{manifest_sha}.json"
        CaptureService._write_once(candidate_path, manifest_bytes)
        self.lineage.append_manifests((manifest,))
        self.lineage.append_events(ordered)
        return {
            "route": "schedule.sync",
            "candidate_manifest_path": candidate_path.relative_to(self.paths.data_root).as_posix(),
            "candidate_manifest_sha256": manifest_sha,
            "event_file_sha256": event_sha,
            "event_count": len(ordered),
            "quarantined": quarantined,
            "activated": False,
        }

    def forecast_due(
        self,
        *,
        trigger: str,
        context: ForecastExecutionContext,
        dry_run: bool = False,
    ) -> dict[str, object]:
        events = self.active_events()
        clusters = due_clusters(events, _as_of(context))
        if dry_run:
            return {"route": "forecast.due", "clusters": clusters, "runs": []}
        by_id = {event.canonical_event_id: event for event in events}
        runs = []
        for cluster in clusters:
            for event_id in cluster.event_ids:
                event = by_id[event_id]
                obligation = OriginObligation(
                    event,
                    ForecastOrigin.for_kickoff(cluster.origin, event.kickoff_at_utc),
                    self.forecast_policy_version,
                )
                runs.append(self.forecast_workflow.run(obligation, cast(Any, trigger), context))
        return {"route": "forecast.due", "clusters": clusters, "runs": runs}

    def forecast_run(
        self,
        *,
        event_id: str,
        origin: str,
        trigger: str,
        context: ForecastExecutionContext,
    ) -> object:
        matches = [event for event in self.active_events() if event.canonical_event_id == event_id]
        if len(matches) != 1:
            raise ValueError("forecast event must resolve to one verified active event")
        parsed_origin = Origin(origin)
        obligation = OriginObligation(
            matches[0],
            ForecastOrigin.for_kickoff(parsed_origin, matches[0].kickoff_at_utc),
            self.forecast_policy_version,
        )
        return self.forecast_workflow.run(obligation, cast(Any, trigger), context)

    def outcomes_sync(
        self, *, season: int, through_week: int, context: ForecastExecutionContext
    ) -> dict[str, object]:
        del context
        imported_obligations = self._import_missing_forecast_obligations(
            season, through_week
        )
        events = tuple(
            event
            for event in self.active_events()
            if event.season == season and event.week <= through_week
        )
        runs = [
            self.outcome_workflow.run(
                event,
                request={
                    "dataset": "schedules",
                    "seasons": [season],
                    "source_event_id": event.source_event_ids["nflverse"],
                },
            )
            for event in events
        ]
        return {
            "route": "outcomes.sync",
            "imported_forecast_obligations": imported_obligations,
            "runs": runs,
        }

    def _prospective_execution_keys(self) -> tuple[str, ...]:
        marker_root = (
            self.paths.prospective_forecast_root / "commits" / "forecast-terminal-prospective"
        )
        if not marker_root.exists():
            return ()
        keys: list[str] = []
        for marker_path in sorted(marker_root.glob("*.json")):
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            key = marker.get("idempotency_key")
            if not isinstance(key, str) or not key:
                raise DataIntegrityError("forecast terminal marker identity is invalid")
            keys.append(key)
        return tuple(keys)

    def _import_missing_forecast_obligations(
        self, season: int, through_week: int
    ) -> int:
        existing = {
            bundle.obligation.execution_key
            for bundle in self.outcome_repository.forecast_obligation_bundles()
        }
        imported = 0
        for key in self._prospective_execution_keys():
            graph = self.prospective_repository.load_committed_graph(key)
            if (
                graph.obligation.event.season != season
                or graph.obligation.event.week > through_week
                or graph.obligation.execution_key in existing
            ):
                continue
            self.outcome_repository.import_forecast_obligation(
                self.prospective_repository, key
            )
            existing.add(graph.obligation.execution_key)
            imported += 1
        return imported

    def _reconcile_committed_outcome(self, event: EventVersion) -> int:
        outcome = self.outcome_repository.latest_outcome(
            event.canonical_event_id, "nflverse"
        )
        if outcome is None:
            return 0
        captures = self.outcome_repository._captures_for_outcome(outcome)
        if not captures:
            raise DataIntegrityError("committed outcome has no exact capture manifest")
        manifest = max(
            captures,
            key=lambda item: (item.response_received_at_utc, item.capture_id),
        )
        settlements, _ = self.outcome_workflow._reconcile_settlements(
            event, outcome, manifest
        )
        return len(settlements)

    def settle(
        self, *, season: int, through_week: int, context: ForecastExecutionContext
    ) -> dict[str, object]:
        del context
        imported = 0
        reconciled = 0
        for key in self._prospective_execution_keys():
            graph = self.prospective_repository.load_committed_graph(key)
            if graph.obligation.event.season != season or graph.obligation.event.week > through_week:
                continue
            try:
                self.outcome_repository.load_forecast_graph(graph.run.origin_run_id)
            except KeyError:
                pass
            else:
                reconciled += self._reconcile_committed_outcome(
                    graph.obligation.event
                )
                continue
            self.outcome_repository.import_forecast_graph(
                self.prospective_repository, key
            )
            imported += 1
            reconciled += self._reconcile_committed_outcome(graph.obligation.event)
        return {
            "route": "settle",
            "imported_forecast_graphs": imported,
            "reconciled_settlements": reconciled,
            "settlement_count": len(self.outcome_repository.settlement_envelopes()),
        }

    def report_weekly(
        self, *, season: int, through_week: int, context: ForecastExecutionContext
    ) -> object:
        del context
        return self.report_workflow.weekly(season, through_week)

    def _verified_dispatch_schedule(self, schedule: Mapping[str, object]) -> str:
        self.active_events()
        relative_manifest = schedule.get("manifest_path")
        declared_manifest_sha = schedule.get("manifest_sha256")
        active_manifest_sha = schedule.get("active_event_version_manifest_sha256")
        if not isinstance(relative_manifest, str) or not relative_manifest:
            raise ValueError("dispatch schedule manifest path is missing")
        if (
            not isinstance(declared_manifest_sha, str)
            or _SHA256.fullmatch(declared_manifest_sha) is None
        ):
            raise ValueError("dispatch schedule manifest hash is invalid")
        root = self.paths.data_root.resolve(strict=True)
        manifest_path = (root / relative_manifest).resolve(strict=True)
        try:
            manifest_path.relative_to(root)
        except ValueError as error:
            raise ValueError("dispatch schedule manifest escapes private root") from error
        raw_manifest = manifest_path.read_bytes()
        if hashlib.sha256(raw_manifest).hexdigest() != declared_manifest_sha:
            raise ValueError("dispatch schedule manifest hash does not match")
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError as error:
            raise ValueError("dispatch schedule manifest is invalid") from error
        if not isinstance(manifest, dict):
            raise TypeError("dispatch schedule manifest must be an object")
        if not (
            schedule.get("generated_from_active_event_versions") is True
            and manifest.get("generated_from_active_event_versions") is True
            and isinstance(active_manifest_sha, str)
            and _SHA256.fullmatch(active_manifest_sha)
            and manifest.get("active_event_version_manifest_sha256")
            == active_manifest_sha
        ):
            raise ValueError("dispatch schedule is not bound to active event versions")
        return declared_manifest_sha

    def odds_budget_plan(
        self, *, season: int, context: ForecastExecutionContext
    ) -> dict[str, object]:
        del context
        config = _private_config(self.paths)
        if config.get("season") != season:
            raise ValueError("budget-plan season does not match private configuration")
        projection = config.get("cost_projection")
        schedule = config.get("schedule")
        if not isinstance(projection, dict) or not isinstance(schedule, dict):
            raise TypeError("private cost or schedule configuration is missing")
        jobs = tuple(
            JobProjection(name, int(projection[name]), int(projection[seconds]))
            for name, seconds in (
                ("private_due_ticks", "private_due_seconds_each"),
                ("full_forecast_workers", "full_forecast_seconds_each"),
                ("settlement_jobs", "settlement_seconds_each"),
                ("correction_jobs", "correction_seconds_each"),
                ("retry_allowance", "retry_seconds_each"),
            )
        )
        plan = plan_private_usage(
            jobs,
            projected_storage_bytes=int(projection["projected_storage_bytes"]),
            verified_included_actions_minutes_remaining=int(
                projection["verified_included_private_actions_minutes"]
            ),
            verified_included_storage_bytes_remaining=int(
                projection["verified_included_storage_bytes"]
            ),
            zero_dollar_mode=False,
        )
        blockers: list[str] = []
        stored_projection = (
            int(projection["projected_github_billed_minutes"]),
            int(projection["projected_paid_actions_minutes"]),
            int(projection["projected_paid_storage_bytes"]),
        )
        computed_projection = (
            plan.github_billed_minutes,
            plan.projected_paid_actions_minutes,
            plan.projected_paid_storage_bytes,
        )
        if stored_projection != computed_projection:
            blockers.append("STORED_USAGE_PROJECTION_MISMATCH")
        stored_odds_requests = int(projection["projected_odds_requests"])
        projected_odds_requests = self.odds_policy.worst_case_monthly_credits or 0
        if stored_odds_requests != projected_odds_requests:
            blockers.append("STORED_ODDS_REQUEST_PROJECTION_MISMATCH")
        if max(stored_odds_requests, projected_odds_requests) > self.odds_policy.monthly_hard_stop:
            blockers.append("ODDS_REQUEST_BUDGET_EXCEEDS_POLICY")
        if max(stored_odds_requests, projected_odds_requests) > 400:
            blockers.append("ODDS_REQUEST_BUDGET_EXCEEDS_400")
        if bool(config.get("zero_dollar_mode")) and (
            plan.projected_paid_actions_minutes or plan.projected_paid_storage_bytes
        ):
            blockers.append("PROJECTED_PAID_USAGE_NONZERO")
        schedule_complete = False
        try:
            self._verified_dispatch_schedule(schedule)
            schedule_complete = True
        except (DataIntegrityError, OSError, TypeError, ValueError):
            blockers.append("ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE")
        if not config.get("deployment_enabled"):
            blockers.append("DEPLOYMENT_DISABLED")
        return {
            "route": "odds.budget-plan",
            "season": season,
            "deployment_allowed": not blockers,
            "blockers": blockers,
            "github_billed_minutes": plan.github_billed_minutes,
            "projected_odds_requests": projected_odds_requests,
            "stored_projected_odds_requests": stored_odds_requests,
            "odds_monthly_hard_stop": self.odds_policy.monthly_hard_stop,
            "projected_paid_actions_minutes": plan.projected_paid_actions_minutes,
            "projected_paid_storage_bytes": plan.projected_paid_storage_bytes,
            "schedule_complete": schedule_complete,
            "zero_dollar_mode": bool(config.get("zero_dollar_mode")),
        }

    def readiness_check(self, *, context: ForecastExecutionContext) -> dict[str, object]:
        blockers: list[str] = []
        hashes: dict[str, object] = {}
        try:
            root = self.paths.data_root.resolve(strict=True)
            root.relative_to(self.app.code_root.resolve(strict=True))
        except ValueError:
            pass
        except OSError:
            blockers.append("DATA_ROOT_INVALID")
        else:
            blockers.append("DATA_ROOT_INSIDE_CODE_ROOT")
        try:
            events = self.active_events()
            hashes["active_event_manifest_sha256"] = _sha256(
                self.paths.data_root / "active-events.json"
            )
            hashes["active_event_count"] = len(events)
        except (DataIntegrityError, OSError, TypeError, ValueError):
            blockers.append("ACTIVE_SCHEDULE_INVALID")
        try:
            bindings = tuple(
                binding
                for origin in (Origin.T72, Origin.T60)
                for binding in self.artifact_registry.for_origin(origin)
            )
            hashes["artifact_ids"] = tuple(binding.artifact_id for binding in bindings)
            hashes["artifact_registry_sha256"] = _sha256(
                self.paths.data_root / "config" / "artifact-registry.json"
            )
            registry_document = json.loads(
                (self.paths.data_root / "config" / "artifact-registry.json").read_bytes()
            )
            hashes["artifact_sha256"] = {
                entry["artifact_id"]: {
                    "marker": entry["marker_sha256"],
                    "metadata": entry["metadata_sha256"],
                    "payload": entry["payload_sha256"],
                }
                for entry in registry_document["entries"]
            }
            artifact_identity_mismatch = False
            for binding in bindings:
                _, metadata = self.artifact_registry.artifact_and_metadata_for(binding)
                if metadata.dependency_lock_sha256 != self.dependency_lock_sha256:
                    artifact_identity_mismatch = True
            if artifact_identity_mismatch:
                blockers.append("ARTIFACT_DEPENDENCY_LOCK_MISMATCH")
        except Exception:  # noqa: BLE001 - readiness converts every trust failure to a blocker
            blockers.append("ARTIFACT_REGISTRY_INVALID")
        try:
            current_lock = _sha256(self.app.code_root / "uv.lock")
            hashes["dependency_lock_sha256"] = current_lock
            if current_lock != self.dependency_lock_sha256:
                blockers.append("DEPENDENCY_LOCK_CHANGED")
        except OSError:
            blockers.append("DEPENDENCY_LOCK_INVALID")
        try:
            current_policy_sha256 = _policy_hashes(self.app.code_root)
            hashes["policy_sha256"] = current_policy_sha256
            if current_policy_sha256 != self.expected_policy_sha256:
                blockers.append("POLICY_BINDING_CHANGED")
        except OSError:
            blockers.append("POLICY_BINDING_INVALID")
        try:
            budget = self.odds_budget_plan(
                season=int(_private_config(self.paths)["season"]), context=context
            )
            budget_blockers = cast(list[str], budget["blockers"])
            blockers.extend(blocker for blocker in budget_blockers if blocker not in blockers)
        except (KeyError, TypeError, ValueError):
            blockers.append("BUDGET_CONFIGURATION_INVALID")
        return {
            "route": "readiness.check",
            "ready": not blockers,
            "blockers": blockers,
            "hashes": hashes,
            "as_of_utc": context.as_of_utc,
        }

    def schedule_render_dispatch(
        self,
        *,
        season: int,
        public_offsets: tuple[int, ...],
        private_offsets: tuple[int, ...],
        context: ForecastExecutionContext,
    ) -> dict[str, object]:
        del context
        events = tuple(event for event in self.active_events() if event.season == season)
        if not events:
            raise ValueError("verified active schedule has no events for season")
        targets = [
            {
                "canonical_event_id": event.canonical_event_id,
                "origin": origin.value,
                "target_at_utc": target_for(event, origin),
            }
            for event in events
            for origin in (Origin.T72, Origin.T60)
        ]
        return {
            "route": "schedule.render-dispatch",
            "season": season,
            "active_event_version_manifest_sha256": _sha256(
                self.paths.data_root / "active-events.json"
            ),
            "origin_targets": targets,
            "public_crons": exact_cron_entries(events, public_offsets),
            "private_crons": exact_cron_entries(events, private_offsets),
        }

    def dispatch_due(
        self,
        *,
        dry_run: bool,
        authorization: DispatchAuthorization | None,
        context: ForecastExecutionContext,
    ) -> dict[str, object]:
        config = _private_config(self.paths)
        authorization_config = config.get("authorization")
        schedule = config.get("schedule")
        if not isinstance(authorization_config, dict) or not isinstance(schedule, dict):
            raise TypeError("dispatch configuration is incomplete")
        source_repository = str(authorization_config["source_public_repository"])
        default_branch = str(authorization_config["source_default_branch"])
        code_sha = str(authorization_config["approved_public_code_sha"])
        schedule_sha = self._verified_dispatch_schedule(schedule)
        envelopes = []
        for cluster in due_clusters(self.active_events(), _as_of(context)):
            nonce = hashlib.sha256(
                f"{cluster.cluster_id}|{code_sha}|{schedule_sha}".encode()
            ).hexdigest()
            envelopes.append(
                build_dispatch_payload(
                    source_repository=source_repository,
                    default_branch=default_branch,
                    cluster_id=cluster.cluster_id,
                    code_sha=code_sha,
                    nonce=nonce,
                    schedule_manifest_sha=schedule_sha,
                )
            )
        if dry_run:
            return {"route": "dispatch.due", "dry_run": True, "envelopes": envelopes}
        if authorization is None:
            raise ValueError("live dispatch requires explicit authorization")
        if authorization.target_repository != authorization_config.get("target_private_repository"):
            raise ValueError("dispatch authorization target does not match private configuration")
        budget = self.odds_budget_plan(season=int(config["season"]), context=context)
        if not budget["deployment_allowed"]:
            raise ValueError("live dispatch is blocked by deployment gates")
        client = self.clients.repository_dispatch_http_client
        owned_client: httpx.Client | None = None
        if client is None:
            owned_client = httpx.Client(timeout=20.0)
            client = owned_client
        try:
            dispatched = 0
            for envelope in envelopes:
                response = client.post(
                    f"https://api.github.com/repos/{authorization.target_repository}/dispatches",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {authorization.credential}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=repository_dispatch_request(envelope),
                )
                status_code = response.status_code
                if (
                    isinstance(status_code, bool)
                    or not isinstance(status_code, int)
                    or not 200 <= status_code < 300
                ):
                    raise ValueError(
                        f"repository dispatch failed with HTTP status {status_code}"
                    )
                dispatched += 1
        finally:
            if owned_client is not None:
                owned_client.close()
        return {
            "route": "dispatch.due",
            "dry_run": False,
            "dispatched": dispatched,
        }


def build_production_runtime(
    config_path: Path,
    environment: Mapping[str, str],
    clock: Callable[[], datetime],
    clients: RuntimeClients | None = None,
) -> ProductionRuntime:
    registry_sha = _validated_registry_sha(environment)
    app = load_app_config(config_path, environment=environment)
    paths = RuntimePaths.from_config(app)
    lineage = DurableLineageRepository(paths.lineage_root, paths.data_root)
    artifact_registry = VerifiedArtifactRegistry.from_private_config(
        paths.data_root,
        paths.artifact_root,
        paths.data_root / "config" / "artifact-registry.json",
        registry_sha,
    )
    feature_policy = load_feature_policy(app.code_root / "configs" / "feature_policy_v1.toml")
    feature_builder = FeatureBuilder(
        lineage,
        feature_policy,
        VenueStore.from_csv(app.code_root / "configs" / "venues_v1.csv"),
        PointInTimeRatingService(policy=feature_policy),
    )
    components = RuntimeComponents.build(
        app=app,
        paths=paths,
        lineage=lineage,
        artifact_registry=artifact_registry,
        feature_builder=feature_builder,
        environment=environment,
        clock=clock,
        clients=clients,
    )
    return ProductionRuntime(
        services=components.service_registry(),
        scheduler_policy=components.scheduler_freshness_policy,
    )
