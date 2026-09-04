from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from nfl_predictor.features import FEATURE_NAMES_V1
from nfl_predictor.models.baselines import (
    HomePriorModel,
    ModelPolicy,
    RatingProbabilityModel,
    load_model_policy,
    rating_baselines_v1,
)
from nfl_predictor.models.epa_logistic import EpaLogisticModel

N_FEATURES = 42
RESULTS = ["away", "away", "home", "home"]


def _model_policy() -> ModelPolicy:
    path = Path(__file__).parents[2] / "configs" / "model_policy_v1.toml"
    return load_model_policy(path)


def _logistic_settings() -> tuple[float, int, int]:
    policy = _model_policy()
    return policy.logistic.c, policy.logistic.max_iter, policy.random_seed


def _rating_model(column: int = 2) -> RatingProbabilityModel:
    c, max_iter, random_seed = _logistic_settings()
    return RatingProbabilityModel(column, c=c, max_iter=max_iter, random_seed=random_seed)


def _epa_model(indices: tuple[int, ...] = (9, 12, 21)) -> EpaLogisticModel:
    c, max_iter, random_seed = _logistic_settings()
    return EpaLogisticModel(indices, c=c, max_iter=max_iter, random_seed=random_seed)


def _logistic_model_with_parameters(
    family: str, *, c: object, max_iter: object, random_seed: object
) -> object:
    if family == "rating":
        return RatingProbabilityModel(2, c=c, max_iter=max_iter, random_seed=random_seed)  # type: ignore[arg-type]
    return EpaLogisticModel((9,), c=c, max_iter=max_iter, random_seed=random_seed)  # type: ignore[arg-type]


# Catches: counting ties as binary home/away observations biases the home prior.
def test_home_prior_excludes_ties_and_returns_hand_derived_probability() -> None:
    features = np.zeros((4, N_FEATURES), dtype=np.float64)
    model = HomePriorModel().fit(features, ["home", "tie", "away", "home"])

    assert model.n_fit == 3
    assert model.predict_r_home(np.zeros((2, N_FEATURES))).tolist() == pytest.approx(
        [2.0 / 3.0, 2.0 / 3.0]
    )


# Catches: production omits/loosens a Task 7 section or permits policy mutation after loading.
def test_load_model_policy_covers_complete_task7_shape_and_is_frozen() -> None:
    policy = _model_policy()

    assert policy.policy_version == "model-v1"
    assert policy.random_seed == 42
    assert policy.elo.initial == 1505.0
    assert policy.massey.home_field_points == 2.5
    assert policy.epa.ridge_alpha == 10.0
    assert policy.logistic.c == 1.0
    assert policy.logistic.max_iter == 2000
    assert policy.calibration.logit_epsilon == 1e-15
    with pytest.raises(ValidationError, match="frozen"):
        policy.random_seed = 7


