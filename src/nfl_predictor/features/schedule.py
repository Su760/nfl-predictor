from __future__ import annotations

import math
from datetime import UTC, datetime

from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import NormalizedFact

from .policy import FeaturePolicy
from .schema import SCHEDULE_FEATURE_NAMES


def _utc_payload_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an aware-UTC ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an aware-UTC ISO string") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must be an aware-UTC ISO string")
    return parsed


def schedule_features(
    event: EventVersion,
    facts: list[NormalizedFact],
    policy: FeaturePolicy,
    decision_at_utc: datetime,
) -> dict[str, float]:
    summaries: dict[str, tuple[int, int, float]] = {}
    for team in (event.home_team, event.away_team):
        games: list[tuple[datetime, NormalizedFact]] = []
        for fact in facts:
            if fact.fact_type != "completed_game" or fact.entity_keys.get("team") != team:
                continue
            if fact.observed_at_utc is None:
                raise ValueError("completed_game facts require a finalized observation timestamp")
            if fact.observed_at_utc >= decision_at_utc:
                continue
            kickoff = _utc_payload_datetime(
                fact.payload.get("kickoff_at_utc"), field="completed_game.kickoff_at_utc"
            )
            if kickoff >= event.kickoff_at_utc:
                continue
            games.append((kickoff, fact))
        games.sort(key=lambda item: (item[0], item[1].fact_id))

        rest = policy.offseason_rest_cap_days
        if games:
            elapsed_days = int((event.kickoff_at_utc - games[-1][0]).total_seconds() // 86400)
            rest = min(policy.offseason_rest_cap_days, elapsed_days)
        observed_weight = sum(
            math.exp(math.log(0.5) / policy.ewma_halflife_games) ** index
            for index in range(len(games))
        )
        prior_weight = policy.prior_effective_games / (
            policy.prior_effective_games + observed_weight
        )
        summaries[team] = (rest, len(games), prior_weight)

    home_rest, home_games, home_prior = summaries[event.home_team]
    away_rest, away_games, away_prior = summaries[event.away_team]
    alignments = [
        fact.fact_type == "division_alignment"
        and fact.entity_keys.get("season") == str(event.season)
        and fact.entity_keys.get("home_team") == event.home_team
        and fact.entity_keys.get("away_team") == event.away_team
        for fact in facts
    ]
    matching_alignments = [fact for fact, matches in zip(facts, alignments, strict=True) if matches]
    if len(matching_alignments) != 1:
        raise ValueError("event requires exactly one division_alignment fact")
    same_division = matching_alignments[0].payload.get("same_division")
    if not isinstance(same_division, bool):
        raise TypeError("division_alignment.same_division must be a boolean")
    values = {
        "home_rest_days": float(home_rest),
        "away_rest_days": float(away_rest),
        "rest_diff": float(home_rest - away_rest),
        "home_field": float(not event.neutral_site),
        "neutral_site": float(event.neutral_site),
        "division_game": float(same_division),
        "home_short_week": float(home_rest <= policy.short_week_max_rest_days),
        "away_short_week": float(away_rest <= policy.short_week_max_rest_days),
        "home_games_observed": float(home_games),
        "away_games_observed": float(away_games),
        "home_prior_weight": float(home_prior),
        "away_prior_weight": float(away_prior),
    }
    if tuple(values) != SCHEDULE_FEATURE_NAMES:
        raise ValueError("schedule feature block does not match V1 schema")
    return values
