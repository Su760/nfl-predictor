from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nfl_predictor.ratings.base import TeamGameEpa
from nfl_predictor.ratings.epa import fit_opponent_adjusted_epa


@pytest.fixture
def cutoff() -> datetime:
    return datetime(2026, 9, 15, tzinfo=UTC)


@pytest.fixture
def team_game_epa_rows(cutoff: datetime) -> list[TeamGameEpa]:
    return [
        TeamGameEpa("one", "SEA", "NE", 0.40, 0.45, 0.30, 60, cutoff - timedelta(days=2)),
        TeamGameEpa("two", "NE", "SEA", -0.20, -0.10, -0.35, 55, cutoff - timedelta(days=1)),
        TeamGameEpa("future", "SEA", "NE", -0.10, -0.05, -0.20, 50, cutoff),
    ]


def test_epa_fit_uses_only_rows_finalized_before_cutoff(
    team_game_epa_rows: list[TeamGameEpa], cutoff: datetime
) -> None:
    changed_future = [
        replace(
            row,
            offense_team="DAL",
            defense_team="MIA",
            offense_epa_per_play=100.0,
            pass_epa_per_play=100.0,
            rush_epa_per_play=100.0,
        )
        if row.finalized_at_utc == cutoff
        else row
        for row in team_game_epa_rows
    ]

    baseline = fit_opponent_adjusted_epa(
        team_game_epa_rows[:2], cutoff, metric="offense_epa_per_play", alpha=10.0
    )

    assert (
        fit_opponent_adjusted_epa(
            team_game_epa_rows, cutoff, metric="offense_epa_per_play", alpha=10.0
        )
        == baseline
    )
    assert (
        fit_opponent_adjusted_epa(changed_future, cutoff, metric="offense_epa_per_play", alpha=10.0)
        == baseline
    )


def test_epa_fit_weights_league_mean_by_plays(cutoff: datetime) -> None:
    rows = [
        TeamGameEpa("heavy", "SEA", "NE", 1.0, 1.0, 1.0, 100, cutoff - timedelta(days=2)),
        TeamGameEpa("light", "SEA", "NE", -1.0, -1.0, -1.0, 1, cutoff - timedelta(days=1)),
    ]

    ratings = fit_opponent_adjusted_epa(rows, cutoff, metric="offense_epa_per_play", alpha=10.0)

    assert ratings.league_mean == pytest.approx(99.0 / 101.0)


@pytest.mark.parametrize(
    ("metric", "expected_effect"),
    [
        ("offense_epa_per_play", 0.25),
        ("pass_epa_per_play", 0.50),
        ("rush_epa_per_play", 0.75),
    ],
)
def test_epa_fit_returns_closed_form_opponent_effects_for_each_metric(
    cutoff: datetime, metric: str, expected_effect: float
) -> None:
    rows = [
        TeamGameEpa("sea-ne", "SEA", "NE", 3.0, 6.0, 9.0, 1, cutoff - timedelta(days=2)),
        TeamGameEpa("ne-sea", "NE", "SEA", -3.0, -6.0, -9.0, 1, cutoff - timedelta(days=1)),
    ]

    ratings = fit_opponent_adjusted_epa(rows, cutoff, metric=metric, alpha=10.0)

    assert ratings.league_mean == pytest.approx(0.0)
    assert list(ratings.offense) == ["NE", "SEA"]
    assert list(ratings.defense) == ["NE", "SEA"]
    assert ratings.offense == pytest.approx({"NE": -expected_effect, "SEA": expected_effect})
    assert ratings.defense == pytest.approx({"NE": -expected_effect, "SEA": expected_effect})
    assert sum(ratings.offense.values()) == pytest.approx(0.0)
    assert sum(ratings.defense.values()) == pytest.approx(0.0)
