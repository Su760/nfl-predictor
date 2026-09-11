from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin

ORIGINS = (Origin.T72, Origin.T60)
WINDOW_MINUTES = 10
_STRICT_UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class DueCluster:
    cluster_id: str
    event_ids: tuple[str, ...]
    origin: Origin
    target_at_utc: datetime


@dataclass(frozen=True)
class ScheduleWindows:
    ticks: tuple[datetime, ...]


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def parse_strict_utc_z(value: str) -> datetime:
    if not isinstance(value, str) or _STRICT_UTC_Z.fullmatch(value) is None:
        raise ValueError("timestamp must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("timestamp must use YYYY-MM-DDTHH:MM:SSZ") from error


def _require_active_events(events: Sequence[EventVersion]) -> None:
    seen: set[str] = set()
    for event in events:
        if event.canonical_event_id in seen:
            raise ValueError("schedule must contain exactly one active version per event")
        seen.add(event.canonical_event_id)


def target_for(event: EventVersion, origin: Origin | str) -> datetime:
    parsed = origin if isinstance(origin, Origin) else Origin(origin)
    return ForecastOrigin.for_kickoff(parsed, event.kickoff_at_utc).target_at_utc


def build_schedule_windows(events: Sequence[EventVersion], tick_minutes: int) -> ScheduleWindows:
    if isinstance(tick_minutes, bool) or not isinstance(tick_minutes, int):
        raise TypeError("tick_minutes must be an integer")
    if tick_minutes <= 0 or (WINDOW_MINUTES * 2) % tick_minutes != 0:
        raise ValueError("tick_minutes must divide the twenty-minute origin window")
    _require_active_events(events)
    ticks: set[datetime] = set()
    for event in events:
        for origin in ORIGINS:
            target = target_for(event, origin)
            for offset in range(-WINDOW_MINUTES, WINDOW_MINUTES + 1, tick_minutes):
                ticks.add(target + timedelta(minutes=offset))
    return ScheduleWindows(tuple(sorted(ticks)))


def _validated_offsets(offsets_minutes: tuple[int, ...]) -> tuple[int, ...]:
    if not offsets_minutes:
        raise ValueError("at least one schedule offset is required")
    if any(isinstance(offset, bool) or not isinstance(offset, int) for offset in offsets_minutes):
        raise TypeError("schedule offsets must be integers")
    if any(abs(offset) > WINDOW_MINUTES for offset in offsets_minutes):
        raise ValueError("schedule offset must remain inside the origin window")
    if len(set(offsets_minutes)) != len(offsets_minutes):
        raise ValueError("schedule offsets must be unique")
    return offsets_minutes


def exact_cron_entries(
    events: Sequence[EventVersion], offsets_minutes: tuple[int, ...]
) -> tuple[str, ...]:
    _require_active_events(events)
    offsets = _validated_offsets(offsets_minutes)
    entries: set[str] = set()
    for event in events:
        for origin in ORIGINS:
            target = target_for(event, origin)
            for offset in offsets:
                tick = target + timedelta(minutes=offset)
                if tick.minute == 0:
                    tick += timedelta(minutes=1 if offset <= 0 else -1)
                entries.add(f"{tick.minute} {tick.hour} {tick.day} {tick.month} *")
    return tuple(sorted(entries))


def due_clusters(events: Sequence[EventVersion], now: datetime) -> tuple[DueCluster, ...]:
    _require_utc(now, "now")
    _require_active_events(events)
    grouped: dict[tuple[Origin, datetime], list[str]] = {}
    for event in events:
        for origin in ORIGINS:
            window = ForecastOrigin.for_kickoff(origin, event.kickoff_at_utc)
            if window.window_opens_at_utc <= now <= window.window_closes_at_utc:
                grouped.setdefault((origin, window.target_at_utc), []).append(
                    event.canonical_event_id
                )
    ordered = sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0].value))
    return tuple(
        DueCluster(
            cluster_id=f"{origin.value}:{target.isoformat()}",
            event_ids=tuple(sorted(event_ids)),
            origin=origin,
            target_at_utc=target,
        )
        for (origin, target), event_ids in ordered
    )


def project_active_schedule(
    events: Sequence[EventVersion], public_offsets: tuple[int, ...],
    private_offsets: tuple[int, ...], schedule_change_calls: int,
) -> tuple[int, dict[str, int]]:
    """Shared actual-event request and job graph derivation for both private planners."""
    if type(schedule_change_calls) is not int or schedule_change_calls < 0:
        raise ValueError("schedule change calls must be a nonnegative integer")
    public_ticks = set(exact_cron_entries(events, public_offsets))
    private_ticks = set(exact_cron_entries(events, private_offsets))
    heartbeat_ticks = private_ticks | set(exact_cron_entries(events, (5,)))
    return len(events) * len(ORIGINS) * 2 + schedule_change_calls, {
        "full_forecast_workers": len(public_ticks),
        "private_due_ticks": len(private_ticks),
        "nonce_admission_jobs": len(public_ticks),
        "budget_admission_jobs": len(public_ticks) + len(private_ticks),
        "heartbeat_jobs": len(heartbeat_ticks),
    }