# Catches: malformed complete-policy values reach model construction instead of loader validation.
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("random_seed = 42", "random_seed = -1"),
        ("initial = 1505.0", "initial = nan"),
        ("offseason_retention = 0.6666666666666666", "offseason_retention = 1.5"),
        ("ridge_alpha = 10.0", "ridge_alpha = -1.0"),
        ("c = 1.0", "c = nan"),
        ("max_iter = 2000", "max_iter = 0"),
        ("logit_epsilon = 1e-15", "logit_epsilon = 0.5"),
    ],
)
def test_load_model_policy_rejects_invalid_domains(tmp_path: Path, old: str, new: str) -> None:
    source = (Path(__file__).parents[2] / "configs" / "model_policy_v1.toml").read_text(
        encoding="utf-8"
    )
    assert old in source
    policy_path = tmp_path / "invalid.toml"
    policy_path.write_text(source.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_model_policy(policy_path)


# Catches: unrecognized nested policy keys are silently ignored rather than forbidden.
def test_load_model_policy_rejects_extra_nested_fields(tmp_path: Path) -> None:
    source = (Path(__file__).parents[2] / "configs" / "model_policy_v1.toml").read_text(
        encoding="utf-8"
    )
    policy_path = tmp_path / "extra.toml"
    policy_path.write_text(f"{source}\nunknown = 1\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="extra"):
        load_model_policy(policy_path)


# Catches: factory wiring swaps a rating lane or drops configured logistic parameters.
@pytest.mark.parametrize(
    ("lane", "feature_name", "column"),
    [("elo", "elo_diff", 2), ("colley", "colley_diff", 5), ("massey", "massey_diff", 8)],
)
def test_rating_baselines_factory_wires_v1_columns_and_policy(
    lane: str, feature_name: str, column: int
) -> None:
    policy = _model_policy()
    models = rating_baselines_v1(policy)
    assert tuple(models) == ("elo", "colley", "massey")
    assert len({id(model) for model in models.values()}) == 3
    assert FEATURE_NAMES_V1[column] == feature_name

    features = np.zeros((4, N_FEATURES), dtype=np.float64)
    features[:, column] = [-2.0, -1.0, 1.0, 2.0]
    model = models[lane].fit(features, RESULTS)
    probe = np.zeros((1, N_FEATURES), dtype=np.float64)
    probe[0, column] = 1.0
    params = model.mapping.get_params()

    assert model.difference_column == column
    assert params["C"] == 1.0
    assert params["max_iter"] == 2000
    assert params["random_state"] == 42
    assert model.predict_r_home(probe)[0] > 0.5


# Catches: refitting the scaler on prediction rows makes a game's probability batch-dependent.
def test_epa_logistic_preprocessing_is_fit_on_training_rows_only() -> None:
    features = np.zeros((4, N_FEATURES), dtype=np.float64)
    features[:, 9] = [-2.0, -1.0, 1.0, 2.0]
    model = _epa_model((9,)).fit(features, RESULTS)

    one_row = np.zeros((1, N_FEATURES), dtype=np.float64)
    mixed_batch = np.zeros((2, N_FEATURES), dtype=np.float64)
    mixed_batch[1, 9] = 1000.0

    one_probability = model.predict_r_home(one_row)[0]
    batch_probability = model.predict_r_home(mixed_batch)[0]
    assert one_probability == pytest.approx(0.5, abs=1e-12)
    assert batch_probability == pytest.approx(one_probability, abs=1e-15)


# Catches: accepting a vector lets downstream column selection fail with incidental NumPy errors.
@pytest.mark.parametrize(
    "factory",
    [HomePriorModel, _rating_model, _epa_model],
)
def test_models_reject_non_matrix_features(factory: Callable[[], object]) -> None:
    model = factory()
    with pytest.raises(ValueError, match="2-D"):
        model.fit(np.zeros(N_FEATURES), ["home"] * N_FEATURES)  # type: ignore[attr-defined]


# Catches: truncated or extended arrays silently shift the ordered V1 feature contract at fit.
@pytest.mark.parametrize("factory", [HomePriorModel, _rating_model, _epa_model])
@pytest.mark.parametrize("width", [N_FEATURES - 1, N_FEATURES + 1])
def test_models_reject_non_v1_width_at_fit(factory: Callable[[], object], width: int) -> None:
    model = factory()
    with pytest.raises(ValueError, match="exactly 42"):
        model.fit(np.zeros((4, width)), RESULTS)  # type: ignore[attr-defined]


# Catches: prediction accepts a schema-mismatched array after a valid V1 fit.
@pytest.mark.parametrize("factory", [HomePriorModel, _rating_model, _epa_model])
@pytest.mark.parametrize("width", [N_FEATURES - 1, N_FEATURES + 1])
def test_models_reject_non_v1_width_at_prediction(
    factory: Callable[[], object], width: int
) -> None:
    model = factory()
    model.fit(np.zeros((4, N_FEATURES)), RESULTS)  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="exactly 42"):
        model.predict_r_home(np.zeros((1, width)))  # type: ignore[attr-defined]


# Catches: silently truncating mismatched result labels trains on the wrong games.
def test_model_rejects_feature_result_count_mismatch() -> None:
    with pytest.raises(ValueError, match="row count"):
        HomePriorModel().fit(np.zeros((2, N_FEATURES)), ["home"])


# Catches: treating an unknown result spelling as an away win corrupts the target.
def test_model_rejects_unknown_results() -> None:
    with pytest.raises(ValueError, match="home, away, or tie"):
        HomePriorModel().fit(np.zeros((2, N_FEATURES)), ["home", "cancelled"])


# Catches: an all-tie training window creates a NaN prior or an empty sklearn fit.
@pytest.mark.parametrize("factory", [HomePriorModel, _rating_model, _epa_model])
def test_models_reject_training_without_non_tie_games(factory: Callable[[], object]) -> None:
    model = factory()
    with pytest.raises(ValueError, match="non-tie"):
        model.fit(np.zeros((2, N_FEATURES)), ["tie", "tie"])  # type: ignore[attr-defined]


# Catches: allowing one-class data through leaks sklearn's implementation-specific fit error.
@pytest.mark.parametrize("factory", [_rating_model, _epa_model])
def test_logistic_models_require_home_and_away_classes(factory: Callable[[], object]) -> None:
    model = factory()
    with pytest.raises(ValueError, match="both home and away"):
        model.fit(np.zeros((2, N_FEATURES)), ["home", "home"])  # type: ignore[attr-defined]


# Catches: an invalid selected column leaks an incidental NumPy IndexError.
@pytest.mark.parametrize(
    "model",
    [_rating_model(N_FEATURES), _epa_model((-1,)), _epa_model((N_FEATURES,))],
)
def test_logistic_models_reject_malformed_column_indices(model: object) -> None:
    with pytest.raises(ValueError, match="feature column"):
        model.fit(np.zeros((4, N_FEATURES)), RESULTS)  # type: ignore[attr-defined]


# Catches: an empty or duplicate EPA feature selection creates a malformed estimator contract.
@pytest.mark.parametrize("indices", [(), (9, 9)])
def test_epa_model_rejects_empty_or_duplicate_columns(indices: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="feature indices"):
        _epa_model(indices)


# Catches: malformed logistic numeric parameters reach sklearn and fail incidentally during fit.
@pytest.mark.parametrize("family", ["rating", "epa"])
@pytest.mark.parametrize(
    ("c", "max_iter", "random_seed"),
    [
        (0.0, 2000, 42),
        (float("nan"), 2000, 42),
        (True, 2000, 42),
        (1.0, 0, 42),
        (1.0, 1.5, 42),
        (1.0, True, 42),
        (1.0, 2000, -1),
        (1.0, 2000, 2**32),
        (1.0, 2000, 1.5),
        (1.0, 2000, True),
    ],
)
def test_logistic_constructors_reject_invalid_numeric_domains(
    family: str, c: object, max_iter: object, random_seed: object
) -> None:
    with pytest.raises((TypeError, ValueError), match="C|max_iter|random_seed"):
        _logistic_model_with_parameters(family, c=c, max_iter=max_iter, random_seed=random_seed)


# Catches: passing NaN/Inf selected values to sklearn hides a broken FeatureBuilder boundary.
@pytest.mark.parametrize(
    ("model", "column"),
    [(_rating_model(2), 2), (_epa_model((9,)), 9)],
)
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_logistic_models_reject_non_finite_selected_training_values(
    model: object, column: int, bad_value: float
) -> None:
    features = np.zeros((4, N_FEATURES), dtype=np.float64)
    features[0, column] = bad_value
    with pytest.raises(ValueError, match="non-finite"):
        model.fit(features, RESULTS)  # type: ignore[attr-defined]


# Catches: predicting before fit leaks missing-attribute or sklearn-specific exceptions.
@pytest.mark.parametrize("model", [HomePriorModel(), _rating_model(), _epa_model()])
def test_models_reject_prediction_before_fit(model: object) -> None:
    with pytest.raises(RuntimeError, match="fit"):
        model.predict_r_home(np.zeros((1, N_FEATURES)))  # type: ignore[attr-defined]


# Catches: prediction bypasses the same finite-value boundary enforced during fit.
@pytest.mark.parametrize(
    ("model", "column"),
    [(_rating_model(2), 2), (_epa_model((9,)), 9)],
)
def test_logistic_models_reject_non_finite_selected_prediction_values(
    model: object, column: int
) -> None:
    model.fit(np.zeros((4, N_FEATURES)), RESULTS)  # type: ignore[attr-defined]
    features = np.zeros((1, N_FEATURES), dtype=np.float64)
    features[0, column] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        model.predict_r_home(features)  # type: ignore[attr-defined]
