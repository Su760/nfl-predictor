from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class FeaturePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    schema_version: str
    ewma_halflife_games: float = Field(gt=0)
    prior_effective_games: float = Field(gt=0)
    offseason_regression_to_mean: float = Field(ge=0, le=1)
    epa_ridge_alpha: float = Field(gt=0)
    minimum_qb_attempts: int = Field(gt=0)
    short_week_max_rest_days: int = Field(gt=0)
    offseason_rest_cap_days: int = Field(gt=0)
    elo_initial: float = Field(gt=0, allow_inf_nan=False)
    elo_home_field_points: float = Field(ge=0, allow_inf_nan=False)
    elo_k_factor: float = Field(gt=0, allow_inf_nan=False)
    elo_offseason_retention: float = Field(ge=0, le=1, allow_inf_nan=False)
    elo_logistic_scale: float = Field(gt=0, allow_inf_nan=False)
    elo_mov_denominator: float = Field(gt=0, allow_inf_nan=False)
    elo_mov_rating_scale: float = Field(ge=0, allow_inf_nan=False)
    massey_home_field_points: float = Field(ge=0, allow_inf_nan=False)


def load_feature_policy(path: str | Path) -> FeaturePolicy:
    with Path(path).open("rb") as handle:
        return FeaturePolicy.model_validate(tomllib.load(handle))
