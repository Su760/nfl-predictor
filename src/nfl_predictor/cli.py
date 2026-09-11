from __future__ import annotations

import argparse
import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel

from nfl_predictor.workflows.dispatch import (
    DispatchAuthorization,
    JobProjection,
    plan_private_usage,
)
from nfl_predictor.workflows.forecast import ForecastExecutionContext
from nfl_predictor.workflows.schedule_windows import parse_strict_utc_z, project_active_schedule

ExecutionContext = ForecastExecutionContext


@dataclass(frozen=True)
class SchedulerFreshnessPolicy:
    policy_version: str
    maximum_skew: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("scheduler freshness policy version is required")
        if not isinstance(self.maximum_skew, timedelta):
            raise TypeError("scheduler maximum skew must be a timedelta")
        if self.maximum_skew <= timedelta(0):
            raise ValueError("scheduler maximum skew must be positive")


class ScheduleSyncService(Protocol):
    def __call__(self, *, season: int, context: ExecutionContext) -> object: ...


class ForecastDueService(Protocol):
    def __call__(
        self, *, trigger: str, context: ExecutionContext, dry_run: bool = False
    ) -> object: ...


class ForecastRunService(Protocol):
    def __call__(
        self, *, event_id: str, origin: str, trigger: str, context: ExecutionContext
    ) -> object: ...


class WeeklyService(Protocol):
    def __call__(self, *, season: int, through_week: int, context: ExecutionContext) -> object: ...


class SeasonService(Protocol):
    def __call__(self, *, season: int, context: ExecutionContext) -> object: ...


class ReadinessService(Protocol):
    def __call__(self, *, context: ExecutionContext) -> object: ...


class ScheduleRenderDispatchService(Protocol):
    def __call__(
        self,
        *,
        season: int,
        public_offsets: tuple[int, ...],
        private_offsets: tuple[int, ...],
        context: ExecutionContext,
    ) -> object: ...


class DispatchDueService(Protocol):
    def __call__(
        self,
        *,
        dry_run: bool,
        authorization: DispatchAuthorization | None,
        context: ExecutionContext,
    ) -> object: ...


@dataclass(frozen=True)
class ServiceRegistry:
    schedule_sync: ScheduleSyncService
    forecast_due: ForecastDueService
    forecast_run: ForecastRunService
    outcomes_sync: WeeklyService
    settle: WeeklyService
    report_weekly: WeeklyService
    odds_budget_plan: SeasonService
    readiness_check: ReadinessService
    schedule_render_dispatch: ScheduleRenderDispatchService | None = None
    dispatch_due: DispatchDueService | None = None


