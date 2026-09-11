from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.workflows import schedule_windows
from nfl_predictor.workflows.schedule_windows import (
    build_schedule_windows,
    due_clusters,
    exact_cron_entries,
    target_for,
)


def event(
    *,
    canonical_event_id: str = "2026_REG_01_NE_SEA",
    kickoff_at_utc: datetime = datetime(2026, 9, 10, 0, 20, tzinfo=UTC),
    event_version: int = 1,
) -> EventVersion:
    return EventVersion(
        canonical_event_id=canonical_event_id,
        event_version=event_version,
        source_event_ids={"official": "NE-at-SEA-2026-09-09"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="SEA",
        away_team="NE",
        kickoff_at_utc=kickoff_at_utc,
        venue_id="lumen-field",
        neutral_site=False,
        observed_at_utc=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        available_at_utc=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        captured_at_utc=datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
        raw_payload_sha256="a" * 64,
    )


def test_every_origin_target_has_redundant_five_minute_ticks_inside_window() -> None:
    schedule_2026 = [event()]

    windows = build_schedule_windows(schedule_2026, tick_minutes=5)

    for scheduled_event in schedule_2026:
        for origin in (Origin.T72, Origin.T60):
            target = target_for(scheduled_event, origin)
            covered = [
                tick for tick in windows.ticks if abs((tick - target).total_seconds()) <= 600
            ]
            assert len(covered) >= 3
    assert target_for(schedule_2026[0], "T72") == datetime(2026, 9, 7, 0, 20, tzinfo=UTC)


def test_generated_public_and_private_crons_are_schedule_specific() -> None:
    schedule_2026 = [event()]

    public = exact_cron_entries(schedule_2026, offsets_minutes=(-8, -3, 2, 7))
    private = exact_cron_entries(schedule_2026, offsets_minutes=(-6, -1, 4, 9))

    assert public == (
        "12 0 7 9 *",
        "12 23 9 9 *",
        "17 0 7 9 *",
        "17 23 9 9 *",
        "22 0 7 9 *",
        "22 23 9 9 *",
        "27 0 7 9 *",
        "27 23 9 9 *",
    )
    assert private == (
        "14 0 7 9 *",
        "14 23 9 9 *",
        "19 0 7 9 *",
        "19 23 9 9 *",
        "24 0 7 9 *",
        "24 23 9 9 *",
        "29 0 7 9 *",
        "29 23 9 9 *",
    )
    assert public != private
    assert all(not entry.startswith("0 ") for entry in public + private)


def test_due_clusters_group_same_target_and_exclude_closed_windows() -> None:
    first = event(canonical_event_id="event-b")
    second = event(canonical_event_id="event-a")
    target = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)

    due = due_clusters([first, second], now=target)

    assert len(due) == 1
    assert due[0].cluster_id == "T72:2026-09-07T00:20:00+00:00"
    assert due[0].event_ids == ("event-a", "event-b")
    assert due[0].origin is Origin.T72
    assert due[0].target_at_utc == target
    assert due_clusters([first, second], now=target + timedelta(minutes=11)) == ()


def test_schedule_window_inputs_fail_closed_on_invalid_ticks_or_non_utc_clock() -> None:
    with pytest.raises(ValueError, match="divide"):
        build_schedule_windows([event()], tick_minutes=6)
    with pytest.raises(ValueError, match="UTC"):
        due_clusters([event()], now=datetime(2026, 9, 7, 0, 20, tzinfo=UTC).replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC"):
        due_clusters(
            [event()],
            now=datetime(2026, 9, 6, 19, 20, tzinfo=timezone(timedelta(hours=-5))),
        )


def test_active_schedule_rejects_multiple_versions_of_one_event() -> None:
    with pytest.raises(ValueError, match="one active version"):
        due_clusters([event(event_version=1), event(event_version=2)], now=datetime.now(UTC))


@pytest.mark.parametrize("offsets", [(), (-11,), (11,), (-8, -8), (True,)])
def test_cron_offsets_must_be_unique_integer_window_offsets(offsets: tuple[object, ...]) -> None:
    with pytest.raises((TypeError, ValueError), match="offset"):
        exact_cron_entries([event()], offsets_minutes=offsets)  # type: ignore[arg-type]


def test_manual_scheduler_timestamp_requires_exact_utc_z_wire_format() -> None:
    parser = getattr(schedule_windows, "parse_strict_utc_z", None)
    assert parser is not None, "strict manual timestamp parser is missing"

    assert parser("2026-09-07T00:20:00Z") == datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
    for invalid in (
        "2026-09-07T00:20:00+00:00",
        "2026-09-07 00:20:00Z",
        "2026-09-07T00:20:00.000Z",
        "2026-09-07T00:20:00Z\nODDS_API_KEY=hostile",
    ):
        with pytest.raises(ValueError, match="YYYY-MM-DDTHH:MM:SSZ"):
            parser(invalid)
