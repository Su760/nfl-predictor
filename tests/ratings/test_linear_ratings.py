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


def test_two_team_colley_and_massey_match_closed_form_solution(
    completed_game: CompletedGame,
) -> None:
    colley = colley_ratings([completed_game])
    massey = massey_ratings([completed_game], home_field_points=2.5)

    assert colley == pytest.approx({"NE": 3.0 / 8.0, "SEA": 5.0 / 8.0})
    assert massey == pytest.approx({"NE": -5.75, "SEA": 5.75})


def test_massey_disconnected_early_season_solution_is_zero_sum_and_deterministic() -> None:
    finalized = datetime(2026, 9, 10, tzinfo=UTC)
    games = [
        CompletedGame("sea-ne", 2026, "SEA", "NE", 10, 0, True, finalized),
        CompletedGame("chi-dal", 2026, "CHI", "DAL", 6, 0, True, finalized),
    ]

    ratings = massey_ratings(games, home_field_points=2.5)

    assert list(ratings) == ["CHI", "DAL", "NE", "SEA"]
    assert sum(ratings.values()) == pytest.approx(0.0)
    assert ratings == pytest.approx({"CHI": 3.0, "DAL": -3.0, "NE": -5.0, "SEA": 5.0})
    assert ratings == massey_ratings(games, home_field_points=2.5)


def test_linear_ratings_ignore_future_mutations_at_cutoff(completed_game: CompletedGame) -> None:
    cutoff = completed_game.finalized_at_utc + timedelta(hours=1)
    future = CompletedGame(
        canonical_event_id="future",
        season=2026,
        home_team="DAL",
        away_team="MIA",
        home_score=99,
        away_score=0,
        neutral_site=False,
        finalized_at_utc=cutoff,
    )
    changed_future = CompletedGame(
        canonical_event_id="future",
        season=2026,
        home_team="MIA",
        away_team="DAL",
        home_score=0,
        away_score=99,
        neutral_site=True,
        finalized_at_utc=cutoff,
    )

    baseline_colley = colley_ratings([completed_game], cutoff)
    baseline_massey = massey_ratings([completed_game], 2.5, cutoff)

    assert colley_ratings([completed_game, future], cutoff) == baseline_colley
    assert colley_ratings([completed_game, changed_future], cutoff) == baseline_colley
    assert massey_ratings([completed_game, future], 2.5, cutoff) == pytest.approx(baseline_massey)
    assert massey_ratings([completed_game, changed_future], 2.5, cutoff) == pytest.approx(
        baseline_massey
    )