def _season(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--season", required=True, type=int)


def _through_week(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--through-week", required=True, type=int)


def _forecast_time(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--at")
    parser.add_argument("--mode", choices=("live", "replay"), default="live")


def _offsets(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("offsets must be comma-separated integers") from error
    if not parsed or len(set(parsed)) != len(parsed) or any(abs(item) > 10 for item in parsed):
        raise argparse.ArgumentTypeError("offsets must be unique values inside -10..10")
    return parsed


def register_core_commands(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", required=True)

    schedule = commands.add_parser("schedule")
    schedule_actions = schedule.add_subparsers(dest="schedule_action", required=True)
    schedule_sync = schedule_actions.add_parser("sync")
    _season(schedule_sync)
    schedule_sync.set_defaults(route="schedule.sync")
    schedule_render = schedule_actions.add_parser("render-dispatch")
    _season(schedule_render)
    schedule_render.add_argument("--public-offsets", type=_offsets, default=(-8, -3, 2, 7))
    schedule_render.add_argument("--private-offsets", type=_offsets, default=(-6, -1, 4, 9))
    schedule_render.set_defaults(route="schedule.render-dispatch")

    forecast = commands.add_parser("forecast")
    forecast_actions = forecast.add_subparsers(dest="forecast_action", required=True)
    forecast_due = forecast_actions.add_parser("due")
    forecast_due.add_argument(
        "--trigger",
        choices=("public_dispatch", "private_schedule", "manual", "fixture"),
        required=True,
    )
    _forecast_time(forecast_due)
    forecast_due.add_argument("--dry-run", action="store_true")
    forecast_due.set_defaults(route="forecast.due")
    forecast_run = forecast_actions.add_parser("run")
    forecast_run.add_argument("--event", required=True)
    forecast_run.add_argument("--origin", choices=("T72", "T60"), required=True)
    forecast_run.add_argument(
        "--trigger",
        choices=("public_dispatch", "private_schedule", "manual", "fixture"),
        default="manual",
    )
    _forecast_time(forecast_run)
    forecast_run.set_defaults(route="forecast.run")

    outcomes = commands.add_parser("outcomes")
    outcomes_actions = outcomes.add_subparsers(dest="outcomes_action", required=True)
    outcomes_sync = outcomes_actions.add_parser("sync")
    _season(outcomes_sync)
    _through_week(outcomes_sync)
    outcomes_sync.set_defaults(route="outcomes.sync")

    settle = commands.add_parser("settle")
    _season(settle)
    _through_week(settle)
    settle.set_defaults(route="settle")

    report = commands.add_parser("report")
    report_actions = report.add_subparsers(dest="report_action", required=True)
    report_weekly = report_actions.add_parser("weekly")
    _season(report_weekly)
    _through_week(report_weekly)
    report_weekly.set_defaults(route="report.weekly")

    odds = commands.add_parser("odds")
    odds_actions = odds.add_subparsers(dest="odds_action", required=True)
    odds_budget = odds_actions.add_parser("budget-plan")
    _season(odds_budget)
    odds_budget.add_argument("--private-config", type=Path)
    odds_budget.set_defaults(route="odds.budget-plan")

    readiness = commands.add_parser("readiness")
    readiness_actions = readiness.add_subparsers(dest="readiness_action", required=True)
    readiness_check = readiness_actions.add_parser("check")
    readiness_check.add_argument("--as-of")
    readiness_check.set_defaults(route="readiness.check")

    dispatch = commands.add_parser("dispatch")
    dispatch_actions = dispatch.add_subparsers(dest="dispatch_action", required=True)
    dispatch_due = dispatch_actions.add_parser("due")
    _forecast_time(dispatch_due)
    dispatch_due.add_argument("--dry-run", action="store_true")
    dispatch_due.set_defaults(route="dispatch.due")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nfl-predictor")
    register_core_commands(parser)
    return parser


def _utc(value: str, parser: argparse.ArgumentParser, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parser.error(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        parser.error(f"{field} must be timezone-aware UTC")
    return parsed


def _clock_value(clock: Callable[[], datetime], parser: argparse.ArgumentParser) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        parser.error("injected clock must return UTC")
    return value


def _context(
    arguments: argparse.Namespace,
    frozen_now: datetime,
    parser: argparse.ArgumentParser,
    scheduler_policy: SchedulerFreshnessPolicy,
) -> ExecutionContext:
    mode = getattr(arguments, "mode", "live")
    requested = getattr(arguments, "at", None)
    if mode == "replay":
        if requested is None:
            parser.error("replay mode requires --at")
        return ExecutionContext.replay(
            _utc(requested, parser, "--at"),
            "CALLER_REQUESTED_HISTORICAL_REPLAY",
        )
    requested_at: datetime | None = None
    if requested is not None:
        if getattr(arguments, "trigger", None) == "manual":
            try:
                requested_at = parse_strict_utc_z(requested)
            except ValueError as error:
                parser.error(str(error))
        else:
            requested_at = _utc(requested, parser, "--at")
    if (
        requested_at is not None
        and abs(requested_at - frozen_now) > scheduler_policy.maximum_skew
    ):
        parser.error("live --at is outside scheduler freshness; use --mode replay")
    readiness_as_of = getattr(arguments, "as_of", None)
    if (
        readiness_as_of is not None
        and abs(_utc(readiness_as_of, parser, "--as-of") - frozen_now)
        > scheduler_policy.maximum_skew
    ):
        parser.error("live --as-of is outside scheduler freshness")
    return ExecutionContext.live(frozen_now)


def _dispatch(
    arguments: argparse.Namespace,
    services: ServiceRegistry,
    context: ExecutionContext,
) -> object:
    route = arguments.route
    if route == "schedule.sync":
        return services.schedule_sync(season=arguments.season, context=context)
    if route == "schedule.render-dispatch":
        renderer = services.schedule_render_dispatch
        if renderer is None:
            raise RuntimeError("schedule dispatch renderer is not configured")
        return renderer(
            season=arguments.season,
            public_offsets=arguments.public_offsets,
            private_offsets=arguments.private_offsets,
            context=context,
        )
    if route == "forecast.due":
        if arguments.dry_run:
            return services.forecast_due(
                trigger=arguments.trigger,
                context=context,
                dry_run=True,
            )
        return services.forecast_due(trigger=arguments.trigger, context=context)
    if route == "dispatch.due":
        dispatcher = services.dispatch_due
        if dispatcher is None:
            raise RuntimeError("due dispatcher is not configured")
        return dispatcher(
            dry_run=arguments.dry_run,
            authorization=arguments.authorization,
            context=context,
        )
    if route == "forecast.run":
        return services.forecast_run(
            event_id=arguments.event,
            origin=arguments.origin,
            trigger=arguments.trigger,
            context=context,
        )
    if route == "outcomes.sync":
        return services.outcomes_sync(
            season=arguments.season,
            through_week=arguments.through_week,
            context=context,
        )
    if route == "settle":
        return services.settle(
            season=arguments.season,
            through_week=arguments.through_week,
            context=context,
        )
    if route == "report.weekly":
        return services.report_weekly(
            season=arguments.season,
            through_week=arguments.through_week,
            context=context,
        )
    if route == "odds.budget-plan":
        private_config = getattr(arguments, "private_config", None)
        if private_config is not None:
            return _private_budget_plan(private_config, arguments.season)
        return services.odds_budget_plan(season=arguments.season, context=context)
    if route == "readiness.check":
        return services.readiness_check(context=context)
    raise RuntimeError("unregistered command route")


def _json_value(value: object) -> Any:
    if isinstance(value, DispatchAuthorization):
        return {"target_repository": value.target_repository}
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported CLI result type: {type(value).__name__}")


def _unavailable_service(**_: object) -> object:
    raise RuntimeError("production service is not configured")


def _offline_due_preview(route: str, context: ExecutionContext) -> dict[str, object]:
    manifest_path = (
        Path(__file__).resolve().parents[2] / "deploy" / "schedules" / "dispatch-windows-2026.json"
    )
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    generated = manifest.get("generated_from_active_event_versions")
    if generated is not False:
        raise RuntimeError("active schedule previews require configured production services")
    return {
        "at": context.as_of_utc,
        "due": False,
        "generated_from_active_event_versions": False,
        "reason": "ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE",
        "route": route,
        "schedule_manifest_sha": hashlib.sha256(raw).hexdigest(),
    }


def _offline_forecast_due(
    *, trigger: str, context: ExecutionContext, dry_run: bool = False
) -> object:
    del trigger
    if not dry_run:
        raise RuntimeError("offline forecast due service is dry-run only")
    return _offline_due_preview("forecast.due", context)


def _offline_dispatch_due(
    *,
    dry_run: bool,
    authorization: DispatchAuthorization | None,
    context: ExecutionContext,
) -> object:
    if not dry_run or authorization is not None:
        raise RuntimeError("offline dispatch due service is dry-run only")
    return _offline_due_preview("dispatch.due", context)


def _private_budget_plan(config_path: Path, season: int) -> dict[str, object]:
    if not config_path.is_absolute():
        raise ValueError("private budget config path must be absolute")
    config_path = config_path.resolve(strict=True)
    private_root = config_path.parent.parent
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if season != config["season"]:
        raise ValueError("budget-plan season does not match the reviewed deployment config")
    projection = config["cost_projection"]
    if not isinstance(projection, dict):
        raise TypeError("private cost projection must be a table")
    jobs: tuple[JobProjection, ...] = (
        JobProjection(
            "private_due_ticks",
            projection["private_due_ticks"],
            projection["private_due_seconds_each"],
        ),
        JobProjection(
            "full_forecast_workers",
            projection["full_forecast_workers"],
            projection["full_forecast_seconds_each"],
        ),
        JobProjection(
            "settlement_jobs",
            projection["settlement_jobs"],
            projection["settlement_seconds_each"],
        ),
        JobProjection(
            "correction_jobs",
            projection["correction_jobs"],
            projection["correction_seconds_each"],
        ),
        JobProjection(
            "retry_allowance",
            projection["retry_allowance"],
            projection["retry_seconds_each"],
        ),
    )
    jobs += tuple(JobProjection(name, projection[name], projection[name + "_seconds_each"])
                  for name in ("nonce_admission_jobs", "budget_admission_jobs", "heartbeat_jobs")
                  if name in projection)
    plan = plan_private_usage(
        jobs,
        projected_storage_bytes=projection["projected_storage_bytes"],
        verified_included_actions_minutes_remaining=projection[
            "verified_included_private_actions_minutes"
        ],
        verified_included_storage_bytes_remaining=projection[
            "verified_included_storage_bytes"
        ],
        zero_dollar_mode=False,
    )
    stored_projection = (
        projection["projected_github_billed_minutes"],
        projection["projected_paid_actions_minutes"],
        projection["projected_paid_storage_bytes"],
    )
    computed_projection = (
        plan.github_billed_minutes,
        plan.projected_paid_actions_minutes,
        plan.projected_paid_storage_bytes,
    )
    blockers: list[str] = []
    if stored_projection != computed_projection:
        blockers.append("STORED_USAGE_PROJECTION_MISMATCH")

    projected_odds_requests = projection.get("projected_odds_requests")
    monthly_hard_stop = projection.get("monthly_hard_stop")
    if (
        isinstance(projected_odds_requests, bool)
        or not isinstance(projected_odds_requests, int)
        or projected_odds_requests < 0
        or isinstance(monthly_hard_stop, bool)
        or not isinstance(monthly_hard_stop, int)
        or monthly_hard_stop < 1
    ):
        raise ValueError("odds request projection and monthly hard stop must be integers")
    if projected_odds_requests > monthly_hard_stop:
        blockers.append("ODDS_REQUEST_BUDGET_EXCEEDS_POLICY")
    if monthly_hard_stop > 400 or projected_odds_requests > 400:
        blockers.append("ODDS_REQUEST_BUDGET_EXCEEDS_400")

    schedule = config["schedule"]
    manifest_path = (private_root / schedule["manifest_path"]).resolve(strict=True)
    try:
        manifest_path.relative_to(private_root.resolve(strict=True))
    except ValueError as error:
        raise ValueError("private schedule manifest escapes data root") from error
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    schedule_complete = bool(
        hashlib.sha256(manifest_raw).hexdigest() == schedule["manifest_sha256"]
        and schedule["generated_from_active_event_versions"]
        and manifest["generated_from_active_event_versions"]
        and schedule["active_event_version_manifest_sha256"]
        and manifest["active_event_version_manifest_sha256"]
        == schedule["active_event_version_manifest_sha256"]
        and config["season"] == manifest["season"] == 2026
    )
    if schedule_complete:
        from nfl_predictor.betting.policy import load_odds_policy
        from nfl_predictor.runtime.lineage import DurableLineageRepository
        from nfl_predictor.storage import DataIntegrityError

        try:
            active_path = (private_root / schedule["active_event_version_manifest_path"]).resolve(strict=True)
            active_path.relative_to(private_root)
            events = DurableLineageRepository(private_root / "lineage", private_root).load_active_events(
                active_path, schedule["active_event_version_manifest_sha256"])
            if not events or any(event.season != season for event in events):
                raise ValueError("active event schedule is empty or outside the reviewed season")
            actual_requests, expected_counts = project_active_schedule(
                events, tuple(schedule["public_offsets_minutes"]),
                tuple(schedule["private_offsets_minutes"]), projection.get("schedule_change_calls", 0),
            )
            if any(projection.get(name, 0) != count for name, count in expected_counts.items()):
                blockers.append("WORKFLOW_JOB_GRAPH_PROJECTION_MISMATCH")
            public_policy = load_odds_policy(
                Path(__file__).resolve().parents[2] / "configs/odds_policy_v1.toml")
            actual_requests = max(actual_requests, public_policy.worst_case_monthly_credits or 0)
            if projected_odds_requests != actual_requests:
                blockers.append("STORED_ODDS_REQUEST_PROJECTION_MISMATCH")
            projected_odds_requests = actual_requests
            if actual_requests > min(monthly_hard_stop, public_policy.monthly_hard_stop):
                blockers.append("ODDS_REQUEST_BUDGET_EXCEEDS_POLICY")
        except (DataIntegrityError, KeyError, OSError, TypeError, ValueError):
            schedule_complete = False
    if not schedule_complete:
        blockers.append("ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE")
    if not config["zero_dollar_mode"]:
        blockers.append("ZERO_DOLLAR_MODE_DISABLED")
    if plan.projected_paid_actions_minutes or plan.projected_paid_storage_bytes:
        blockers.append("PROJECTED_PAID_USAGE_NONZERO")
    if not config["deployment_enabled"]:
        blockers.append("DEPLOYMENT_DISABLED")
    return {
        "blockers": blockers,
        "deployment_allowed": not blockers,
        "github_billed_minutes": plan.github_billed_minutes,
        "odds_monthly_hard_stop": monthly_hard_stop,
        "projected_odds_requests": projected_odds_requests,
        "projected_paid_actions_minutes": plan.projected_paid_actions_minutes,
        "projected_paid_storage_bytes": plan.projected_paid_storage_bytes,
        "route": "odds.budget-plan",
        "schedule_complete": schedule_complete,
        "season": season,
        "zero_dollar_mode": config["zero_dollar_mode"],
    }


def _offline_odds_budget_plan(*, season: int, context: ExecutionContext) -> object:
    del context
    private_root = Path(__file__).resolve().parents[2] / "deploy" / "private-data-repo"
    return _private_budget_plan(private_root / "config/data_repo.toml", season)


def _offline_service_registry() -> ServiceRegistry:
    return ServiceRegistry(
        schedule_sync=cast(ScheduleSyncService, _unavailable_service),
        forecast_due=_offline_forecast_due,
        forecast_run=cast(ForecastRunService, _unavailable_service),
        outcomes_sync=cast(WeeklyService, _unavailable_service),
        settle=cast(WeeklyService, _unavailable_service),
        report_weekly=cast(WeeklyService, _unavailable_service),
        odds_budget_plan=_offline_odds_budget_plan,
        readiness_check=cast(ReadinessService, _unavailable_service),
        dispatch_due=_offline_dispatch_due,
    )


def _is_offline_preview(arguments: argparse.Namespace) -> bool:
    route = str(arguments.route)
    due_preview = route in {"forecast.due", "dispatch.due"} and bool(
        getattr(arguments, "dry_run", False)
    )
    return due_preview or route == "odds.budget-plan"


class _ProcessEntry:
    pass


_PROCESS_ENTRY = _ProcessEntry()


def main(
    argv: Sequence[str] | None | _ProcessEntry = _PROCESS_ENTRY,
    *,
    services: ServiceRegistry | None = None,
    clock: Callable[[], datetime] | None = None,
    scheduler_policy: SchedulerFreshnessPolicy | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    if argv is _PROCESS_ENTRY:
        if (
            services is None
            and clock is None
            and scheduler_policy is None
            and environment is None
        ):
            return process_main()
        argv = None
    parser = build_parser()
    arguments = parser.parse_args(cast(Sequence[str] | None, argv))
    _validate_task13_arguments(arguments, parser)
    active_environment = {} if environment is None else environment
    using_offline_services = False
    if services is None:
        runtime_config = (
            active_environment["NFL_V2_RUNTIME_CONFIG"]  # noqa: SIM401
            if "NFL_V2_RUNTIME_CONFIG" in active_environment
            else None
        )
        if runtime_config is not None:
            from nfl_predictor.runtime.services import build_production_runtime

            runtime = build_production_runtime(
                Path(runtime_config), active_environment, clock or (lambda: datetime.now(UTC))
            )
            services = runtime.services
            if scheduler_policy is None:
                scheduler_policy = runtime.scheduler_policy
        else:
            if not _is_offline_preview(arguments):
                parser.error("production services are not configured")
            services = _offline_service_registry()
            using_offline_services = True
    if scheduler_policy is None:
        if not using_offline_services:
            parser.error("scheduler freshness policy is not configured")
        scheduler_policy = SchedulerFreshnessPolicy(
            policy_version="scheduler-freshness-v1",
            maximum_skew=timedelta(minutes=5),
        )
    frozen_now = _clock_value(clock or (lambda: datetime.now(UTC)), parser)
    context = _context(arguments, frozen_now, parser, scheduler_policy)
    arguments.authorization = _dispatch_authorization(
        arguments,
        context,
        active_environment,
        parser,
    )
    result = _dispatch(arguments, services, context)
    if result is not None:
        print(json.dumps(_json_value(result), sort_keys=True))
    if (
        arguments.route == "odds.budget-plan"
        and isinstance(result, Mapping)
        and bool(result.get("blockers"))
        and "DEPLOYMENT_DISABLED" not in cast(Sequence[object], result["blockers"])
    ):
        return 1
    return 0


def process_main() -> int:
    return main(None, environment=os.environ)


def _validate_task13_arguments(
    arguments: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if arguments.route != "schedule.render-dispatch":
        return
    if arguments.public_offsets != (-8, -3, 2, 7):
        parser.error("public dispatch offsets must be exactly -8,-3,2,7")
    if arguments.private_offsets != (-6, -1, 4, 9):
        parser.error("private dispatch offsets must be exactly -6,-1,4,9")


def _dispatch_authorization(
    arguments: argparse.Namespace,
    context: ExecutionContext,
    environment: Mapping[str, str],
    parser: argparse.ArgumentParser,
) -> DispatchAuthorization | None:
    if arguments.route != "dispatch.due" or arguments.dry_run:
        return None
    if context.mode != "live":
        parser.error("non-dry-run dispatch cannot use replay mode")
    if environment.get("NFL_V2_DEPLOYMENT_ENABLED") != "true":
        parser.error("non-dry-run dispatch requires NFL_V2_DEPLOYMENT_ENABLED=true")
    target_repository = environment.get("NFL_DATA_REPO")
    credential = environment.get("NFL_DATA_REPO_DISPATCH_TOKEN")
    if not target_repository or not credential:
        parser.error("non-dry-run dispatch requires NFL_DATA_REPO and NFL_DATA_REPO_DISPATCH_TOKEN")
    try:
        return DispatchAuthorization(target_repository, credential)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(process_main())
