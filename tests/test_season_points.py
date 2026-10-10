from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from season_points import features, fit_linear, interval_radius, predict_linear, spread


def history():
    start = datetime(2024, 9, 1, tzinfo=UTC)
    return [
        {
            "game_id": str(i),
            "season": 2024,
            "week": i + 1,
            "home": "A",
            "away": "B",
            "home_score": 20 + i,
            "away_score": 17,
            "neutral_site": False,
            "game_type": "REG",
            "kickoff": (start + timedelta(days=i * 7)).isoformat(),
            "available_at": (start + timedelta(days=i * 7 + 1)).isoformat(),
            "pace": {"A": 65 + i, "B": 60 + i},
            "pace_available_at": (start + timedelta(days=i * 7 + 1)).isoformat(),
        }
        for i in range(8)
    ]


def cfg():
    return {"rolling_games": 8, "minimum_team_games": 3, "shrinkage_games": 4}


def target():
    return {"game_id": "target", "season": 2025, "home": "A", "away": "B", "neutral_site": False}


def test_strict_cutoff_current_game_and_season_boundary():
    games = history()
    cutoff = datetime.fromisoformat(games[-1]["available_at"])
    a = features(target(), games, cutoff, 2.5, cfg())
    extra = {**games[0], "game_id": "old", "season": 2023, "home_score": 9999}
    current = {**games[0], "game_id": "target", "home_score": 9999}
    future = {**games[-1], "game_id": "future", "home_score": 9999}
    b = features(target(), games + [extra, current, future], cutoff, 2.5, cfg())
    assert a == b
    assert games[-1]["game_id"] not in a["history_ids"]


def test_neutral_venue_and_sign():
    games = history()
    cutoff = datetime(2025, 9, 1, tzinfo=UTC)
    neutral = features({**target(), "neutral_site": True}, games, cutoff, 2.5, cfg())
    home = features(target(), games, cutoff, 2.5, cfg())
    assert home["baseline"]["margin"] - neutral["baseline"]["margin"] == pytest.approx(2.5)
    assert neutral["features"]["margin"][1] == 0
    assert spread(3, "A", "B") == {"home_spread": -3, "favorite": "A", "favorite_spread": -3}
    assert spread(-3, "A", "B") == {"home_spread": 3, "favorite": "B", "favorite_spread": -3}
    assert spread(0, "A", "B")["favorite"] == "PICK"


def test_missing_is_unavailable_not_zero():
    games = history()
    cutoff = datetime(2025, 9, 1, tzinfo=UTC)
    assert (
        features(target(), games[:2], cutoff, 2.5, cfg())["reason"] == "INSUFFICIENT_SCORE_HISTORY"
    )
    for g in games:
        g["pace"] = {}
    result = features(target(), games, cutoff, 2.5, cfg())
    assert result["baseline"]["total"] > 0 and result["features"] is None
    assert result["reason"] == "INSUFFICIENT_PACE_HISTORY"
    games[0]["home_score"] = None
    assert "0" not in features(target(), games, cutoff, 2.5, cfg())["history_ids"]


def test_pace_not_available_at_cutoff():
    games = history()
    cutoff = datetime(2025, 9, 1, tzinfo=UTC)
    for g in games:
        g["pace_available_at"] = cutoff.isoformat()
    assert features(target(), games, cutoff, 2.5, cfg())["features"] is None


def test_training_only_scaling_and_missing_rejection():
    model = fit_linear([[1, 2], [2, 2], [3, 2]], [3, 5, 7], 10)
    assert model["mean"] == [2, 2] and model["scale"][1] == 1
    prediction = predict_linear(model, [[999, 2]])
    assert np.isfinite(prediction).all() and model["mean"] == [2, 2]
    with pytest.raises(ValueError):
        fit_linear([[1, np.nan]], [1], 1)
    with pytest.raises(ValueError):
        predict_linear(model, [[None, 2]])


def test_interval_order_statistic_and_empty_rejection():
    assert interval_radius([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.8) == 9
    with pytest.raises(ValueError):
        interval_radius([], 0.8)
