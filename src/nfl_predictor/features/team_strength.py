from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import NormalizedFact
from nfl_predictor.ratings.base import CompletedGame, TeamGameEpa
from nfl_predictor.ratings.colley import colley_ratings
from nfl_predictor.ratings.elo import EloRater
from nfl_predictor.ratings.epa import fit_opponent_adjusted_epa
from nfl_predictor.ratings.massey import massey_ratings

from .policy import FeaturePolicy
from .schema import RATING_FEATURE_NAMES

STRENGTH_KEYS = (
    "elo",
    "colley",
    "massey",
    "off_epa",
    "def_epa",
    "pass_epa",
    "rush_epa",
)
COMPLETED_GAME_KEYS = (
    "season",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral_site",
    "kickoff_at_utc",
)
TEAM_GAME_EPA_KEYS = (
    "offense_epa_per_play",
    "pass_epa_per_play",
    "rush_epa_per_play",
    "plays",
)


def shrunk_ewma(
    values: Sequence[float],
    prior: float,
    halflife_games: float,
    prior_effective_games: float,
) -> tuple[float, float]:
    if halflife_games <= 0 or prior_effective_games <= 0:
        raise ValueError("EWMA halflife and prior effective games must be positive")
    if not values:
        return prior, 1.0
    decay = math.exp(math.log(0.5) / halflife_games)
    weights = [decay**index for index in reversed(range(len(values)))]
    observed_weight = sum(weights)
    estimate = (
        prior * prior_effective_games
        + sum(weight * value for weight, value in zip(weights, values, strict=True))
    ) / (prior_effective_games + observed_weight)
    return estimate, prior_effective_games / (prior_effective_games + observed_weight)


def regressed_prior(previous: float | None, league_mean: float, fraction: float) -> float:
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("regression fraction must be between zero and one")
    if previous is None:
        return league_mean
    return (1.0 - fraction) * previous + fraction * league_mean


def rating_feature_values(
    event: EventVersion, ratings: Mapping[str, Mapping[str, float]]
) -> dict[str, float]:
    values: dict[str, float] = {}
    for system, prefix in (
        ("elo", "elo"),
        ("colley", "colley"),
        ("massey", "massey"),
        ("off_epa", "off_epa"),
        ("def_epa", "def_epa"),
        ("pass_epa", "pass_epa"),
        ("rush_epa", "rush_epa"),
    ):
        home = float(ratings[system][event.home_team])
        away = float(ratings[system][event.away_team])
        values[f"home_{prefix}"] = home
        values[f"away_{prefix}"] = away
        values[f"{prefix}_diff"] = home - away
    if tuple(values) != RATING_FEATURE_NAMES:
        raise ValueError("rating feature block does not match V1 schema")
    return values


def _require_exact_payload(
    payload: Mapping[str, Any], expected: tuple[str, ...], *, label: str
) -> None:
    if set(payload) != set(expected):
        raise ValueError(f"{label} payload must contain exactly {expected}")


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a nonempty string")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _utc_iso(value: object, *, field: str) -> datetime:
    text = _string(value, field=field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field} must be an aware-UTC ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must be an aware-UTC ISO timestamp")
    return parsed


def _strength_payload(fact: NormalizedFact) -> dict[str, float]:
    _require_exact_payload(fact.payload, STRENGTH_KEYS, label="strength prior")
    return {
        key: _finite_number(fact.payload[key], field=f"strength prior.{key}")
        for key in STRENGTH_KEYS
    }


def _completed_game(fact: NormalizedFact) -> CompletedGame:
    _require_exact_payload(fact.payload, COMPLETED_GAME_KEYS, label="completed_game")
    canonical_event_id = _string(
        fact.entity_keys.get("canonical_event_id"),
        field="completed_game.canonical_event_id",
    )
    home_team = _string(fact.payload["home_team"], field="completed_game.home_team")
    away_team = _string(fact.payload["away_team"], field="completed_game.away_team")
    if home_team == away_team:
        raise ValueError("completed_game teams must differ")
    if fact.observed_at_utc is None:
        raise ValueError("completed_game requires finalized observed_at_utc")
    _utc_iso(fact.payload["kickoff_at_utc"], field="completed_game.kickoff_at_utc")
    return CompletedGame(
        canonical_event_id=canonical_event_id,
        season=_integer(fact.payload["season"], field="completed_game.season", minimum=1),
        home_team=home_team,
        away_team=away_team,
        home_score=_integer(fact.payload["home_score"], field="completed_game.home_score"),
        away_score=_integer(fact.payload["away_score"], field="completed_game.away_score"),
        neutral_site=_boolean(fact.payload["neutral_site"], field="completed_game.neutral_site"),
        finalized_at_utc=fact.observed_at_utc,
    )


