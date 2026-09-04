from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import Field, field_validator

from nfl_predictor.contracts.common import UtcModel
from nfl_predictor.contracts.events import EventVersion

from .teams import canonicalize_team


class ScheduleFact(UtcModel):
    source: str
    source_event_id: str
    season: int
    season_type: str
    week: int
    home_team: str
    away_team: str
    kickoff_at_utc: datetime
    venue_id: str | None
    neutral_site: bool
    observed_at_utc: datetime
    available_at_utc: datetime
    captured_at_utc: datetime
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("home_team", "away_team")
    @classmethod
    def require_known_team(cls, value: str) -> str:
        return canonicalize_team(value)

    def to_event_version(self) -> EventVersion:
        home_team = canonicalize_team(self.home_team)
        away_team = canonicalize_team(self.away_team)
        event_id = f"{self.season}_{self.season_type}_{self.week:02d}_{away_team}_{home_team}"
        return EventVersion(
            canonical_event_id=event_id,
            event_version=1,
            source_event_ids={self.source: self.source_event_id},
            season=self.season,
            season_type=self.season_type,
            week=self.week,
            home_team=home_team,
            away_team=away_team,
            kickoff_at_utc=self.kickoff_at_utc,
            venue_id=self.venue_id,
            neutral_site=self.neutral_site,
            observed_at_utc=self.observed_at_utc,
            available_at_utc=self.available_at_utc,
            captured_at_utc=self.captured_at_utc,
            raw_payload_sha256=self.raw_payload_sha256,
        )


@dataclass(frozen=True)
class QuarantinedEvent:
    source_event_id: str
    reason_code: str


class EventReconciler:
    def reconcile(
        self, fact: ScheduleFact, existing: list[EventVersion]
    ) -> EventVersion | QuarantinedEvent:
        source_matches = [
            event for event in existing if fact.source_event_id in event.source_event_ids.values()
        ]
        if len(source_matches) > 1:
            return QuarantinedEvent(fact.source_event_id, "AMBIGUOUS_EVENT_MATCH")

        fact_home_team = canonicalize_team(fact.home_team)
        fact_away_team = canonicalize_team(fact.away_team)
        if len(source_matches) == 1:
            current = source_matches[0]
            if (current.home_team, current.away_team) != (fact_home_team, fact_away_team):
                return QuarantinedEvent(fact.source_event_id, "HOME_AWAY_MISMATCH")
            if self._same_schedule_context(current, fact):
                return current
            return EventVersion.model_validate(
                {
                    **current.model_dump(),
                    "event_version": current.event_version + 1,
                    "source_event_ids": {
                        **current.source_event_ids,
                        fact.source: fact.source_event_id,
                    },
                    "kickoff_at_utc": fact.kickoff_at_utc,
                    "venue_id": fact.venue_id,
                    "neutral_site": fact.neutral_site,
                    "observed_at_utc": fact.observed_at_utc,
                    "available_at_utc": fact.available_at_utc,
                    "captured_at_utc": fact.captured_at_utc,
                    "raw_payload_sha256": fact.raw_payload_sha256,
                }
            )

        participant_matches = [
            event
            for event in existing
            if event.season == fact.season
            and event.season_type == fact.season_type
            and event.week == fact.week
            and event.home_team == fact_home_team
            and event.away_team == fact_away_team
        ]
        if participant_matches:
            return QuarantinedEvent(fact.source_event_id, "UNALIASED_EXISTING_EVENT")
        return fact.to_event_version()

    @staticmethod
    def _same_schedule_context(current: EventVersion, fact: ScheduleFact) -> bool:
        return (
            current.kickoff_at_utc == fact.kickoff_at_utc
            and current.venue_id == fact.venue_id
            and current.neutral_site == fact.neutral_site
        )
