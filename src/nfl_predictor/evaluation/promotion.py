from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Self, TypeAlias

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.evaluation.bootstrap import GameBundle, moving_week_bootstrap

FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0.0)]
PositiveFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.0)]
NonBlank = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
ModelLane: TypeAlias = Literal["football_only", "market_blend"]
EvidenceBasis: TypeAlias = Literal["prospective", "historical"]


class EvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: NonBlank
    bootstrap_replicates: int = Field(strict=True, ge=1)
    bootstrap_seed: int = Field(strict=True, ge=0, lt=2**32)
    moving_block_weeks: int = Field(strict=True, ge=1)
    calibration_regression_max_iter: int = Field(strict=True, ge=1)
    log_loss_epsilon: Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.0, lt=0.5)]
    minimum_promotion_seasons: int = Field(strict=True, ge=1)
    minimum_systematic_staking_seasons: int = Field(strict=True, ge=1)
    minimum_common_games: int = Field(strict=True, ge=1)
    maximum_brier_degradation: NonNegativeFiniteFloat
    minimum_calibration_intercept: FiniteFloat
    maximum_calibration_intercept: FiniteFloat
    minimum_calibration_slope: PositiveFiniteFloat
    maximum_calibration_slope: PositiveFiniteFloat

    @model_validator(mode="after")
    def require_ordered_guardrails(self) -> Self:
        if self.minimum_calibration_intercept > self.maximum_calibration_intercept:
            raise ValueError("minimum calibration intercept exceeds maximum intercept")
        if self.minimum_calibration_slope > self.maximum_calibration_slope:
            raise ValueError("minimum calibration slope exceeds maximum slope")
        return self


def load_evaluation_policy(path: Path) -> EvaluationPolicy:
    with path.open("rb") as handle:
        return EvaluationPolicy.model_validate(tomllib.load(handle))


@dataclass(frozen=True)
class ModelEvaluation:
    event_ids: tuple[str, ...]
    estimator_training_event_ids: tuple[str, ...]
    calibration_event_ids: tuple[str, ...]
    origin: Origin
    model_lane: ModelLane
    confirmatory: bool
    evidence_grade: ProvenanceGrade
    evidence_basis: EvidenceBasis
    season_by_event: Mapping[str, int]
    week_by_event: Mapping[str, int]
    multinomial_log_loss_by_event: Mapping[str, float]
    multiclass_brier_by_event: Mapping[str, float]
    calibration_intercept: float
    calibration_slope: float

    def __post_init__(self) -> None:
        if not self.event_ids:
            raise ValueError("model evaluation requires at least one event")
        if any(
            not isinstance(event_id, str) or not event_id.strip() for event_id in self.event_ids
        ):
            raise ValueError("evaluation event IDs must be non-blank strings")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("evaluation event IDs must be unique")
        training_ids = self._validated_lineage_ids(
            self.estimator_training_event_ids, "estimator-training"
        )
        calibration_ids = self._validated_lineage_ids(self.calibration_event_ids, "calibration")
        evaluation_ids = set(self.event_ids)
        if set(training_ids) & evaluation_ids:
            raise ValueError(
                "estimator-training lineage must be disjoint from evaluation event IDs"
            )
        if set(calibration_ids) & evaluation_ids:
            raise ValueError("calibration lineage must be disjoint from evaluation event IDs")
        if set(training_ids) & set(calibration_ids):
            raise ValueError("training and calibration lineage must be disjoint")
        if not isinstance(self.origin, Origin):
            raise TypeError("evaluation origin must be an Origin")
        if self.model_lane not in {"football_only", "market_blend"}:
            raise ValueError("evaluation model lane is invalid")
        if not isinstance(self.confirmatory, bool):
            raise TypeError("evaluation confirmatory status must be boolean")
        if not isinstance(self.evidence_grade, ProvenanceGrade):
            raise TypeError("evaluation evidence grade must be a ProvenanceGrade")
        if self.evidence_basis not in {"prospective", "historical"}:
            raise ValueError("evaluation evidence basis is invalid")
        identifiers = set(self.event_ids)
        mappings: tuple[tuple[str, Mapping[str, object]], ...] = (
            ("season", self.season_by_event),
            ("week", self.week_by_event),
            ("multinomial log loss", self.multinomial_log_loss_by_event),
            ("multiclass Brier", self.multiclass_brier_by_event),
        )
        for name, values in mappings:
            if set(values) != identifiers:
                raise ValueError(f"{name} rows must exactly align with evaluation event IDs")
        seasons = dict(self.season_by_event)
        weeks = dict(self.week_by_event)
        losses = dict(self.multinomial_log_loss_by_event)
        brier_scores = dict(self.multiclass_brier_by_event)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in seasons.values()
        ):
            raise ValueError("evaluation seasons must be positive integers")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in weeks.values()
        ):
            raise ValueError("evaluation weeks must be positive integers")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
            for value in losses.values()
        ):
            raise ValueError("event log losses must be finite and non-negative")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in brier_scores.values()
        ):
            raise ValueError("event multiclass Brier contributions must be finite")
        if any(not 0.0 <= float(value) <= 2.0 for value in brier_scores.values()):
            raise ValueError("event multiclass Brier contributions must be inside [0, 2]")
        metrics = (self.calibration_intercept, self.calibration_slope)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in metrics
        ):
            raise ValueError("evaluation metrics must be finite")
        object.__setattr__(self, "estimator_training_event_ids", training_ids)
        object.__setattr__(self, "calibration_event_ids", calibration_ids)
        object.__setattr__(self, "season_by_event", MappingProxyType(seasons))
        object.__setattr__(self, "week_by_event", MappingProxyType(weeks))
        object.__setattr__(
            self,
            "multinomial_log_loss_by_event",
            MappingProxyType({key: float(value) for key, value in losses.items()}),
        )
        object.__setattr__(
            self,
            "multiclass_brier_by_event",
            MappingProxyType({key: float(value) for key, value in brier_scores.items()}),
        )

    @staticmethod
    def _validated_lineage_ids(event_ids: tuple[str, ...], name: str) -> tuple[str, ...]:
        if not isinstance(event_ids, tuple):
            raise TypeError(f"{name} lineage IDs must be an immutable tuple")
        if not event_ids:
            raise ValueError(f"{name} lineage IDs must be nonempty")
        if any(not isinstance(event_id, str) or not event_id.strip() for event_id in event_ids):
            raise ValueError(f"{name} lineage IDs must be non-blank strings")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError(f"{name} lineage IDs must be unique")
        return event_ids

    @property
    def multiclass_brier(self) -> float:
        return math.fsum(self.multiclass_brier_by_event.values()) / len(
            self.multiclass_brier_by_event
        )


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reason_codes: tuple[str, ...]
    upper_95_log_loss_delta: float | None


