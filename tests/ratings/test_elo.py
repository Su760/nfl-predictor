from datetime import UTC, datetime, timedelta

import pytest

from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.elo import EloRater


def _rater() -> EloRater:
    return EloRater(
        initial=1505.0,
        home_field_points=65.0,
        k_factor=20.0,
        offseason_retention=2.0 / 3.0,
        logistic_scale=400.0,
        mov_denominator=2.2,
        mov_rating_scale=0.001,
    )


def test_equal_teams_home_probability_matches_65_point_hfa() -> None:
    assert _rater().home_probability("SEA", "NE", neutral_site=False) == pytest.approx(
        1 / (1 + 10 ** (-65 / 400))
    )


def test_snapshot_ignores_game_finalized_after_cutoff() -> None:
    cutoff = datetime(2026, 9, 1, tzinfo=UTC)
    future = CompletedGame(
        canonical_event_id="future",
        season=2026,
        home_team="SEA",
        away_team="NE",
        home_score=30,
        away_score=10,
        neutral_site=False,
        finalized_at_utc=cutoff + timedelta(seconds=1),
    )

    snapshot = _rater().snapshot([future], cutoff)

    assert snapshot.values["SEA"] == 1505.0
    assert snapshot.values["NE"] == 1505.0


def test_snapshot_regresses_inactive_franchise_before_new_season_game() -> None:
    first_season_end = datetime(2025, 12, 31, tzinfo=UTC)
    second_season_start = datetime(2026, 9, 1, tzinfo=UTC)
    games = [
        CompletedGame("one", 2025, "SEA", "NE", 31, 10, False, first_season_end),
        CompletedGame(
            "two", 2025, "CHI", "SEA", 27, 10, False, first_season_end + timedelta(days=1)
        ),
        CompletedGame("three", 2026, "SEA", "NE", 24, 17, False, second_season_start),
    ]
    prior = _rater().snapshot(games[:2], second_season_start)

    snapshot = _rater().snapshot(games, second_season_start + timedelta(days=1))

    assert snapshot.values["CHI"] == pytest.approx(
        1505.0 + (prior.values["CHI"] - 1505.0) * (2.0 / 3.0)
    )