def _team_game_epa(fact: NormalizedFact) -> TeamGameEpa:
    _require_exact_payload(fact.payload, TEAM_GAME_EPA_KEYS, label="team_game_epa")
    if fact.observed_at_utc is None:
        raise ValueError("team_game_epa requires finalized observed_at_utc")
    return TeamGameEpa(
        canonical_event_id=_string(
            fact.entity_keys.get("canonical_event_id"),
            field="team_game_epa.canonical_event_id",
        ),
        offense_team=_string(
            fact.entity_keys.get("offense_team"), field="team_game_epa.offense_team"
        ),
        defense_team=_string(
            fact.entity_keys.get("defense_team"), field="team_game_epa.defense_team"
        ),
        offense_epa_per_play=_finite_number(
            fact.payload["offense_epa_per_play"],
            field="team_game_epa.offense_epa_per_play",
        ),
        pass_epa_per_play=_finite_number(
            fact.payload["pass_epa_per_play"], field="team_game_epa.pass_epa_per_play"
        ),
        rush_epa_per_play=_finite_number(
            fact.payload["rush_epa_per_play"], field="team_game_epa.rush_epa_per_play"
        ),
        plays=_integer(fact.payload["plays"], field="team_game_epa.plays", minimum=1),
        finalized_at_utc=fact.observed_at_utc,
    )


