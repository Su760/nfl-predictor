from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from nfl_predictor.models.baselines import ModelPolicy


def _probability_array(probabilities: Sequence[float]) -> NDArray[np.float64]:
    values = tuple(probabilities)
    if not values:
        raise ValueError("calibration requires at least one probability")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise TypeError("calibration probabilities must be real numbers")
    array = np.asarray(values, dtype=np.float64)
    if not np.isfinite(array).all():
        raise ValueError("calibration probabilities must be finite")
    if ((array < 0.0) | (array > 1.0)).any():
        raise ValueError("calibration probabilities must be inside [0, 1]")
    return array


def _binary_labels(labels: Sequence[int]) -> NDArray[np.int64]:
    values = tuple(labels)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise TypeError("calibration labels must be integers")
    if set(values) - {0, 1}:
        raise ValueError("calibration labels must contain only 0 or 1")
    return np.asarray(values, dtype=np.int64)


def _epsilon(epsilon: float) -> float:
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)):
        raise TypeError("logit epsilon must be a real number")
    value = float(epsilon)
    if not math.isfinite(value) or not 0.0 < value < 0.5:
        raise ValueError("logit epsilon must be finite and inside (0, 0.5)")
    return value


def clipped_logit(probabilities: Sequence[float], epsilon: float) -> NDArray[np.float64]:
    values = np.clip(_probability_array(probabilities), _epsilon(epsilon), 1.0 - epsilon)
    return np.asarray(np.log(values / (1.0 - values)).reshape(-1, 1), dtype=np.float64)


class IdentityCalibrator:
    family = "identity"

    @classmethod
    def from_policy(cls, policy: ModelPolicy) -> IdentityCalibrator:
        return cls()

    def fit(self, base_probabilities: Sequence[float], labels: Sequence[int]) -> IdentityCalibrator:
        probabilities = _probability_array(base_probabilities)
        target = _binary_labels(labels)
        if len(probabilities) != len(target):
            raise ValueError("calibration rows and labels must be exactly aligned")
        return self

    def transform(self, base_probabilities: Sequence[float]) -> NDArray[np.float64]:
        return _probability_array(base_probabilities).copy()


class SigmoidCalibrator:
    family = "sigmoid"

    def __init__(self, c: float, max_iter: int, random_seed: int, logit_epsilon: float) -> None:
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise TypeError("sigmoid C must be a real number")
        c_value = float(c)
        if not math.isfinite(c_value) or c_value <= 0.0:
            raise ValueError("sigmoid C must be finite and positive")
        if isinstance(max_iter, bool) or not isinstance(max_iter, int):
            raise TypeError("sigmoid max_iter must be an integer")
        if max_iter < 1:
            raise ValueError("sigmoid max_iter must be positive")
        if isinstance(random_seed, bool) or not isinstance(random_seed, int):
            raise TypeError("sigmoid random seed must be an integer")
        if not 0 <= random_seed < 2**32:
            raise ValueError("sigmoid random seed must be inside [0, 2**32)")
        self.logit_epsilon = _epsilon(logit_epsilon)
        self.mapping = LogisticRegression(C=c_value, max_iter=max_iter, random_state=random_seed)
        self._is_fitted = False

    @classmethod
    def from_policy(cls, policy: ModelPolicy) -> SigmoidCalibrator:
        return cls(
            c=policy.calibration.sigmoid_c,
            max_iter=policy.logistic.max_iter,
            random_seed=policy.random_seed,
            logit_epsilon=policy.calibration.logit_epsilon,
        )

    def fit(self, base_probabilities: Sequence[float], labels: Sequence[int]) -> SigmoidCalibrator:
        probabilities = _probability_array(base_probabilities)
        target = _binary_labels(labels)
        if len(probabilities) != len(target):
            raise ValueError("calibration rows and labels must be exactly aligned")
        if set(target.tolist()) != {0, 1}:
            raise ValueError("sigmoid calibration requires both binary classes")
        self.mapping.fit(clipped_logit(probabilities.tolist(), self.logit_epsilon), target)
        self._is_fitted = True
        return self

    def transform(self, base_probabilities: Sequence[float]) -> NDArray[np.float64]:
        if not self._is_fitted:
            raise RuntimeError("sigmoid calibrator must be fit before transform")
        probabilities = self.mapping.predict_proba(
            clipped_logit(base_probabilities, self.logit_epsilon)
        )[:, 1]
        return np.asarray(probabilities, dtype=np.float64)


CalibratorType: TypeAlias = type[IdentityCalibrator] | type[SigmoidCalibrator]

CALIBRATOR_REGISTRY: dict[str, CalibratorType] = {
    "identity": IdentityCalibrator,
    "sigmoid": SigmoidCalibrator,
}