def _eligible_evidence(evaluation: ModelEvaluation) -> bool:
    return (
        evaluation.evidence_grade is ProvenanceGrade.A
        and evaluation.evidence_basis == "prospective"
    ) or (
        evaluation.evidence_grade is ProvenanceGrade.B and evaluation.evidence_basis == "historical"
    )


class PromotionPolicy:
    def __init__(self, policy: EvaluationPolicy) -> None:
        if not isinstance(policy, EvaluationPolicy):
            raise TypeError("promotion policy requires an EvaluationPolicy")
        self.policy = policy

    def evaluate(
        self,
        incumbent: ModelEvaluation,
        challenger: ModelEvaluation,
    ) -> PromotionDecision:
        if not isinstance(incumbent, ModelEvaluation) or not isinstance(
            challenger, ModelEvaluation
        ):
            raise TypeError("promotion inputs must be ModelEvaluation instances")
        reasons: list[str] = []
        if incumbent.origin is not challenger.origin:
            reasons.append("ORIGIN_MISMATCH")
        if incumbent.model_lane != challenger.model_lane:
            reasons.append("MODEL_LANE_MISMATCH")
        if not challenger.confirmatory:
            reasons.append("CHALLENGER_NOT_CONFIRMATORY")
        if not _eligible_evidence(incumbent) or not _eligible_evidence(challenger):
            reasons.append("INELIGIBLE_EVIDENCE")
        elif (
            incumbent.evidence_grade is not challenger.evidence_grade
            or incumbent.evidence_basis != challenger.evidence_basis
        ):
            reasons.append("EVIDENCE_MISMATCH")

        incumbent_ids = set(incumbent.event_ids)
        challenger_ids = set(challenger.event_ids)
        if not incumbent_ids <= challenger_ids:
            reasons.append("COVERAGE_GAP")
        common = sorted(incumbent_ids & challenger_ids)
        if any(
            incumbent.season_by_event[event_id] != challenger.season_by_event[event_id]
            or incumbent.week_by_event[event_id] != challenger.week_by_event[event_id]
            for event_id in common
        ):
            reasons.append("EVENT_METADATA_MISMATCH")
        seasons = {incumbent.season_by_event[event_id] for event_id in common}
        if (
            len(common) < self.policy.minimum_common_games
            or len(seasons) < self.policy.minimum_promotion_seasons
        ):
            reasons.append("INSUFFICIENT_PROSPECTIVE_SAMPLE")

        upper: float | None = None
        if not reasons:
            bundles = [
                GameBundle(
                    season=incumbent.season_by_event[event_id],
                    week=incumbent.week_by_event[event_id],
                    canonical_event_id=event_id,
                    values={
                        "delta": challenger.multinomial_log_loss_by_event[event_id]
                        - incumbent.multinomial_log_loss_by_event[event_id]
                    },
                )
                for event_id in common
            ]
            draws = moving_week_bootstrap(
                bundles,
                statistic=lambda rows: sum(row.values["delta"] for row in rows) / len(rows),
                replicates=self.policy.bootstrap_replicates,
                seed=self.policy.bootstrap_seed,
                block_weeks=self.policy.moving_block_weeks,
            )
            upper = float(np.quantile(draws, 0.95))
            if upper >= 0.0:
                reasons.append("LOG_LOSS_INTERVAL_NOT_SUPERIOR")
        if common:
            incumbent_common_brier = math.fsum(
                incumbent.multiclass_brier_by_event[event_id] for event_id in common
            ) / len(common)
            challenger_common_brier = math.fsum(
                challenger.multiclass_brier_by_event[event_id] for event_id in common
            ) / len(common)
            if (
                challenger_common_brier
                > incumbent_common_brier + self.policy.maximum_brier_degradation
            ):
                reasons.append("BRIER_GUARDRAIL")
        if not (
            self.policy.minimum_calibration_intercept
            <= challenger.calibration_intercept
            <= self.policy.maximum_calibration_intercept
        ):
            reasons.append("CALIBRATION_INTERCEPT_GUARDRAIL")
        if not (
            self.policy.minimum_calibration_slope
            <= challenger.calibration_slope
            <= self.policy.maximum_calibration_slope
        ):
            reasons.append("CALIBRATION_SLOPE_GUARDRAIL")
        return PromotionDecision(not reasons, tuple(reasons), upper)
