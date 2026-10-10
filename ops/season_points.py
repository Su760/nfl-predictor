"""Prior-game points features and small serializable regularized linear models."""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
from sklearn.linear_model import Ridge

from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.massey import massey_ratings

FEATURES = {
    "margin": ["rating_diff", "home_advantage", "scored_diff", "allowed_diff", "pace_diff"],
    "total": ["absolute_rating_diff", "home_advantage", "scored_sum", "allowed_sum", "pace_sum"],
}


def time(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return at


def valid_score(game):
    return all(
        isinstance(game.get(k), int) and not isinstance(game[k], bool) and game[k] >= 0
        for k in ("home_score", "away_score")
    )


def home_advantage(games, season, cfg):
    prior = [
        g
        for g in games
        if g["season"] <= season - 2
        and g["game_type"] == "REG"
        and not g["neutral_site"]
        and valid_score(g)
    ]
    if not prior:
        return cfg["initial_home_field_points"]
    return float(np.mean([g["home_score"] - g["away_score"] for g in prior]))


def features(target, games, cutoff, hfa, cfg):
    if cutoff.tzinfo is None or type(target.get("neutral_site")) is not bool:
        raise ValueError("INVALID_CUTOFF_OR_VENUE")
    prior = sorted(
        [
            g
            for g in games
            if g["game_id"] != target["game_id"]
            and target["season"] - 1 <= g["season"] <= target["season"]
            and g["game_type"] == "REG"
            and valid_score(g)
            and time(g["available_at"]) < cutoff
            and time(g["kickoff"]) < cutoff
        ],
        key=lambda g: (g["kickoff"], g["game_id"]),
    )
    if len({g["game_id"] for g in prior}) != len(prior):
        raise ValueError("DUPLICATE_HISTORY_GAME")
    base = {
        "baseline": None,
        "features": None,
        "reason": None,
        "history_ids": [g["game_id"] for g in prior],
        "pace_history_ids": {},
    }
    histories = {
        team: [g for g in prior if team in (g["home"], g["away"])]
        for team in (target["home"], target["away"])
    }
    if any(len(v) < cfg["minimum_team_games"] for v in histories.values()):
        return {**base, "reason": "INSUFFICIENT_SCORE_HISTORY"}
    completed = [
        CompletedGame(
            g["game_id"],
            g["season"],
            g["home"],
            g["away"],
            g["home_score"],
            g["away_score"],
            g["neutral_site"],
            time(g["available_at"]),
        )
        for g in prior
    ]
    ratings = massey_ratings(completed, hfa, cutoff)
    league = np.mean([(g["home_score"] + g["away_score"]) / 2 for g in prior])
    scored, allowed, pace, pace_ids = {}, {}, {}, {}
    for team, history in histories.items():
        recent = history[-cfg["rolling_games"] :]
        shrink = cfg["shrinkage_games"]
        scored[team] = (
            sum(g["home_score"] if g["home"] == team else g["away_score"] for g in recent)
            + shrink * league
        ) / (len(recent) + shrink)
        allowed[team] = (
            sum(g["away_score"] if g["home"] == team else g["home_score"] for g in recent)
            + shrink * league
        ) / (len(recent) + shrink)
        observed = [
            g
            for g in recent
            if g.get("pace_available_at")
            and time(g["pace_available_at"]) < cutoff
            and isinstance(g.get("pace", {}).get(team), (float, int))
            and math.isfinite(g["pace"][team])
            and g["pace"][team] > 0
        ]
        pace_ids[team] = [g["game_id"] for g in observed]
        if len(observed) >= cfg["minimum_team_games"]:
            pace[team] = float(np.mean([g["pace"][team] for g in observed]))
        ratings[team] *= len(history) / (len(history) + shrink)
    home, away = target["home"], target["away"]
    strength = ratings[home] - ratings[away]
    venue = 0.0 if target["neutral_site"] else hfa
    baseline = {
        "margin": strength + venue,
        "total": float((scored[home] + scored[away] + allowed[home] + allowed[away]) / 2),
    }
    base.update(baseline=baseline, pace_history_ids=pace_ids)
    if len(pace) != 2:
        return {**base, "reason": "INSUFFICIENT_PACE_HISTORY"}
    base["features"] = {
        "margin": [
            strength,
            venue,
            scored[home] - scored[away],
            allowed[home] - allowed[away],
            pace[home] - pace[away],
        ],
        "total": [
            abs(strength),
            venue,
            scored[home] + scored[away],
            allowed[home] + allowed[away],
            pace[home] + pace[away],
        ],
    }
    return base


def fit_linear(x, y, alpha):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 2 or y.shape != (len(x),) or not len(x) or alpha <= 0:
        raise ValueError("INVALID_LINEAR_TRAINING")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("MISSING_LINEAR_TRAINING")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1
    model = Ridge(alpha=alpha, fit_intercept=True, solver="svd").fit((x - mean) / scale, y)
    return {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "beta": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "alpha": alpha,
        "n": len(x),
    }


def predict_linear(model, x):
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(model["mean"]) or not np.isfinite(x).all():
        raise ValueError("INVALID_LINEAR_FEATURES")
    return model["intercept"] + ((x - np.asarray(model["mean"])) / model["scale"]) @ model["beta"]


def interval_radius(residuals, level):
    values = np.sort(np.abs(np.asarray(residuals, dtype=float)))
    if not len(values) or not np.isfinite(values).all() or not 0 < level < 1:
        raise ValueError("INVALID_INTERVAL_CALIBRATION")
    return float(values[min(len(values), math.ceil((len(values) + 1) * level)) - 1])


def spread(margin, home, away):
    return {
        "home_spread": -margin,
        "favorite": home if margin > 0 else away if margin < 0 else "PICK",
        "favorite_spread": -abs(margin),
    }
