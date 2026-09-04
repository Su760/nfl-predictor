from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nfl_predictor.cli import SchedulerFreshnessPolicy, ServiceRegistry, main
from nfl_predictor.workflows.dispatch import (
    DispatchAuthorization,
    DispatchPolicy,
    DispatchRejected,
    FileNonceStore,
    InMemoryNonceStore,
    JobProjection,
    PaidUsageRejected,
    build_dispatch_payload,
    plan_private_usage,
    repository_dispatch_request,
    validate_dispatch,
)
from nfl_predictor.workflows.forecast import ForecastExecutionContext

NOW = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
CODE_SHA = "a" * 40
MANIFEST_SHA = "b" * 64
CLUSTER_ID = "T72:2026-09-07T00:20:00+00:00"
SCHEDULER_POLICY = SchedulerFreshnessPolicy(
    policy_version="scheduler-freshness-v1",
    maximum_skew=timedelta(minutes=5),
)


@pytest.fixture
def dispatch_policy() -> DispatchPolicy:
    return DispatchPolicy(
        source_repository="Su760/nfl-predictor",
        default_branch="main",
        approved_code_shas=frozenset({CODE_SHA}),
        approved_schedule_manifest_shas=frozenset({MANIFEST_SHA}),
        allowed_cluster_ids=frozenset({CLUSTER_ID}),
    )


@pytest.fixture
def valid_payload() -> dict[str, str]:
    return {
        "event_type": "nfl_forecast_due",
        "source_repository": "Su760/nfl-predictor",
        "source_ref": "refs/heads/main",
        "cluster_id": CLUSTER_ID,
        "code_sha": CODE_SHA,
        "nonce": "20260907T002000Z-a1b2c3d4e5f6",
        "schedule_manifest_sha": MANIFEST_SHA,
    }


def test_private_dispatch_rejects_wrong_sender_ref_event_type_or_code_sha(
    valid_payload: dict[str, str], dispatch_policy: DispatchPolicy
) -> None:
    for field, value in {
        "source_repository": "attacker/repo",
        "source_ref": "refs/heads/unapproved",
        "event_type": "shell-command",
        "code_sha": "not-a-sha",
    }.items():
        with pytest.raises(DispatchRejected):
            validate_dispatch(
                {**valid_payload, field: value},
                policy=dispatch_policy,
                nonce_store=InMemoryNonceStore(),
            )


def test_dispatch_accepts_only_the_fixed_schema_and_approved_values(
    valid_payload: dict[str, str], dispatch_policy: DispatchPolicy
) -> None:
    validated = validate_dispatch(
        valid_payload,
        policy=dispatch_policy,
        nonce_store=InMemoryNonceStore(),
    )

    assert validated.cluster_id == CLUSTER_ID
    assert validated.code_sha == CODE_SHA
    assert validated.source_ref == "refs/heads/main"

    hostile_payloads: list[dict[str, object]] = [
        {**valid_payload, "event_ids": ["private-event"]},
        {key: value for key, value in valid_payload.items() if key != "nonce"},
        {**valid_payload, "nonce": 123},
        {**valid_payload, "schedule_manifest_sha": "c" * 64},
        {**valid_payload, "cluster_id": "T72:2026-09-14T00:20:00+00:00"},
        {**valid_payload, "code_sha": CODE_SHA.upper()},
    ]
    for payload in hostile_payloads:
        with pytest.raises(DispatchRejected):
            validate_dispatch(
                payload,
                policy=dispatch_policy,
                nonce_store=InMemoryNonceStore(),
            )


def test_dispatch_schema_rejects_non_string_keys_without_leaking_type_errors(
    valid_payload: dict[str, str], dispatch_policy: DispatchPolicy
) -> None:
    hostile: dict[object, object] = {**valid_payload, 1: "private-data"}

    with pytest.raises(DispatchRejected, match="schema"):
        validate_dispatch(
            hostile,
            policy=dispatch_policy,
            nonce_store=InMemoryNonceStore(),
        )


def test_dispatch_policy_rejects_an_impossible_utc_cluster_target() -> None:
    with pytest.raises(ValueError, match="cluster"):
        DispatchPolicy(
            source_repository="Su760/nfl-predictor",
            default_branch="main",
            approved_code_shas=frozenset({CODE_SHA}),
            approved_schedule_manifest_shas=frozenset({MANIFEST_SHA}),
            allowed_cluster_ids=frozenset({"T72:2026-99-99T99:99:99+00:00"}),
        )


