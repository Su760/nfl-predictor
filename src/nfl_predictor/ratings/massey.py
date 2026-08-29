from collections.abc import Sequence
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from .base import CompletedGame


def massey_ratings(
    games: Sequence[CompletedGame], home_field_points: float, cutoff: datetime | None = None
) -> dict[str, float]:
    eligible = [game for game in games if cutoff is None or game.finalized_at_utc < cutoff]
    teams = sorted({team for game in eligible for team in (game.home_team, game.away_team)})
    if not teams:
        return {}

    index = {team: position for position, team in enumerate(teams)}
    design: NDArray[np.float64] = np.zeros((len(eligible), len(teams)))
    margins: NDArray[np.float64] = np.zeros(len(eligible))
    for row, game in enumerate(eligible):
        design[row, index[game.home_team]] = 1.0
        design[row, index[game.away_team]] = -1.0
        hfa = 0.0 if game.neutral_site else home_field_points
        margins[row] = game.home_score - game.away_score - hfa

    normal = design.T @ design
    rhs = design.T @ margins
    constrained = np.block(
        [
            [normal, np.ones((len(teams), 1))],
            [np.ones((1, len(teams))), np.zeros((1, 1))],
        ]
    )
    solved = np.linalg.lstsq(constrained, np.append(rhs, 0.0), rcond=None)[0][:-1]
    solved -= solved.mean()
    return dict(zip(teams, solved.tolist(), strict=True))
