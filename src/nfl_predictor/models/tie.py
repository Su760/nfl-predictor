from __future__ import annotations

import math
from dataclasses import dataclass

_ALLOWED_RESULTS = frozenset({"home", "away", "tie"})


@dataclass(frozen=True)
class TieLayer:
    p_tie: float

    @classmethod
    def fit(cls, results: list[str]) -> TieLayer:
        if not results:
            raise ValueError("tie fitting requires at least one result")
        if set(results) - _ALLOWED_RESULTS:
            raise ValueError("results must contain only home, away, or tie")
        ties = sum(result == "tie" for result in results)
        return cls((ties + 0.5) / (len(results) + 1.0))


def to_three_way(r_home: float, p_tie: float) -> tuple[float, float, float]:
    if (
        not math.isfinite(r_home)
        or not math.isfinite(p_tie)
        or not 0.0 <= r_home <= 1.0
        or not 0.0 <= p_tie <= 1.0
    ):
        raise ValueError("probabilities must be finite and inside [0, 1]")
    remaining = 1.0 - p_tie
    return remaining * r_home, remaining * (1.0 - r_home), p_tie