def test_public_builder_rejects_hostile_default_branch_text() -> None:
    with pytest.raises(DispatchRejected, match="ref"):
        build_dispatch_payload(
            source_repository="Su760/nfl-predictor",
            default_branch="main\nattacker",
            cluster_id=CLUSTER_ID,
            code_sha=CODE_SHA,
            nonce="20260907T002000Z-a1b2c3d4e5f6",
            schedule_manifest_sha=MANIFEST_SHA,
        )


def test_explicit_empty_policy_environment_never_falls_back_to_process_state(
    monkeypatch: pytest.MonkeyPatch, valid_payload: dict[str, str]
) -> None:
    process_policy = {
        "NFL_PUBLIC_REPOSITORY": "Su760/nfl-predictor",
        "NFL_PUBLIC_DEFAULT_BRANCH": "main",
        "NFL_APPROVED_CODE_SHA": CODE_SHA,
        "NFL_APPROVED_SCHEDULE_MANIFEST_SHA": MANIFEST_SHA,
        "NFL_APPROVED_CLUSTER_IDS": CLUSTER_ID,
    }
    for name, value in process_policy.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(DispatchRejected, match="environment"):
        validate_dispatch(
            {**valid_payload, "nonce": "20260907T002000Z-explicit-empty"},
            environment={},
            nonce_store=InMemoryNonceStore(),
        )


def test_nonce_is_consumed_only_once_after_full_validation(
    valid_payload: dict[str, str], dispatch_policy: DispatchPolicy
) -> None:
    nonces = InMemoryNonceStore()

    validate_dispatch(valid_payload, policy=dispatch_policy, nonce_store=nonces)

    with pytest.raises(DispatchRejected, match="replay"):
        validate_dispatch(valid_payload, policy=dispatch_policy, nonce_store=nonces)


def test_file_nonce_store_persists_replay_defense(
    tmp_path: Path, valid_payload: dict[str, str], dispatch_policy: DispatchPolicy
) -> None:
    first_process = FileNonceStore(tmp_path / "nonces")
    validate_dispatch(valid_payload, policy=dispatch_policy, nonce_store=first_process)

    second_process = FileNonceStore(tmp_path / "nonces")
    with pytest.raises(DispatchRejected, match="replay"):
        validate_dispatch(valid_payload, policy=dispatch_policy, nonce_store=second_process)

    files = tuple((tmp_path / "nonces").iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".nonce")
    assert valid_payload["nonce"] not in files[0].name


def test_public_builder_emits_only_the_fixed_repository_dispatch_envelope(
    valid_payload: dict[str, str], dispatch_policy: DispatchPolicy
) -> None:
    built = build_dispatch_payload(
        source_repository=dispatch_policy.source_repository,
        default_branch=dispatch_policy.default_branch,
        cluster_id=CLUSTER_ID,
        code_sha=CODE_SHA,
        nonce=valid_payload["nonce"],
        schedule_manifest_sha=MANIFEST_SHA,
    )

    assert built == valid_payload
    assert repository_dispatch_request(built) == {
        "event_type": "nfl_forecast_due",
        "client_payload": valid_payload,
    }


def test_private_usage_projection_rounds_each_github_job_and_rejects_paid_usage() -> None:
    jobs = (
        JobProjection("private_due_ticks", count=8, seconds_per_job=15),
        JobProjection("full_forecast_workers", count=2, seconds_per_job=121),
        JobProjection("settlement_jobs", count=1, seconds_per_job=61),
        JobProjection("correction_jobs", count=1, seconds_per_job=1),
        JobProjection("retry_allowance", count=2, seconds_per_job=121),
    )

    plan = plan_private_usage(
        jobs,
        projected_storage_bytes=128,
        verified_included_actions_minutes_remaining=23,
        verified_included_storage_bytes_remaining=128,
        zero_dollar_mode=True,
    )

    assert plan.github_billed_minutes == 23
    assert plan.projected_paid_actions_minutes == 0
    assert plan.projected_paid_storage_bytes == 0

    with pytest.raises(PaidUsageRejected, match="paid Actions"):
        plan_private_usage(
            jobs,
            projected_storage_bytes=128,
            verified_included_actions_minutes_remaining=22,
            verified_included_storage_bytes_remaining=128,
            zero_dollar_mode=True,
        )

    with pytest.raises(PaidUsageRejected, match="paid storage"):
        plan_private_usage(
            jobs,
            projected_storage_bytes=129,
            verified_included_actions_minutes_remaining=23,
            verified_included_storage_bytes_remaining=128,
            zero_dollar_mode=True,
        )


