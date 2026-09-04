from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin


def test_event_version_rejects_naive_time() -> None:
    with pytest.raises(ValidationError):
        EventVersion(
            canonical_event_id="2026_REG_01_NE_SEA",
            event_version=1,
            source_event_ids={"nflverse": "2026_01_NE_SEA"},
            season=2026,
            season_type="REG",
            week=1,
            home_team="SEA",
            away_team="NE",
            kickoff_at_utc=datetime(2026, 9, 10, 0, 20),  # noqa: DTZ001
            neutral_site=False,
            observed_at_utc=datetime.now(UTC),
            available_at_utc=datetime.now(UTC),
            captured_at_utc=datetime.now(UTC),
            raw_payload_sha256="a" * 64,
        )


def test_t72_window_is_plus_or_minus_ten_minutes() -> None:
    kickoff = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    origin = ForecastOrigin.for_kickoff(Origin.T72, kickoff)
    assert origin.target_at_utc == kickoff - timedelta(hours=72)
    assert origin.window_opens_at_utc == origin.target_at_utc - timedelta(minutes=10)
    assert origin.window_closes_at_utc == origin.target_at_utc + timedelta(minutes=10)


def test_runtime_t72_input_uses_t72_horizon() -> None:
    kickoff = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    origin = ForecastOrigin.for_kickoff("T72", kickoff)
    assert origin.target_at_utc == kickoff - timedelta(hours=72)


def test_t60_window_uses_sixty_minute_horizon() -> None:
    kickoff = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    origin = ForecastOrigin.for_kickoff(Origin.T60, kickoff)
    assert origin.target_at_utc == kickoff - timedelta(minutes=60)
    assert origin.window_opens_at_utc == origin.target_at_utc - timedelta(minutes=10)
    assert origin.window_closes_at_utc == origin.target_at_utc + timedelta(minutes=10)
