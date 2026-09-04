from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import get_type_hints

import pytest

from nfl_predictor.cli import SchedulerFreshnessPolicy, ServiceRegistry, build_parser, main
from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.workflows.forecast import ForecastExecutionContext

NOW = datetime(2026, 9, 13, 19, 25, tzinfo=UTC)
SCHEDULER_POLICY = SchedulerFreshnessPolicy(
    policy_version="scheduler-freshness-v1",
    maximum_skew=timedelta(minutes=5),
)


class Services:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _record(self, route: str, **values: object) -> dict[str, str]:
        self.calls.append((route, values))
        return {"route": route}

    def schedule_sync(self, *, season: int, context: ForecastExecutionContext) -> object:
        return self._record("schedule.sync", season=season, context=context)

    def forecast_due(self, *, trigger: str, context: ForecastExecutionContext) -> object:
        return self._record("forecast.due", trigger=trigger, context=context)

    def forecast_run(
        self,
        *,
        event_id: str,
        origin: str,
        trigger: str,
        context: ForecastExecutionContext,
    ) -> object:
        return self._record(
            "forecast.run",
            event_id=event_id,
            origin=origin,
            trigger=trigger,
            context=context,
        )

    def outcomes_sync(
        self, *, season: int, through_week: int, context: ForecastExecutionContext
    ) -> object:
        return self._record(
            "outcomes.sync", season=season, through_week=through_week, context=context
        )

    def settle(
        self, *, season: int, through_week: int, context: ForecastExecutionContext
    ) -> object:
        return self._record("settle", season=season, through_week=through_week, context=context)

    def report_weekly(
        self, *, season: int, through_week: int, context: ForecastExecutionContext
    ) -> object:
        return self._record(
            "report.weekly", season=season, through_week=through_week, context=context
        )

    def odds_budget_plan(self, *, season: int, context: ForecastExecutionContext) -> object:
        return self._record("odds.budget-plan", season=season, context=context)

    def readiness_check(self, *, context: ForecastExecutionContext) -> object:
        return self._record("readiness.check", context=context)

    def registry(self) -> ServiceRegistry:
        return ServiceRegistry(
            schedule_sync=self.schedule_sync,
            forecast_due=self.forecast_due,
            forecast_run=self.forecast_run,
            outcomes_sync=self.outcomes_sync,
            settle=self.settle,
            report_weekly=self.report_weekly,
            odds_budget_plan=self.odds_budget_plan,
            readiness_check=self.readiness_check,
        )


@pytest.mark.parametrize(
    ("argv", "route"),
    [
        (["schedule", "sync", "--season", "2026"], "schedule.sync"),
        (["forecast", "due", "--trigger", "manual"], "forecast.due"),
        (
            ["forecast", "run", "--event", "event-1", "--origin", "T60"],
            "forecast.run",
        ),
        (
            ["outcomes", "sync", "--season", "2026", "--through-week", "4"],
            "outcomes.sync",
        ),
        (["settle", "--season", "2026", "--through-week", "4"], "settle"),
        (
            ["report", "weekly", "--season", "2026", "--through-week", "4"],
            "report.weekly",
        ),
        (["odds", "budget-plan", "--season", "2026"], "odds.budget-plan"),
        (["readiness", "check"], "readiness.check"),
    ],
)
def test_every_documented_command_routes_through_typed_registry(
    argv: list[str], route: str
) -> None:
    services = Services()

    assert (
        main(
            argv,
            services=services.registry(),
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )
        == 0
    )

    assert services.calls[0][0] == route


def test_live_forecast_freezes_real_clock_and_rejects_time_travel() -> None:
    services = Services()
    registry = services.registry()

    main(
        ["forecast", "due", "--trigger", "manual"],
        services=registry,
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
    )
    _, values = services.calls[-1]
    assert values["context"].as_of_utc == NOW
    assert values["context"].namespace == "prospective"
    assert values["context"].provenance_grade is ProvenanceGrade.A

    historical = (NOW - timedelta(days=1)).isoformat()
    with pytest.raises(SystemExit):
        main(
            ["forecast", "due", "--trigger", "manual", "--at", historical],
            services=registry,
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )
    assert len(services.calls) == 1


