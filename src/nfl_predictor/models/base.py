from __future__ import annotations

from typing import Protocol, Self

import numpy as np
from numpy.typing import NDArray

FeatureMatrix = NDArray[np.float64]
ProbabilityVector = NDArray[np.float64]


class ProbabilityModel(Protocol):
    def fit(self, features: FeatureMatrix, results: list[str]) -> Self: ...

    def predict_r_home(self, features: FeatureMatrix) -> ProbabilityVector: ...
