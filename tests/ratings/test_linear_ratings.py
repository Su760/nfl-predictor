from datetime import UTC, datetime, timedelta

import pytest

from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.colley import colley_ratings
from nfl_predictor.ratings.massey import massey_ratings


@pytest.fixture
def completed_game() -> CompletedGame:
    return CompletedGame(
        canonical_event_id="sea-ne-2026",
        season=2026,
        home_team="SEA",
        away_team="NE",
        home_score=28,
        away_score=14,
        neutral_site=False,
        finalized_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
    )


def test_two_team_colley_and_massey_rank_winner_higher(completed_game: CompletedGame) -> None:
    assert colley_ratings([completed_game])[completed_game.home_team] > 0.5

    ratings = massey_ratings([completed_game], home_field_points=2.5)

    assert ratings[completed_game.home_team] > ratings[completed_game.away_team]


def test_massey_singular_early_season_solution_is_zero_sum_and_deterministic(
    completed_game: CompletedGame,
) -> None:
    ratings = massey_ratings([completed_game], home_field_points=2.5)

    assert list(ratings) == ["NE", "SEA"]
    assert sum(ratings.values()) == pytest.approx(0.0)
    assert ratings == massey_ratings([completed_game], home_field_points=2.5)


def test_linear_ratings_ignore_games_finalized_after_cutoff(completed_game: CompletedGame) -> None:
    cutoff = completed_game.finalized_at_utc + timedelta(hours=1)
    future = CompletedGame(
        canonical_event_id="future",
        season=2026,
        home_team="NE",
        away_team="SEA",
        home_score=99,
        away_score=0,
        neutral_site=False,
        finalized_at_utc=cutoff + timedelta(seconds=1),
    )

    assert colley_ratings([completed_game, future], cutoff) == colley_ratings([completed_game])
    assert massey_ratings([completed_game, future], 2.5, cutoff) == pytest.approx(
        massey_ratings([completed_game], 2.5)
    )