def test_replay_uses_distinct_namespace_and_non_grade_a_provenance() -> None:
    services = Services()
    historical = (NOW - timedelta(days=7)).isoformat()

    main(
        [
            "forecast",
            "run",
            "--event",
            "event-1",
            "--origin",
            "T72",
            "--mode",
            "replay",
            "--at",
            historical,
        ],
        services=services.registry(),
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
    )

    _, values = services.calls[0]
    context = values["context"]
    assert context.as_of_utc == NOW - timedelta(days=7)
    assert context.namespace == "replay"
    assert context.provenance_grade is ProvenanceGrade.C
    assert context.reconstruction_reason == "CALLER_REQUESTED_HISTORICAL_REPLAY"


def test_replay_requires_explicit_cutoff_and_utc_input() -> None:
    services = Services()
    registry = services.registry()

    with pytest.raises(SystemExit):
        main(
            [
                "forecast",
                "run",
                "--event",
                "event-1",
                "--origin",
                "T60",
                "--mode",
                "replay",
            ],
            services=registry,
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )
    with pytest.raises(SystemExit):
        main(
            [
                "forecast",
                "run",
                "--event",
                "event-1",
                "--origin",
                "T60",
                "--mode",
                "replay",
                "--at",
                "2026-09-01T00:00:00-05:00",
            ],
            services=registry,
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )
    assert services.calls == []


def test_parser_is_additive_and_console_entry_is_restored() -> None:
    parser = build_parser()
    assert parser.parse_args(["schedule", "sync", "--season", "2026"]).season == 2026
    with open("pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["scripts"]["nfl-predictor"] == "nfl_predictor.cli:main"


def test_unconfigured_production_registry_fails_closed() -> None:
    with pytest.raises(SystemExit):
        main(
            ["schedule", "sync", "--season", "2026"],
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )


def test_live_scheduler_timestamp_tolerates_dispatch_skew_but_uses_one_real_now() -> None:
    services = Services()
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return NOW

    supplied = (NOW - timedelta(seconds=45)).isoformat()
    main(
        ["forecast", "due", "--trigger", "private_schedule", "--at", supplied],
        services=services.registry(),
        clock=clock,
        scheduler_policy=SCHEDULER_POLICY,
    )

    assert calls == 1
    assert services.calls[0][1]["context"].as_of_utc == NOW
    assert isinstance(services.calls[0][1]["context"], ForecastExecutionContext)


def test_live_scheduler_timestamp_outside_freshness_bound_requires_replay() -> None:
    services = Services()

    with pytest.raises(SystemExit):
        main(
            [
                "forecast",
                "due",
                "--trigger",
                "private_schedule",
                "--at",
                (NOW - timedelta(minutes=6)).isoformat(),
            ],
            services=services.registry(),
            clock=lambda: NOW,
            scheduler_policy=SCHEDULER_POLICY,
        )
    assert services.calls == []


def test_service_registry_exposes_typed_command_protocols() -> None:
    assert get_type_hints(ServiceRegistry)["forecast_due"].__name__ == "ForecastDueService"


def test_scheduler_freshness_policy_is_required_and_versioned() -> None:
    with pytest.raises(SystemExit):
        main(
            ["forecast", "due", "--trigger", "manual"],
            services=Services().registry(),
            clock=lambda: NOW,
        )
    with pytest.raises(ValueError, match="policy version"):
        SchedulerFreshnessPolicy("", timedelta(minutes=5))


@dataclass(frozen=True)
class StructuredResult:
    route: str
    at: datetime
    grade: ProvenanceGrade


class StructuredReadiness:
    def __call__(self, *, context: ForecastExecutionContext) -> object:
        assert isinstance(context, ForecastExecutionContext)
        return StructuredResult("readiness.check", NOW, ProvenanceGrade.A)


def test_cli_serializes_typed_results_as_structured_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = Services()
    registry = services.registry()
    object.__setattr__(registry, "readiness_check", StructuredReadiness())

    main(
        ["readiness", "check"],
        services=registry,
        clock=lambda: NOW,
        scheduler_policy=SCHEDULER_POLICY,
    )

    assert capsys.readouterr().out.strip() == (
        '{"at": "2026-09-13T19:25:00+00:00", "grade": "A", "route": "readiness.check"}'
    )
