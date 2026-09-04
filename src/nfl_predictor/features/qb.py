from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import NormalizedFact

from .policy import FeaturePolicy
from .schema import QB_FEATURE_NAMES


def _attempts(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("QB attempts must be an integer")
    return value


def _metric(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"QB {name} must be numeric")
    return float(value)


def qb_features(
    event: EventVersion,
    facts: Sequence[NormalizedFact],
    policy: FeaturePolicy,
    decision_at_utc: datetime | None = None,
) -> dict[str, float]:
    summaries: dict[str, tuple[float, float]] = {}
    for team in (event.home_team, event.away_team):
        eligible = sorted(
            (
                fact
                for fact in facts
                if fact.fact_type == "qb_trailing"
                and fact.entity_keys.get("team") == team
                and fact.observed_at_utc is not None
                and (decision_at_utc is None or fact.observed_at_utc < decision_at_utc)
            ),
            key=lambda fact: (fact.observed_at_utc, fact.fact_id),
        )
        attempts_by_fact = [_attempts(fact.payload["attempts"]) for fact in eligible]
        if any(attempts < 0 for attempts in attempts_by_fact):
            raise ValueError("QB attempts cannot be negative")
        attempts = sum(attempts_by_fact)
        if attempts >= policy.minimum_qb_attempts:
            decay = math.exp(math.log(0.5) / policy.ewma_halflife_games)
            weighted = [(decay**index, fact) for index, fact in enumerate(reversed(eligible))]
            weighted_attempts = sum(
                weight * _attempts(fact.payload["attempts"]) for weight, fact in weighted
            )
            if weighted_attempts <= 0:
                raise ValueError("eligible QB history must contain positive attempts")
            epa = (
                sum(
                    weight
                    * _metric(fact.payload["epa_per_play"], name="epa_per_play")
                    * _attempts(fact.payload["attempts"])
                    for weight, fact in weighted
                )
                / weighted_attempts
            )
            cpoe = (
                sum(
                    weight
                    * _metric(fact.payload["cpoe"], name="cpoe")
                    * _attempts(fact.payload["attempts"])
                    for weight, fact in weighted
                )
                / weighted_attempts
            )
        else:
            priors = [
                fact
                for fact in facts
                if fact.fact_type == "team_passing_prior" and fact.entity_keys.get("team") == team
            ]
            if len(priors) != 1:
                raise ValueError(f"team {team} must have exactly one versioned passing prior")
            epa = _metric(priors[0].payload["epa_per_play"], name="epa_per_play")
            cpoe = _metric(priors[0].payload["cpoe"], name="cpoe")
        summaries[team] = (epa, cpoe)

    home_epa, home_cpoe = summaries[event.home_team]
    away_epa, away_cpoe = summaries[event.away_team]
    values = {
        "home_qb_epa": home_epa,
        "away_qb_epa": away_epa,
        "qb_epa_diff": home_epa - away_epa,
        "home_qb_cpoe": home_cpoe,
        "away_qb_cpoe": away_cpoe,
        "qb_cpoe_diff": home_cpoe - away_cpoe,
    }
    if tuple(values) != QB_FEATURE_NAMES:
        raise ValueError("QB feature block does not match V1 schema")
    return values