class Task13Services:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def record(self, route: str, **values: object) -> dict[str, str]:
        self.calls.append((route, values))
        return {"route": route}

    def schedule_render_dispatch(
        self,
        *,
        season: int,
        public_offsets: tuple[int, ...],
        private_offsets: tuple[int, ...],
        context: ForecastExecutionContext,
    ) -> object:
        return self.record(
            "schedule.render-dispatch",
            season=season,
            public_offsets=public_offsets,
            private_offsets=private_offsets,
            context=context,
        )

    def dispatch_due(
        self,
        *,
        dry_run: bool,
        authorization: object,
        context: ForecastExecutionContext,
    ) -> object:
        return self.record(
            "dispatch.due",
            dry_run=dry_run,
            authorization=authorization,
            context=context,
        )

    def forecast_due(
        self,
        *,
        trigger: str,
        context: ForecastExecutionContext,
        dry_run: bool = False,
    ) -> object:
        return self.record("forecast.due", trigger=trigger, context=context, dry_run=dry_run)


def task13_registry(services: Task13Services) -> ServiceRegistry:
    unused = lambda **_: None
    registry = ServiceRegistry(
        schedule_sync=unused,
        forecast_due=services.forecast_due,
        forecast_run=unused,
        outcomes_sync=unused,
        settle=unused,
        report_weekly=unused,
        odds_budget_plan=unused,
        readiness_check=unused,
    )
    return replace(
        registry,
        schedule_render_dispatch=services.schedule_render_dispatch,
        dispatch_due=services.dispatch_due,
    )


def test_cli_routes_schedule_render_with_typed_offsets() -> None:
    services = Task13Services()

    main(
        [
            "schedule",
            "render-dispatch",
            "--season",
            "2026",
            "--public-offsets=-8,-3,2,7",
            "--private-offsets=-6,-1,4,9",
        ],
        services=task13_registry(services),
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
    )

    assert services.calls == [
        (
            "schedule.render-dispatch",
            {
                "season": 2026,
                "public_offsets": (-8, -3, 2, 7),
                "private_offsets": (-6, -1, 4, 9),
                "context": ForecastExecutionContext.live(NOW),
            },
        )
    ]


def test_cli_rejects_nonstandard_dispatch_offsets_before_rendering() -> None:
    services = Task13Services()

    with pytest.raises(SystemExit):
        main(
            [
                "schedule",
                "render-dispatch",
                "--season",
                "2026",
                "--public-offsets=-7,-3,2,7",
                "--private-offsets=-6,-1,4,9",
            ],
            services=task13_registry(services),
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )
    assert services.calls == []


class NoEnvironmentReads(dict[str, str]):
    def get(self, key: str, default: Any = None) -> str | Any:
        raise AssertionError(f"dry-run unexpectedly read environment key {key}")


def test_dispatch_due_dry_run_needs_no_authorization_environment_or_network() -> None:
    services = Task13Services()

    main(
        ["dispatch", "due", "--at", NOW.isoformat(), "--dry-run"],
        services=task13_registry(services),
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
        environment=NoEnvironmentReads(),
    )

    _, values = services.calls[0]
    assert values["dry_run"] is True
    assert values["authorization"] is None
    assert values["context"] == ForecastExecutionContext.live(NOW)


