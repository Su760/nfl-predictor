from datetime import UTC, datetime, timedelta

import pytest

from nfl_predictor.contracts.enums import Origin, RunStatus
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import OriginRun
from nfl_predictor.identity.origins import OriginPlanner


@pytest.fixture
def event_v1() -> EventVersion:
    return EventVersion(
        canonical_event_id="2026_REG_01_NE_SEA",
        event_version=1,
        source_event_ids={"nflverse": "2026_01_NE_SEA"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="SEA",
        away_team="NE",
        kickoff_at_utc=datetime(2026, 9, 10, 0, 20, tzinfo=UTC),
        venue_id="lumen-field",
        neutral_site=False,
        observed_at_utc=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        available_at_utc=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        captured_at_utc=datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
        raw_payload_sha256="a" * 64,
    )


def origin_run(
    event: EventVersion,
    origin: Origin,
    policy_version: str,
    status: RunStatus = RunStatus.MISSED,
) -> OriginRun:
    target = event.kickoff_at_utc - (
        timedelta(hours=72) if origin is Origin.T72 else timedelta(minutes=60)
    )
    successful = status in {RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY}
    return OriginRun(
        origin_run_id=f"run-{origin.value}-{policy_version}",
        canonical_event_id=event.canonical_event_id,
        event_version=event.event_version,
        origin=origin,
        forecast_policy_version=policy_version,
        target_at_utc=target,
        window_opens_at_utc=target - timedelta(minutes=10),
        window_closes_at_utc=target + timedelta(minutes=10),
        decision_at_utc=target if successful else None,
        status=status,
        attempt_ids=[],
        prediction_ids=["prediction"] if successful else [],
        reason_codes=[],
        code_sha="b" * 40,
        created_at_utc=target,
    )


def test_due_returns_each_unfulfilled_origin_once(event_v1: EventVersion) -> None:
    target = event_v1.kickoff_at_utc - timedelta(hours=72)
    planner = OriginPlanner(policy_version="forecast-v1")

    due = planner.due([event_v1], existing_runs=[], now=target)

    assert [(item.origin, item.idempotency_key) for item in due] == [
        (Origin.T72, f"{event_v1.canonical_event_id}:T72:forecast-v1")
    ]


@pytest.mark.parametrize("offset", [timedelta(minutes=-10), timedelta(minutes=10)])
def test_due_includes_the_inclusive_window_boundaries(
    event_v1: EventVersion, offset: timedelta
) -> None:
    target = event_v1.kickoff_at_utc - timedelta(hours=72)

    due = OriginPlanner(policy_version="forecast-v1").due(
        [event_v1], existing_runs=[], now=target + offset
    )

    assert [item.origin for item in due] == [Origin.T72]


def test_current_policy_run_suppresses_duplicate_due(event_v1: EventVersion) -> None:
    target = event_v1.kickoff_at_utc - timedelta(hours=72)
    existing = origin_run(event_v1, Origin.T72, "forecast-v1")

    due = OriginPlanner(policy_version="forecast-v1").due([event_v1], [existing], target)

    assert due == []


def test_other_policy_run_does_not_suppress_due(event_v1: EventVersion) -> None:
    target = event_v1.kickoff_at_utc - timedelta(hours=72)
    existing = origin_run(event_v1, Origin.T72, "forecast-v0")

    due = OriginPlanner(policy_version="forecast-v1").due([event_v1], [existing], target)

    assert [item.origin for item in due] == [Origin.T72]


def test_overdue_returns_each_unfulfilled_origin(event_v1: EventVersion) -> None:
    overdue = OriginPlanner(policy_version="forecast-v1").overdue(
        [event_v1], existing_runs=[], now=event_v1.kickoff_at_utc
    )

    assert [item.origin for item in overdue] == [Origin.T72, Origin.T60]


@pytest.mark.parametrize(
    "status",
    [RunStatus.MISSED, RunStatus.FAILED, RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY],
)
def test_terminal_current_policy_run_stops_overdue_replanning(
    event_v1: EventVersion, status: RunStatus
) -> None:
    existing = origin_run(event_v1, Origin.T72, "forecast-v1", status)

    overdue = OriginPlanner(policy_version="forecast-v1").overdue(
        [event_v1], existing_runs=[existing], now=event_v1.kickoff_at_utc
    )

    assert [item.origin for item in overdue] == [Origin.T60]


def test_other_policy_terminal_run_does_not_stop_overdue_replanning(event_v1: EventVersion) -> None:
    existing = origin_run(event_v1, Origin.T72, "forecast-v0")

    overdue = OriginPlanner(policy_version="forecast-v1").overdue(
        [event_v1], existing_runs=[existing], now=event_v1.kickoff_at_utc
    )

    assert [item.origin for item in overdue] == [Origin.T72, Origin.T60]
