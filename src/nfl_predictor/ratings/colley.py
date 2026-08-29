from collections.abc import Sequence
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from .base import CompletedGame


def colley_ratings(
    games: Sequence[CompletedGame], cutoff: datetime | None = None
) -> dict[str, float]:
    eligible = [game for game in games if cutoff is None or game.finalized_at_utc < cutoff]
    teams = sorted({team for game in eligible for team in (game.home_team, game.away_team)})
    if not teams:
        return {}

    index = {team: position for position, team in enumerate(teams)}
    matrix: NDArray[np.float64] = 2.0 * np.eye(len(teams))
    rhs: NDArray[np.float64] = np.ones(len(teams))
    for game in eligible:
        home, away = index[game.home_team], index[game.away_team]
        matrix[home, home] += 1.0
        matrix[away, away] += 1.0
        matrix[home, away] -= 1.0
        matrix[away, home] -= 1.0
        if game.home_score > game.away_score:
            rhs[home] += 0.5
            rhs[away] -= 0.5
        elif game.away_score > game.home_score:
            rhs[home] -= 0.5
            rhs[away] += 0.5

    solved = np.linalg.solve(matrix, rhs)
    return dict(zip(teams, solved.tolist(), strict=True))
