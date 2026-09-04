from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Annotated

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from nfl_predictor.features.schema import FEATURE_NAMES_V1

from .base import FeatureMatrix, ProbabilityVector

_ALLOWED_RESULTS = frozenset({"home", "away", "tie"})

FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.0)]
NonNegativeFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0.0)]
NonBlank = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


class _PolicySection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _EloPolicy(_PolicySection):
    initial: PositiveFiniteFloat
    home_field_points: FiniteFloat
    k_factor: PositiveFiniteFloat
    offseason_retention: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0.0, le=1.0)]
    logistic_scale: PositiveFiniteFloat
    mov_denominator: PositiveFiniteFloat
    mov_rating_scale: PositiveFiniteFloat


class _MasseyPolicy(_PolicySection):
    home_field_points: FiniteFloat


class _EpaPolicy(_PolicySection):
    ridge_alpha: NonNegativeFiniteFloat


class _LogisticPolicy(_PolicySection):
    c: PositiveFiniteFloat
    max_iter: int = Field(strict=True, ge=1)


class _CalibrationPolicy(_PolicySection):
    sigmoid_c: PositiveFiniteFloat
    selection_tolerance: NonNegativeFiniteFloat
    logit_epsilon: Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.0, lt=0.5)]


class ModelPolicy(_PolicySection):
    policy_version: NonBlank
    random_seed: int = Field(strict=True, ge=0, lt=2**32)
    elo: _EloPolicy
    massey: _MasseyPolicy
    epa: _EpaPolicy
    logistic: _LogisticPolicy
    calibration: _CalibrationPolicy


def load_model_policy(path: Path) -> ModelPolicy:
    with path.open("rb") as handle:
        return ModelPolicy.model_validate(tomllib.load(handle))


def _as_feature_matrix(features: FeatureMatrix) -> FeatureMatrix:
    matrix = np.asarray(features, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("features must be a 2-D matrix")
    expected_width = len(FEATURE_NAMES_V1)
    if matrix.shape[1] != expected_width:
        raise ValueError(f"features must contain exactly {expected_width} ordered V1 columns")
    return matrix


def non_tie_rows(
    features: FeatureMatrix, results: list[str]
) -> tuple[FeatureMatrix, NDArray[np.int64]]:
    matrix = _as_feature_matrix(features)
    if matrix.shape[0] != len(results):
        raise ValueError("feature row count must equal result count")
    invalid = sorted(set(results) - _ALLOWED_RESULTS)
    if invalid:
        raise ValueError("results must contain only home, away, or tie")
    mask = np.asarray([result != "tie" for result in results], dtype=np.bool_)
    if not mask.any():
        raise ValueError("training requires at least one non-tie game")
    labels = np.asarray([result == "home" for result in results], dtype=np.int64)[mask]
    return matrix[mask], labels


def _require_binary_classes(labels: NDArray[np.int64]) -> None:
    if set(labels.tolist()) != {0, 1}:
        raise ValueError("logistic fitting requires both home and away results")


def _validate_logistic_parameters(
    c: float, max_iter: int, random_seed: int
) -> tuple[float, int, int]:
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        raise TypeError("C must be a real number")
    c_value = float(c)
    if not math.isfinite(c_value) or c_value <= 0.0:
        raise ValueError("C must be finite and greater than zero")
    if isinstance(max_iter, bool) or not isinstance(max_iter, int):
        raise TypeError("max_iter must be an integer")
    if max_iter < 1:
        raise ValueError("max_iter must be at least one")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise TypeError("random_seed must be an integer")
    if not 0 <= random_seed < 2**32:
        raise ValueError("random_seed must be inside [0, 2**32)")
    return c_value, max_iter, random_seed


class HomePriorModel:
    def __init__(self) -> None:
        self._is_fitted = False
        self.n_fit = 0
        self.probability = 0.0

    def fit(self, features: FeatureMatrix, results: list[str]) -> HomePriorModel:
        _, labels = non_tie_rows(features, results)
        self.n_fit = len(labels)
        self.probability = float(labels.mean())
        self._is_fitted = True
        return self

    def predict_r_home(self, features: FeatureMatrix) -> ProbabilityVector:
        if not self._is_fitted:
            raise RuntimeError("model must be fit before prediction")
        matrix = _as_feature_matrix(features)
        return np.full(matrix.shape[0], self.probability, dtype=np.float64)


class RatingProbabilityModel:
    def __init__(
        self,
        difference_column: int,
        c: float,
        max_iter: int,
        random_seed: int,
    ) -> None:
        if isinstance(difference_column, bool) or not isinstance(difference_column, int):
            raise TypeError("difference feature column must be an integer")
        c, max_iter, random_seed = _validate_logistic_parameters(c, max_iter, random_seed)
        self.difference_column = difference_column
        self.mapping = LogisticRegression(C=c, max_iter=max_iter, random_state=random_seed)
        self._is_fitted = False

    def _select(self, features: FeatureMatrix) -> FeatureMatrix:
        matrix = _as_feature_matrix(features)
        if not 0 <= self.difference_column < matrix.shape[1]:
            raise ValueError("difference feature column is outside the feature matrix")
        selected = matrix[:, [self.difference_column]]
        if not np.isfinite(selected).all():
            raise ValueError("selected feature values must be finite; found non-finite value")
        return selected

    def fit(self, features: FeatureMatrix, results: list[str]) -> RatingProbabilityModel:
        eligible, labels = non_tie_rows(features, results)
        selected = self._select(eligible)
        _require_binary_classes(labels)
        self.mapping.fit(selected, labels)
        self._is_fitted = True
        return self

    def predict_r_home(self, features: FeatureMatrix) -> ProbabilityVector:
        if not self._is_fitted:
            raise RuntimeError("model must be fit before prediction")
        return np.asarray(
            self.mapping.predict_proba(self._select(features))[:, 1], dtype=np.float64
        )


def rating_baselines_v1(policy: ModelPolicy) -> dict[str, RatingProbabilityModel]:
    columns = (("elo", "elo_diff", 2), ("colley", "colley_diff", 5), ("massey", "massey_diff", 8))
    for _, feature_name, column in columns:
        if FEATURE_NAMES_V1[column] != feature_name:
            raise RuntimeError(
                "ordered V1 rating difference columns do not match the model contract"
            )
    return {
        lane: RatingProbabilityModel(
            column,
            c=policy.logistic.c,
            max_iter=policy.logistic.max_iter,
            random_seed=policy.random_seed,
        )
        for lane, _, column in columns
    }
