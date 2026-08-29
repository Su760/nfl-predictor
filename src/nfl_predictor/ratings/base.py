from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CompletedGame:
    canonical_event_id: str
    season: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    neutral_site: bool
    finalized_at_utc: datetime


@dataclass(frozen=True)
class RatingSnapshot:
    system: str
    cutoff_at_utc: datetime
    values: dict[str, float]


@dataclass(frozen=True)
class TeamGameEpa:
    canonical_event_id: str
    offense_team: str
    defense_team: str
    offense_epa_per_play: float
    pass_epa_per_play: float
    rush_epa_per_play: float
    plays: int
    finalized_at_utc: datetime