@pytest.mark.parametrize(
    ("argv", "route"),
    [
        (["dispatch", "due", "--at", NOW.isoformat(), "--dry-run"], "dispatch.due"),
        (
            [
                "forecast",
                "due",
                "--trigger",
                "fixture",
                "--at",
                NOW.isoformat(),
                "--dry-run",
            ],
            "forecast.due",
        ),
    ],
)
def test_unconfigured_offline_due_preview_reports_the_active_schedule_blocker(
    argv: list[str], route: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main(argv, clock=lambda: NOW, environment=NoEnvironmentReads())

    rendered = json.loads(capsys.readouterr().out)
    assert rendered == {
        "at": "2026-09-07T00:20:00+00:00",
        "due": False,
        "generated_from_active_event_versions": False,
        "reason": "ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE",
        "route": route,
        "schedule_manifest_sha": (
            "4f44499226db9a9ac65e06fdc1aa8c2a2182c6312597ffcf0c163988a5b7e3ff"
        ),
    }


def test_unconfigured_offline_budget_plan_reports_zero_dollar_blockers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["odds", "budget-plan", "--season", "2026"], clock=lambda: NOW)

    rendered = json.loads(capsys.readouterr().out)
    assert rendered == {
        "blockers": [
            "ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE",
            "PROJECTED_PAID_USAGE_NONZERO",
            "DEPLOYMENT_DISABLED",
        ],
        "deployment_allowed": False,
        "github_billed_minutes": 23,
        "projected_odds_requests": 6,
        "projected_paid_actions_minutes": 23,
        "projected_paid_storage_bytes": 104857600,
        "route": "odds.budget-plan",
        "schedule_complete": False,
        "season": 2026,
        "zero_dollar_mode": True,
    }


def test_dispatch_due_non_dry_run_requires_explicit_authorized_environment() -> None:
    services = Task13Services()
    registry = task13_registry(services)

    with pytest.raises(SystemExit):
        main(
            ["dispatch", "due", "--at", NOW.isoformat()],
            services=registry,
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
            environment={},
        )
    assert services.calls == []

    main(
        ["dispatch", "due", "--at", NOW.isoformat()],
        services=registry,
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
        environment={
            "NFL_V2_DEPLOYMENT_ENABLED": "true",
            "NFL_DATA_REPO": "Su760/nfl-predictor-data",
            "NFL_DATA_REPO_DISPATCH_TOKEN": "masked-test-credential",
        },
    )

    authorization = services.calls[0][1]["authorization"]
    assert isinstance(authorization, DispatchAuthorization)
    assert authorization.target_repository == "Su760/nfl-predictor-data"
    assert authorization.credential == "masked-test-credential"
    assert "masked-test-credential" not in repr(authorization)


def test_dispatch_due_rejects_a_whitespace_credential() -> None:
    services = Task13Services()

    with pytest.raises(SystemExit):
        main(
            ["dispatch", "due", "--at", NOW.isoformat()],
            services=task13_registry(services),
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
            environment={
                "NFL_V2_DEPLOYMENT_ENABLED": "true",
                "NFL_DATA_REPO": "Su760/nfl-predictor-data",
                "NFL_DATA_REPO_DISPATCH_TOKEN": "   ",
            },
        )
    assert services.calls == []


class EchoAuthorizationServices(Task13Services):
    def dispatch_due(
        self,
        *,
        dry_run: bool,
        authorization: object,
        context: ForecastExecutionContext,
    ) -> object:
        return authorization


def test_cli_never_serializes_a_dispatch_credential(
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = EchoAuthorizationServices()

    main(
        ["dispatch", "due", "--at", NOW.isoformat()],
        services=task13_registry(services),
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
        environment={
            "NFL_V2_DEPLOYMENT_ENABLED": "true",
            "NFL_DATA_REPO": "Su760/nfl-predictor-data",
            "NFL_DATA_REPO_DISPATCH_TOKEN": "never-print-this-credential",
        },
    )

    rendered = capsys.readouterr().out
    assert "never-print-this-credential" not in rendered
    assert rendered.strip() == '{"target_repository": "Su760/nfl-predictor-data"}'


def test_forecast_due_dry_run_preserves_live_and_replay_clock_semantics() -> None:
    services = Task13Services()
    historical = NOW - timedelta(days=7)

    main(
        [
            "forecast",
            "due",
            "--trigger",
            "fixture",
            "--dry-run",
            "--mode",
            "replay",
            "--at",
            historical.isoformat(),
        ],
        services=task13_registry(services),
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
    )

    context = services.calls[0][1]["context"]
    assert services.calls[0][1]["dry_run"] is True
    assert context == ForecastExecutionContext.replay(
        historical, "CALLER_REQUESTED_HISTORICAL_REPLAY"
    )