class PointInTimeRatingService:
    def __init__(self, *, policy: FeaturePolicy) -> None:
        self.policy = policy

    def _league_prior(
        self, event: EventVersion, facts: Sequence[NormalizedFact]
    ) -> dict[str, float]:
        matches = [
            fact
            for fact in facts
            if fact.fact_type == "league_strength_prior"
            and fact.entity_keys.get("season") == str(event.season)
        ]
        if len(matches) != 1:
            raise ValueError("target season requires exactly one league_strength_prior")
        return _strength_payload(matches[0])

    def _team_prior(
        self,
        event: EventVersion,
        facts: Sequence[NormalizedFact],
        team: str,
        league: Mapping[str, float],
    ) -> dict[str, float]:
        matches = [
            fact
            for fact in facts
            if fact.fact_type == "team_strength_prior"
            and fact.entity_keys.get("season") == str(event.season)
            and fact.entity_keys.get("team") == team
        ]
        if len(matches) > 1:
            raise ValueError(f"team {team} must have at most one team_strength_prior")
        previous = _strength_payload(matches[0]) if matches else None
        return {
            key: regressed_prior(
                None if previous is None else previous[key],
                league[key],
                self.policy.offseason_regression_to_mean,
            )
            for key in STRENGTH_KEYS
        }

    @staticmethod
    def _deduplicated_games(
        event: EventVersion, cutoff: datetime, facts: Sequence[NormalizedFact]
    ) -> list[CompletedGame]:
        by_id: dict[str, CompletedGame] = {}
        for fact in sorted(facts, key=lambda item: item.fact_id):
            if fact.fact_type != "completed_game":
                continue
            if fact.observed_at_utc is None:
                raise ValueError("completed_game requires finalized observed_at_utc")
            if fact.observed_at_utc >= cutoff:
                continue
            game = _completed_game(fact)
            if game.season != event.season:
                continue
            existing = by_id.get(game.canonical_event_id)
            if existing is not None and existing != game:
                raise ValueError(
                    f"conflicting completed_game duplicates for {game.canonical_event_id}"
                )
            by_id[game.canonical_event_id] = game
        return sorted(
            by_id.values(),
            key=lambda game: (game.finalized_at_utc, game.canonical_event_id),
        )

    @staticmethod
    def _deduplicated_epa(
        cutoff: datetime,
        facts: Sequence[NormalizedFact],
        current_game_ids: set[str],
    ) -> list[TeamGameEpa]:
        by_key: dict[tuple[str, str, str], TeamGameEpa] = {}
        for fact in sorted(facts, key=lambda item: item.fact_id):
            if fact.fact_type != "team_game_epa":
                continue
            if fact.observed_at_utc is None:
                raise ValueError("team_game_epa requires finalized observed_at_utc")
            if fact.observed_at_utc >= cutoff:
                continue
            row = _team_game_epa(fact)
            if row.canonical_event_id not in current_game_ids:
                continue
            key = (row.canonical_event_id, row.offense_team, row.defense_team)
            existing = by_key.get(key)
            if existing is not None and existing != row:
                raise ValueError(f"conflicting team_game_epa duplicates for {key}")
            by_key[key] = row
        return sorted(
            by_key.values(),
            key=lambda row: (
                row.finalized_at_utc,
                row.canonical_event_id,
                row.offense_team,
                row.defense_team,
            ),
        )

    def _blend(self, raw: float, prior: float, observations: int) -> float:
        if observations == 0:
            return prior
        decay = math.exp(math.log(0.5) / self.policy.ewma_halflife_games)
        observed_weight = sum(decay**index for index in range(observations))
        return (prior * self.policy.prior_effective_games + raw * observed_weight) / (
            self.policy.prior_effective_games + observed_weight
        )

    def features(
        self,
        event: EventVersion,
        cutoff: datetime,
        facts: list[NormalizedFact],
    ) -> dict[str, float]:
        league = self._league_prior(event, facts)
        teams = (event.home_team, event.away_team)
        priors = {team: self._team_prior(event, facts, team, league) for team in teams}
        games = self._deduplicated_games(event, cutoff, facts)
        game_counts = {
            team: sum(team in {game.home_team, game.away_team} for game in games) for team in teams
        }

        elo = EloRater(
            initial=self.policy.elo_initial,
            home_field_points=self.policy.elo_home_field_points,
            k_factor=self.policy.elo_k_factor,
            offseason_retention=self.policy.elo_offseason_retention,
            logistic_scale=self.policy.elo_logistic_scale,
            mov_denominator=self.policy.elo_mov_denominator,
            mov_rating_scale=self.policy.elo_mov_rating_scale,
        ).snapshot(games, cutoff)
        colley = colley_ratings(games, cutoff)
        massey = massey_ratings(games, self.policy.massey_home_field_points, cutoff)

        rows = self._deduplicated_epa(cutoff, facts, {game.canonical_event_id for game in games})
        off_fit = fit_opponent_adjusted_epa(
            rows,
            cutoff,
            metric="offense_epa_per_play",
            alpha=self.policy.epa_ridge_alpha,
        )
        pass_fit = fit_opponent_adjusted_epa(
            rows,
            cutoff,
            metric="pass_epa_per_play",
            alpha=self.policy.epa_ridge_alpha,
        )
        rush_fit = fit_opponent_adjusted_epa(
            rows,
            cutoff,
            metric="rush_epa_per_play",
            alpha=self.policy.epa_ridge_alpha,
        )
        offense_counts = {team: sum(row.offense_team == team for row in rows) for team in teams}
        defense_counts = {team: sum(row.defense_team == team for row in rows) for team in teams}

        ratings: dict[str, dict[str, float]] = {key: {} for key in STRENGTH_KEYS}
        for team in teams:
            ratings["elo"][team] = self._blend(
                elo.values.get(team, self.policy.elo_initial),
                priors[team]["elo"],
                game_counts[team],
            )
            ratings["colley"][team] = self._blend(
                colley.get(team, league["colley"]),
                priors[team]["colley"],
                game_counts[team],
            )
            ratings["massey"][team] = self._blend(
                massey.get(team, league["massey"]),
                priors[team]["massey"],
                game_counts[team],
            )
            ratings["off_epa"][team] = self._blend(
                off_fit.league_mean + off_fit.offense.get(team, 0.0),
                priors[team]["off_epa"],
                offense_counts[team],
            )
            ratings["def_epa"][team] = self._blend(
                off_fit.league_mean - off_fit.defense.get(team, 0.0),
                priors[team]["def_epa"],
                defense_counts[team],
            )
            ratings["pass_epa"][team] = self._blend(
                pass_fit.league_mean + pass_fit.offense.get(team, 0.0),
                priors[team]["pass_epa"],
                offense_counts[team],
            )
            ratings["rush_epa"][team] = self._blend(
                rush_fit.league_mean + rush_fit.offense.get(team, 0.0),
                priors[team]["rush_epa"],
                offense_counts[team],
            )
        return rating_feature_values(event, ratings)
