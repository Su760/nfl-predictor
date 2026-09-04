from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from nfl_predictor.contracts.enums import Origin, RunStatus
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.forecasts import OriginRun

TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.MISSED, RunStatus.FAILED, RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY}
)


@dataclass(frozen=True)
class OriginObligation:
    event: EventVersion
    window: ForecastOrigin
    policy_version: str

    @property
    def origin(self) -> Origin:
        return self.window.origin

    @property
    def idempotency_key(self) -> str:
        return f"{self.event.canonical_event_id}:{self.origin.value}:{self.policy_version}"


class OriginPlanner:
    def __init__(self, policy_version: str) -> None:
        self.policy_version = policy_version

    def due(
        self,
        events: list[EventVersion],
        existing_runs: list[OriginRun],
        now: datetime,
    ) -> list[OriginObligation]:
        fulfilled = {
            self._run_key(run.canonical_event_id, run.origin, run.forecast_policy_version)
            for run in existing_runs
        }
        return self._plan(events, now, fulfilled, overdue=False)

    def overdue(
        self,
        events: list[EventVersion],
        existing_runs: list[OriginRun],
        now: datetime,
    ) -> list[OriginObligation]:
        fulfilled = {
            self._run_key(run.canonical_event_id, run.origin, run.forecast_policy_version)
            for run in existing_runs
            if run.status in TERMINAL_RUN_STATUSES
        }
        return self._plan(events, now, fulfilled, overdue=True)

    def _plan(
        self,
        events: list[EventVersion],
        now: datetime,
        fulfilled: set[tuple[str, Origin, str]],
        *,
        overdue: bool,
    ) -> list[OriginObligation]:
        obligations: list[OriginObligation] = []
        for event in sorted(events, key=lambda item: (item.canonical_event_id, item.event_version)):
            for origin in (Origin.T72, Origin.T60):
                window = ForecastOrigin.for_kickoff(origin, event.kickoff_at_utc)
                key = self._run_key(event.canonical_event_id, origin, self.policy_version)
                is_due = window.window_opens_at_utc <= now <= window.window_closes_at_utc
                if key not in fulfilled and (
                    now > window.window_closes_at_utc if overdue else is_due
                ):
                    obligations.append(OriginObligation(event, window, self.policy_version))
        return obligations

    @staticmethod
    def _run_key(
        canonical_event_id: str, origin: Origin, policy_version: str
    ) -> tuple[str, Origin, str]:
        return canonical_event_id, origin, policy_version
