from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline, make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from .base import FeatureMatrix, ProbabilityVector
from .baselines import (
    _as_feature_matrix,
    _require_binary_classes,
    _validate_logistic_parameters,
    non_tie_rows,
)


class EpaLogisticModel:
    def __init__(
        self,
        feature_indices: tuple[int, ...],
        c: float,
        max_iter: int,
        random_seed: int,
    ) -> None:
        if (
            not feature_indices
            or len(set(feature_indices)) != len(feature_indices)
            or any(
                isinstance(index, bool) or not isinstance(index, int) for index in feature_indices
            )
        ):
            raise ValueError("EPA feature indices must be non-empty, unique integers")
        c, max_iter, random_seed = _validate_logistic_parameters(c, max_iter, random_seed)
        self.feature_indices = feature_indices
        self.pipeline: Pipeline = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=c, max_iter=max_iter, random_state=random_seed),
        )
        self._is_fitted = False

    def _select(self, features: FeatureMatrix) -> FeatureMatrix:
        matrix = _as_feature_matrix(features)
        if any(index < 0 or index >= matrix.shape[1] for index in self.feature_indices):
            raise ValueError("EPA feature column is outside the feature matrix")
        selected = matrix[:, list(self.feature_indices)]
        if not np.isfinite(selected).all():
            raise ValueError(
                "FeatureBuilder must resolve missing/non-finite values before model fit or prediction"
            )
        return selected

    def fit(self, features: FeatureMatrix, results: list[str]) -> EpaLogisticModel:
        eligible, labels = non_tie_rows(features, results)
        selected = self._select(eligible)
        _require_binary_classes(labels)
        self.pipeline.fit(selected, labels)
        self._is_fitted = True
        return self

    def predict_r_home(self, features: FeatureMatrix) -> ProbabilityVector:
        if not self._is_fitted:
            raise RuntimeError("model must be fit before prediction")
        return np.asarray(
            self.pipeline.predict_proba(self._select(features))[:, 1], dtype=np.float64
        )
