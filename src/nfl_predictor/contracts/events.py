from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import Field, model_validator

from .common import UtcModel
from .enums import Origin


class EventVersion(UtcModel):
    canonical_event_id: str
    event_version: int = Field(ge=1)
    source_event_ids: dict[str, str]
    season: int
    season_type: str
    week: int = Field(ge=1)
    home_team: str
    away_team: str
    kickoff_at_utc: datetime
    venue_id: str | None = None
    neutral_site: bool
    observed_at_utc: datetime
    available_at_utc: datetime
    captured_at_utc: datetime
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_distinct_teams_and_capture_order(self) -> EventVersion:
        if self.home_team == self.away_team:
            raise ValueError("home and away teams must differ")
        if self.available_at_utc > self.captured_at_utc:
            raise ValueError("schedule fact cannot be captured before availability")
        return self


class ForecastOrigin(UtcModel):
    origin: Origin
    target_at_utc: datetime
    window_opens_at_utc: datetime
    window_closes_at_utc: datetime

    @model_validator(mode="after")
    def require_exact_window(self) -> ForecastOrigin:
        if self.window_opens_at_utc != self.target_at_utc - timedelta(minutes=10):
            raise ValueError("origin window must open ten minutes before target")
        if self.window_closes_at_utc != self.target_at_utc + timedelta(minutes=10):
            raise ValueError("origin window must close ten minutes after target")
        return self

    @classmethod
    def for_kickoff(cls, origin: Origin, kickoff_at_utc: datetime) -> ForecastOrigin:
        origin = Origin(origin)
        if origin is Origin.T72:
            horizon = timedelta(hours=72)
        elif origin is Origin.T60:
            horizon = timedelta(minutes=60)
        else:
            raise ValueError(f"unsupported forecast origin: {origin}")
        target = kickoff_at_utc - horizon
        return cls(
            origin=origin,
            target_at_utc=target,
            window_opens_at_utc=target - timedelta(minutes=10),
            window_closes_at_utc=target + timedelta(minutes=10),
        )
