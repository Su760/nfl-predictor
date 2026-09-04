from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import nfl_predictor.runtime as runtime_module
import nfl_predictor.runtime.services as services_module
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.runtime.artifacts import VerifiedForecastPredictor
from nfl_predictor.runtime.capture import (
    ArrowOutcomeAdapter,
    OptionalOddsCapture,
    RequiredFootballCapture,
)
from nfl_predictor.runtime.markets import ProductionMarketLayer
from nfl_predictor.storage import schema_for, write_contracts
from nfl_predictor.workflows.forecast import (
    DurableForecastRepository,
    ForecastExecutionContext,
    ForecastWorkflow,
)
from nfl_predictor.workflows.outcomes import DurableOutcomeReportRepository, OutcomeWorkflow
from nfl_predictor.workflows.report import ReportWorkflow

NOW = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
CODE_SHA = "1" * 40


class RuntimeTree:
    def __init__(self, tmp_path: Path) -> None:
        self.data_root = tmp_path / "private"
        self.data_root.mkdir()
        (self.data_root / "artifacts").mkdir()
        (self.data_root / "config").mkdir()
        (self.data_root / "config" / "calibration-bins.json").write_text(
            "[]", encoding="utf-8"
        )
        self.registry_path = self.data_root / "config" / "artifact-registry.json"
        self.registry_path.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "artifact_id": "fixture-champion",
                            "origin": "T60",
                            "model_role": "champion",
                            "model_lane": "football_only",
                            "feature_schema_version": "feature-schema-v1",
                            "feature_policy_version": "feature-v1",
                            "policy_versions": {
                                "model": "model-v1",
                                "calibration": "calibration-v1",
                            },
                            "market_policy_version": "odds-v1",
                            "candidate_policy_version": "candidate-v1",
                            "code_sha": CODE_SHA,
                            "dependency_lock_sha256": "3" * 64,
                            "marker_sha256": "4" * 64,
                            "metadata_sha256": "5" * 64,
                            "payload_sha256": "6" * 64,
                        }
                    ]
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.event_path = self.data_root / "events" / "active.parquet"
        event_sha = write_contracts(self.event_path, (), schema_for(EventVersion))
        self.active_manifest = self.data_root / "active-events.json"
        self.active_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "active-events-v1",
                    "season": 2026,
                    "files": [
                        {
                            "path": "events/active.parquet",
                            "sha256": event_sha,
                            "events": [],
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        active_sha = hashlib.sha256(self.active_manifest.read_bytes()).hexdigest()
        self.base_config = tmp_path / "base.toml"
        self.base_config.write_text(
            f'[paths]\ndata_root = "{self.data_root}"\n', encoding="utf-8"
        )
        (self.data_root / "config" / "data_repo.toml").write_text(
            "\n".join(
                (
                    "deployment_enabled = false",
                    "zero_dollar_mode = true",
                    'schema_version = "nfl-v2-private-data-v1"',
                    "season = 2026",
                    'month_lock = "2026-09"',
                    "",
                    "[authorization]",
                    'source_public_repository = "owner/public"',
                    'source_default_branch = "main"',
                    'target_private_repository = "owner/private"',
                    f'approved_public_code_sha = "{CODE_SHA}"',
                    'dispatch_event_type = "nfl_forecast_due"',
                    "",
                    "[schedule]",
                    'manifest_path = "config/dispatch-windows-2026.json"',
                    f'manifest_sha256 = "{"2" * 64}"',
                    f'active_event_version_manifest_sha256 = "{active_sha}"',
                    "generated_from_active_event_versions = true",
                    "public_offsets_minutes = [-8, -3, 2, 7]",
                    "private_offsets_minutes = [-6, -1, 4, 9]",
                    "",
                    "[cost_projection]",
                    'projection_status = "VERIFIED"',
                    'runner_class = "ubuntu-latest"',
                    'github_rounding = "ceil_each_job_to_whole_minute"',
                    "private_due_ticks = 0",
                    "private_due_seconds_each = 15",
                    "full_forecast_workers = 0",
                    "full_forecast_seconds_each = 121",
                    "settlement_jobs = 0",
                    "settlement_seconds_each = 61",
                    "correction_jobs = 0",
                    "correction_seconds_each = 1",
                    "retry_allowance = 0",
                    "retry_seconds_each = 121",
                    "projected_github_billed_minutes = 0",
                    "projected_odds_requests = 0",
                    "projected_storage_bytes = 0",
                    "verified_included_private_actions_minutes = 0",
                    "verified_included_storage_bytes = 0",
                    "projected_paid_actions_minutes = 0",
                    "projected_paid_storage_bytes = 0",
                    'verified_allowance_at_utc = "2026-09-01T00:00:00+00:00"',
                    "",
                    "[ledger]",
                    'nonce_directory = "ledger/dispatch-nonces"',
                    'budget_reservation_directory = "ledger/budget-reservations"',
                    'allowed_append_roots = ["ledger", "reports", "raw"]',
                    "",
                    "[safety]",
                    "football_only = true",
                    "automatic_wagering = false",
                    "allow_paid_usage = false",
                    "allow_unreviewed_schedule = false",
                    "allow_moving_public_ref = false",
                )
            ),
            encoding="utf-8",
        )
        dispatch = self.data_root / "config" / "dispatch-windows-2026.json"
        dispatch.write_text("{}", encoding="utf-8")
        self.environment = {
            "NFL_V2_ARTIFACT_REGISTRY_SHA256": hashlib.sha256(
                self.registry_path.read_bytes()
            ).hexdigest(),
        }
        self.context = ForecastExecutionContext.live(NOW)

    def runtime(self):
        builder = runtime_module.build_production_runtime
        return builder(self.base_config, self.environment, lambda: NOW)

    def add_champion(self, *, artifact_id: str, origin: str) -> None:
        document = json.loads(self.registry_path.read_text(encoding="utf-8"))
        entry = dict(document["entries"][0])
        entry.update({"artifact_id": artifact_id, "origin": origin})
        document["entries"].append(entry)
        self.registry_path.write_text(
            json.dumps(document, sort_keys=True), encoding="utf-8"
        )
        self.environment["NFL_V2_ARTIFACT_REGISTRY_SHA256"] = hashlib.sha256(
            self.registry_path.read_bytes()
        ).hexdigest()

    def install_due_event(self) -> EventVersion:
        event = EventVersion(
            canonical_event_id="event-due",
            event_version=1,
            source_event_ids={"nflverse": "2026_01_GB_CHI"},
            season=2026,
            season_type="REG",
            week=1,
            home_team="CHI",
            away_team="GB",
            kickoff_at_utc=NOW + timedelta(hours=72),
            venue_id="soldier-field",
            neutral_site=False,
            observed_at_utc=NOW - timedelta(days=7),
            available_at_utc=NOW - timedelta(days=7),
            captured_at_utc=NOW - timedelta(days=7),
            raw_payload_sha256="a" * 64,
        )
        self.event_path.unlink()
        event_sha = write_contracts(self.event_path, (event,), schema_for(EventVersion))
        self.active_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "active-events-v1",
                    "season": 2026,
                    "files": [
                        {
                            "path": "events/active.parquet",
                            "sha256": event_sha,
                            "events": [
                                {
                                    "canonical_event_id": event.canonical_event_id,
                                    "event_version": event.event_version,
                                }
                            ],
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        config_path = self.data_root / "config" / "data_repo.toml"
        config = config_path.read_text(encoding="utf-8")
        old_sha = next(
            line.split('"')[1]
            for line in config.splitlines()
            if line.startswith("active_event_version_manifest_sha256")
        )
        config_path.write_text(
            config.replace(old_sha, hashlib.sha256(self.active_manifest.read_bytes()).hexdigest()),
            encoding="utf-8",
        )
        return event


@pytest.fixture
def runtime_tree(tmp_path: Path) -> RuntimeTree:
    return RuntimeTree(tmp_path)


# Catches a composition root that leaves route callbacks unbound or uses preview placeholders.
def test_build_production_runtime_populates_every_service(runtime_tree: RuntimeTree) -> None:
    runtime = runtime_tree.runtime()

    assert runtime.services.schedule_render_dispatch is not None
    assert runtime.services.dispatch_due is not None
    assert callable(runtime.services.schedule_sync)
    assert callable(runtime.services.forecast_due)
    assert callable(runtime.services.forecast_run)
    assert callable(runtime.services.outcomes_sync)
    assert callable(runtime.services.settle)
    assert callable(runtime.services.report_weekly)
    assert callable(runtime.services.odds_budget_plan)
    assert callable(runtime.services.readiness_check)
    components = runtime.services.forecast_run.__self__
    assert isinstance(components.required_capture, RequiredFootballCapture)
    assert isinstance(components.optional_odds_capture, OptionalOddsCapture)
    assert isinstance(components.predictor, VerifiedForecastPredictor)
    assert isinstance(components.market_layer, ProductionMarketLayer)
    assert isinstance(components.forecast_workflow, ForecastWorkflow)
    assert isinstance(components.prospective_repository, DurableForecastRepository)
    assert isinstance(components.outcome_adapter, ArrowOutcomeAdapter)
    assert isinstance(components.outcome_repository, DurableOutcomeReportRepository)
    assert isinstance(components.outcome_workflow, OutcomeWorkflow)
    assert isinstance(components.report_workflow, ReportWorkflow)


# Catches self-pinning mutable registry bytes or consulting implicit process environment.
def test_build_requires_explicit_reviewed_registry_sha(runtime_tree: RuntimeTree) -> None:
    builder = runtime_module.build_production_runtime

    with pytest.raises(ValueError, match="registry SHA"):
        builder(runtime_tree.base_config, {}, lambda: NOW)


# Catches applying one origin's champion identity to every market-candidate evaluation.
def test_build_binds_each_origin_to_its_reviewed_champion(
    runtime_tree: RuntimeTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_tree.add_champion(artifact_id="fixture-champion-t72", origin="T72")
    contexts = []
    concrete_policy = services_module.CandidatePolicy

    class RecordingCandidatePolicy(concrete_policy):
        def __init__(self, *args, **kwargs) -> None:
            contexts.append(kwargs.get("context", args[3] if len(args) > 3 else None))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(services_module, "CandidatePolicy", RecordingCandidatePolicy)

    runtime_tree.runtime()

    assert {
        context.expected_champion_artifact_id: context.required_prediction_policy_versions["model"]
        for context in contexts
    } == {
        "fixture-champion": "model-v1",
        "fixture-champion-t72": "model-v1",
    }


# Catches readiness hashing only the manifest declaration rather than the bound event bytes.
def test_readiness_fails_closed_after_active_event_bytes_are_tampered(
    runtime_tree: RuntimeTree,
) -> None:
    runtime = runtime_tree.runtime()
    runtime_tree.event_path.write_bytes(b"tampered")

    result = runtime.services.readiness_check(context=runtime_tree.context)

    assert result["ready"] is False
    assert "ACTIVE_SCHEDULE_INVALID" in result["blockers"]


# Catches trusting reviewed projection output fields instead of recomputing every cap.
def test_budget_plan_blocks_a_stored_projection_mismatch(runtime_tree: RuntimeTree) -> None:
    config_path = runtime_tree.data_root / "config" / "data_repo.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "projected_github_billed_minutes = 0",
            "projected_github_billed_minutes = 1",
        ),
        encoding="utf-8",
    )
    runtime = runtime_tree.runtime()

    result = runtime.services.odds_budget_plan(season=2026, context=runtime_tree.context)

    assert result["deployment_allowed"] is False
    assert "STORED_USAGE_PROJECTION_MISMATCH" in result["blockers"]


class NoDispatchClient:
    def post(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("dry-run dispatch attempted an HTTP request")


# Catches dispatch planning using preview data or performing network I/O in dry-run mode.
def test_dispatch_due_dry_run_returns_exact_active_schedule_envelope_without_http(
    runtime_tree: RuntimeTree,
) -> None:
    runtime_tree.install_due_event()
    builder = runtime_module.build_production_runtime
    runtime = builder(
        runtime_tree.base_config,
        runtime_tree.environment,
        lambda: NOW,
        runtime_module.RuntimeClients(repository_dispatch_http_client=NoDispatchClient()),
    )

    result = runtime.services.dispatch_due(
        dry_run=True,
        authorization=None,
        context=runtime_tree.context,
    )

    assert result["dry_run"] is True
    assert result["envelopes"] == [
        {
            "event_type": "nfl_forecast_due",
            "source_repository": "owner/public",
            "source_ref": "refs/heads/main",
            "cluster_id": "T72:2026-09-07T00:20:00+00:00",
            "code_sha": CODE_SHA,
            "nonce": "3f7bb797935039552449b632a523e361e4c1f023a6a73c7ff32b8ec6366fa942",
            "schedule_manifest_sha": "2" * 64,
        }
    ]
