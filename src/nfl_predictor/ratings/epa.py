from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import Ridge  # type: ignore[import-untyped]

from .base import TeamGameEpa


@dataclass(frozen=True)
class OpponentAdjustedEpa:
    league_mean: float
    offense: dict[str, float]
    defense: dict[str, float]


def fit_opponent_adjusted_epa(
    rows: Sequence[TeamGameEpa], cutoff: datetime, metric: str, alpha: float
) -> OpponentAdjustedEpa:
    eligible = [row for row in rows if row.finalized_at_utc < cutoff and row.plays > 0]
    teams = sorted({team for row in eligible for team in (row.offense_team, row.defense_team)})
    if not eligible:
        return OpponentAdjustedEpa(0.0, {}, {})

    index = {team: position for position, team in enumerate(teams)}
    design: NDArray[np.float64] = np.zeros((len(eligible), len(teams) * 2))
    target = np.asarray([float(getattr(row, metric)) for row in eligible])
    weights = np.asarray([row.plays for row in eligible], dtype=float)
    for position, row in enumerate(eligible):
        design[position, index[row.offense_team]] = 1.0
        design[position, len(teams) + index[row.defense_team]] = -1.0

    model = Ridge(alpha=alpha, fit_intercept=True)
    model.fit(design, target, sample_weight=weights)
    offense = model.coef_[: len(teams)]
    defense = model.coef_[len(teams) :]
    offense -= offense.mean()
    defense -= defense.mean()
    return OpponentAdjustedEpa(
        league_mean=float(model.intercept_),
        offense=dict(zip(teams, offense.tolist(), strict=True)),
        defense=dict(zip(teams, defense.tolist(), strict=True)),
    )
