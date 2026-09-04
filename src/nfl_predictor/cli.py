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
    if (
        requested is not None
        and abs(_utc(requested, parser, "--at") - frozen_now) > scheduler_policy.maximum_skew
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


def _offline_odds_budget_plan(*, season: int, context: ExecutionContext) -> object:
    del context
    private_root = Path(__file__).resolve().parents[2] / "deploy" / "private-data-repo"
    config = tomllib.loads((private_root / "config/data_repo.toml").read_text())
    if season != config["season"]:
        raise ValueError("budget-plan season does not match the reviewed deployment config")
    projection = config["cost_projection"]
    jobs = (
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
    plan = plan_private_usage(
        jobs,
        projected_storage_bytes=projection["projected_storage_bytes"],
        verified_included_actions_minutes_remaining=projection[
            "verified_included_private_actions_minutes"
        ],
        verified_included_storage_bytes_remaining=projection["verified_included_storage_bytes"],
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
    if stored_projection != computed_projection:
        raise ValueError("stored private usage projection does not match typed planner")
    manifest = json.loads((private_root / config["schedule"]["manifest_path"]).read_text())
    schedule_complete = bool(
        config["schedule"]["generated_from_active_event_versions"]
        and manifest["generated_from_active_event_versions"]
        and config["schedule"]["active_event_version_manifest_sha256"]
        and manifest["active_event_version_manifest_sha256"]
        == config["schedule"]["active_event_version_manifest_sha256"]
    )
    blockers: list[str] = []
    if not schedule_complete:
        blockers.append("ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE")
    if config["zero_dollar_mode"] and (
        plan.projected_paid_actions_minutes or plan.projected_paid_storage_bytes
    ):
        blockers.append("PROJECTED_PAID_USAGE_NONZERO")
    if projection["projected_odds_requests"] > 400:
        blockers.append("ODDS_REQUEST_BUDGET_EXCEEDS_400")
    if not config["deployment_enabled"]:
        blockers.append("DEPLOYMENT_DISABLED")
    return {
        "blockers": blockers,
        "deployment_allowed": not blockers,
        "github_billed_minutes": plan.github_billed_minutes,
        "projected_odds_requests": projection["projected_odds_requests"],
        "projected_paid_actions_minutes": plan.projected_paid_actions_minutes,
        "projected_paid_storage_bytes": plan.projected_paid_storage_bytes,
        "route": "odds.budget-plan",
        "schedule_complete": schedule_complete,
        "season": season,
        "zero_dollar_mode": config["zero_dollar_mode"],
    }


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


def main(
    argv: Sequence[str] | None = None,
    *,
    services: ServiceRegistry | None = None,
    clock: Callable[[], datetime] | None = None,
    scheduler_policy: SchedulerFreshnessPolicy | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    _validate_task13_arguments(arguments, parser)
    using_offline_services = False
    if services is None:
        runtime_config = (
            environment["NFL_V2_RUNTIME_CONFIG"]
            if environment is not None and "NFL_V2_RUNTIME_CONFIG" in environment
            else None
        )
        if runtime_config is not None:
            from nfl_predictor.runtime.services import build_production_runtime

            assert environment is not None
            runtime = build_production_runtime(
                Path(runtime_config), environment, clock or (lambda: datetime.now(UTC))
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
        os.environ if environment is None else environment,
        parser,
    )
    result = _dispatch(arguments, services, context)
    if result is not None:
        print(json.dumps(_json_value(result), sort_keys=True))
    return 0


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
    raise SystemExit(main())
