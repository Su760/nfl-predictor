import math
from datetime import datetime

from .base import CompletedGame, RatingSnapshot


class EloRater:
    def __init__(
        self,
        initial: float,
        home_field_points: float,
        k_factor: float,
        offseason_retention: float,
        logistic_scale: float,
        mov_denominator: float,
        mov_rating_scale: float,
    ) -> None:
        self.initial = initial
        self.home_field_points = home_field_points
        self.k_factor = k_factor
        self.offseason_retention = offseason_retention
        self.logistic_scale = logistic_scale
        self.mov_denominator = mov_denominator
        self.mov_rating_scale = mov_rating_scale
        self.ratings: dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial)

    def home_probability(self, home: str, away: str, neutral_site: bool) -> float:
        advantage = 0.0 if neutral_site else self.home_field_points
        difference = self.rating(home) - self.rating(away) + advantage
        return 1.0 / (1.0 + math.pow(10.0, -difference / self.logistic_scale))

    def update(self, game: CompletedGame) -> None:
        expected = self.home_probability(game.home_team, game.away_team, game.neutral_site)
        actual = (
            0.5 if game.home_score == game.away_score else float(game.home_score > game.away_score)
        )
        margin = abs(game.home_score - game.away_score)
        pregame_diff = self.rating(game.home_team) - self.rating(game.away_team)
        winner_diff = pregame_diff if actual >= 0.5 else -pregame_diff
        multiplier = math.log(max(margin, 1) + 1.0) * (
            self.mov_denominator / (winner_diff * self.mov_rating_scale + self.mov_denominator)
        )
        change = self.k_factor * multiplier * (actual - expected)
        self.ratings[game.home_team] = self.rating(game.home_team) + change
        self.ratings[game.away_team] = self.rating(game.away_team) - change

    def snapshot(self, games: list[CompletedGame], cutoff: datetime) -> RatingSnapshot:
        self.ratings = {
            team: self.initial
            for team in sorted(
                {team for game in games for team in (game.home_team, game.away_team)}
            )
        }
        current_season: int | None = None
        for game in sorted(
            games, key=lambda item: (item.season, item.finalized_at_utc, item.canonical_event_id)
        ):
            if game.finalized_at_utc >= cutoff:
                continue
            if current_season is not None and game.season != current_season:
                self.ratings = {
                    team: self.initial + (rating - self.initial) * self.offseason_retention
                    for team, rating in self.ratings.items()
                }
            self.update(game)
            current_season = game.season
        return RatingSnapshot("elo", cutoff, dict(self.ratings))
