from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from nfl_predictor.contracts.enums import Origin
from nfl_predictor.evaluation.calibration import calibration_intercept_slope
from nfl_predictor.evaluation.metrics import binary_brier, binary_log_loss
from nfl_predictor.evaluation.promotion import EvaluationPolicy
from nfl_predictor.models.baselines import ModelPolicy
from nfl_predictor.models.calibration import CALIBRATOR_REGISTRY


def _probabilities(values: Sequence[float], name: str) -> tuple[float, ...]:
    rows = tuple(values)
    if not rows:
        raise ValueError(f"{name} probabilities cannot be empty")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in rows):
        raise TypeError(f"{name} probabilities must be real numbers")
    probabilities = tuple(float(value) for value in rows)
    if any(not math.isfinite(value) for value in probabilities):
        raise ValueError(f"{name} probabilities must be finite")
    if any(not 0.0 <= value <= 1.0 for value in probabilities):
        raise ValueError(f"{name} probabilities must be inside [0, 1]")
    return probabilities


def _event_ids(values: Sequence[str], name: str) -> tuple[str, ...]:
    identifiers = tuple(values)
    if not identifiers:
        raise ValueError(f"{name} event IDs cannot be empty")
    if any(not isinstance(event_id, str) or not event_id.strip() for event_id in identifiers):
        raise ValueError(f"{name} event IDs must be non-blank strings")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{name} event IDs must be unique")
    return identifiers


@dataclass(frozen=True)
class CalibrationFoldLedger:
    origin: Origin
    target_season: int
    estimator_event_ids: tuple[str, ...]
    calibration_event_ids: tuple[str, ...]
    target_event_ids: tuple[str, ...]
    calibration_r_home: tuple[float, ...]
    target_r_home: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Origin):
            raise TypeError("fold ledger origin must be an Origin")
        if isinstance(self.target_season, bool) or not isinstance(self.target_season, int):
            raise TypeError("fold ledger target season must be an integer")
        if self.target_season < 1:
            raise ValueError("fold ledger target season must be positive")
        estimator = _event_ids(self.estimator_event_ids, "estimator")
        calibration = _event_ids(self.calibration_event_ids, "calibration")
        target = _event_ids(self.target_event_ids, "target")
        if (
            set(estimator) & set(calibration)
            or set(estimator) & set(target)
            or set(calibration) & set(target)
        ):
            raise ValueError("estimator, calibration, and target event IDs must be disjoint")
        calibration_probabilities = _probabilities(self.calibration_r_home, "calibration")
        target_probabilities = _probabilities(self.target_r_home, "target")
        if len(calibration) != len(calibration_probabilities):
            raise ValueError("calibration event IDs and probabilities must be exactly aligned")
        if len(target) != len(target_probabilities):
            raise ValueError("target event IDs and probabilities must be exactly aligned")
        object.__setattr__(self, "estimator_event_ids", estimator)
        object.__setattr__(self, "calibration_event_ids", calibration)
        object.__setattr__(self, "target_event_ids", target)
        object.__setattr__(self, "calibration_r_home", calibration_probabilities)
        object.__setattr__(self, "target_r_home", target_probabilities)

    def calibration_guardrails(
        self,
        probabilities: Sequence[float],
        labels: Sequence[int],
        policy: EvaluationPolicy,
    ) -> bool:
        try:
            intercept, slope = calibration_intercept_slope(
                probabilities,
                labels,
                max_iter=policy.calibration_regression_max_iter,
                epsilon=policy.log_loss_epsilon,
            )
        except ValueError:
            return False
        base_brier = binary_brier(self.target_r_home, labels)
        return (
            binary_brier(probabilities, labels) <= base_brier + policy.maximum_brier_degradation
            and policy.minimum_calibration_intercept
            <= intercept
            <= policy.maximum_calibration_intercept
            and policy.minimum_calibration_slope <= slope <= policy.maximum_calibration_slope
        )


@dataclass(frozen=True)
class CalibrationFamilyScore:
    family: str
    mean_season_log_loss: float
    mean_season_brier: float
    guardrails_pass: bool


class FoldLedgerStore(Protocol):
    model_policy: ModelPolicy
    evaluation_policy: EvaluationPolicy

    def read(self, *, origin: Origin, target_season: int) -> CalibrationFoldLedger: ...


class LabelReader(Protocol):
    def read(self, season: int, event_ids: Sequence[str]) -> Sequence[int]: ...


def select_calibration_family(
    target_season: int,
    origin: Origin | str,
    fold_ledgers: FoldLedgerStore,
    label_reader: LabelReader,
) -> str:
    if isinstance(target_season, bool) or not isinstance(target_season, int):
        raise TypeError("target season must be an integer")
    selected_origin = Origin(origin)
    target_seasons = tuple(range(2019, target_season - 1))
    if not target_seasons:
        raise ValueError("calibration family selection requires completed outer folds")
    scores: list[CalibrationFamilyScore] = []
    for family, calibrator_type in CALIBRATOR_REGISTRY.items():
        season_log_losses: list[float] = []
        season_briers: list[float] = []
        guardrails: list[bool] = []
        for season in target_seasons:
            ledger = fold_ledgers.read(origin=selected_origin, target_season=season)
            if ledger.origin is not selected_origin:
                raise ValueError("fold ledger origin does not match requested origin")
            if ledger.target_season != season:
                raise ValueError("fold ledger target season does not match requested season")
            calibration_labels = tuple(label_reader.read(season - 1, ledger.calibration_event_ids))
            target_labels = tuple(label_reader.read(season, ledger.target_event_ids))
            calibrator = calibrator_type.from_policy(fold_ledgers.model_policy).fit(
                ledger.calibration_r_home,
                calibration_labels,
            )
            calibrated = calibrator.transform(ledger.target_r_home)
            calibrated_values = calibrated.tolist()
            season_log_losses.append(binary_log_loss(calibrated_values, target_labels))
            season_briers.append(binary_brier(calibrated_values, target_labels))
            guardrails.append(
                ledger.calibration_guardrails(
                    calibrated_values,
                    target_labels,
                    fold_ledgers.evaluation_policy,
                )
            )
        scores.append(
            CalibrationFamilyScore(
                family=family,
                mean_season_log_loss=sum(season_log_losses) / len(season_log_losses),
                mean_season_brier=sum(season_briers) / len(season_briers),
                guardrails_pass=all(guardrails),
            )
        )
    eligible = [score for score in scores if score.guardrails_pass]
    if not eligible:
        return "identity"
    eligible.sort(
        key=lambda score: (
            score.mean_season_log_loss,
            score.mean_season_brier,
            score.family,
        )
    )
    best = eligible[0]
    identity = next(score for score in scores if score.family == "identity")
    if (
        identity.guardrails_pass
        and identity.mean_season_log_loss - best.mean_season_log_loss
        < fold_ledgers.model_policy.calibration.selection_tolerance
    ):
        return "identity"
    return best.family
