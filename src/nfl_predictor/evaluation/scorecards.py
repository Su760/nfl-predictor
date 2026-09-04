from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, TypeAlias

import numpy as np

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.outcomes import Outcome
from nfl_predictor.evaluation.bootstrap import (
    GameBundle,
    moving_week_bootstrap,
    moving_week_roi_bootstrap,
)
from nfl_predictor.evaluation.metrics import (
    binary_brier,
    binary_log_loss,
    multiclass_brier,
    multinomial_log_loss,
    straight_up_accuracy,
)
from nfl_predictor.evaluation.promotion import EvaluationPolicy

ModelRole: TypeAlias = Literal["champion", "fallback", "challenger"]
OutcomeKey: TypeAlias = tuple[str, int]
Interval: TypeAlias = tuple[float, float] | None
PolicyLineage: TypeAlias = tuple[tuple[str, str], ...]
GroupKey: TypeAlias = tuple[Origin, ModelRole, str, str | None, PolicyLineage, ProvenanceGrade]


@dataclass(frozen=True)
class MarketComparatorInput:
    market_comparator_id: str
    canonical_event_id: str
    origin: Origin
    cutoff_at_utc: datetime
    decision_at_utc: datetime
    quote_ids: tuple[str, ...]
    capture_ids: tuple[str, ...]
    home_probability: Decimal

    def __post_init__(self) -> None:
        if (
            not self.market_comparator_id
            or not self.quote_ids
            or len(self.quote_ids) != len(self.capture_ids)
            or len(set(self.quote_ids)) != len(self.quote_ids)
            or any(not item for item in (*self.quote_ids, *self.capture_ids))
        ):
            raise ValueError(
                "market comparator requires exact comparator, quote, and capture lineage"
            )
        if not Decimal(0) <= self.home_probability <= Decimal(1):
            raise ValueError("market comparator probability must be inside [0, 1]")


@dataclass(frozen=True)
class PredictionScoreInput:
    season: int
    week: int
    prediction: Prediction
    outcome: Outcome | None
    market_comparator: MarketComparatorInput | None = None

    def __post_init__(self) -> None:
        if self.season < 1 or self.week < 1:
            raise ValueError("score input season and week must be positive")
        if (
            self.outcome is not None
            and self.prediction.canonical_event_id != self.outcome.canonical_event_id
        ):
            raise ValueError("prediction and outcome event IDs must match")
        comparator = self.market_comparator
        if comparator is not None and (
            comparator.canonical_event_id != self.prediction.canonical_event_id
            or comparator.origin != self.prediction.origin
            or comparator.decision_at_utc != self.prediction.decision_at_utc
            or comparator.cutoff_at_utc != self.prediction.decision_at_utc
        ):
            raise ValueError("market comparator event/origin/decision lineage mismatch")


@dataclass(frozen=True)
class ForecastObligationInput:
    canonical_event_id: str
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    evidence_grade: ProvenanceGrade


@dataclass(frozen=True)
class ReturnScoreInput:
    season: int
    week: int
    canonical_event_id: str
    outcome_id: str
    outcome_version: int
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    profit: Decimal | None
    stake: Decimal
    evidence_grade: ProvenanceGrade
    candidate_id: str | None = None
    ticket_id: str | None = None
    settlement_id: str = ""
    settlement_version: int = 1
    supersedes_settlement_id: str | None = None
    operator_evidence_id: str = ""
    settlement_result: Literal["win", "loss", "push", "void", "unresolved"] = "win"

    def __post_init__(self) -> None:
        if self.season < 1 or self.week < 1 or self.outcome_version < 1:
            raise ValueError("return input season, week, and outcome version must be positive")
        if self.stake <= 0:
            raise ValueError("return score input requires positive stake")
        if tuple(sorted(self.policy_versions)) != self.policy_versions:
            raise ValueError("return policy lineage must be sorted")
        if (self.candidate_id is None) == (self.ticket_id is None):
            raise ValueError("return input must link exactly one candidate or ticket")
        if not self.settlement_id or not self.operator_evidence_id:
            raise ValueError("return input requires settlement and operator evidence lineage")
        if (self.settlement_version == 1) != (self.supersedes_settlement_id is None):
            raise ValueError("corrected return input must retain superseded settlement lineage")
        if (self.settlement_result == "unresolved") != (self.profit is None):
            raise ValueError("only unresolved return inputs omit profit")

    @property
    def position_id(self) -> str:
        return self.candidate_id if self.candidate_id is not None else str(self.ticket_id)


@dataclass(frozen=True)
class ReturnObligationInput:
    season: int
    week: int
    position_id: str
    canonical_event_id: str
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    evidence_grade: ProvenanceGrade
    candidate_id: str | None = None
    ticket_id: str | None = None

    def __post_init__(self) -> None:
        if self.season < 1 or self.week < 1 or not self.position_id or not self.canonical_event_id:
            raise ValueError("return obligation requires positive period and position/event IDs")
        if (self.candidate_id is None) == (self.ticket_id is None):
            raise ValueError("return obligation must link exactly one candidate or ticket")
        if (self.candidate_id or self.ticket_id) != self.position_id:
            raise ValueError("return obligation position ID must match linked candidate or ticket")


@dataclass(frozen=True)
class ProbabilityScorecard:
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    event_ids: tuple[str, ...]
    outcome_keys: tuple[OutcomeKey, ...]
    metric_event_ids: tuple[str, ...]
    coverage: Decimal
    n_missing: int
    n_all_settled: int
    n_ties: int
    n_unresolved: int
    multinomial_log_loss: float | None
    multiclass_brier: float | None
    evidence_grade: ProvenanceGrade
    log_loss_interval: Interval = None
    brier_interval: Interval = None
    interval_unavailable_reason: str | None = None


@dataclass(frozen=True)
class WinnerScorecard:
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    event_ids: tuple[str, ...]
    outcome_keys: tuple[OutcomeKey, ...]
    metric_event_ids: tuple[str, ...]
    coverage: Decimal
    n_missing: int
    n_non_ties: int
    n_ties: int
    n_unresolved: int
    straight_up_accuracy: float | None
    evidence_grade: ProvenanceGrade
    accuracy_interval: Interval = None
    interval_unavailable_reason: str | None = None


@dataclass(frozen=True)
class MarketScorecard:
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    event_ids: tuple[str, ...]
    outcome_keys: tuple[OutcomeKey, ...]
    metric_event_ids: tuple[str, ...]
    comparator_ids: tuple[str, ...]
    quote_ids: tuple[str, ...]
    capture_ids: tuple[str, ...]
    quote_id_groups: tuple[tuple[str, ...], ...]
    capture_id_groups: tuple[tuple[str, ...], ...]
    coverage: Decimal
    n_missing: int
    n_comparators: int
    n_ties: int
    n_unresolved: int
    binary_log_loss: float | None
    binary_brier: float | None
    model_binary_log_loss: float | None
    model_binary_brier: float | None
    evidence_grade: ProvenanceGrade
    log_loss_interval: Interval = None
    brier_interval: Interval = None
    paired_log_loss_difference_interval: Interval = None
    paired_brier_difference_interval: Interval = None
    interval_unavailable_reason: str | None = None


@dataclass(frozen=True)
class PairwiseScorecard:
    origin: Origin
    evidence_grade: ProvenanceGrade
    champion_artifact_id: str
    challenger_artifact_id: str
    champion_calibrator_artifact_id: str | None
    challenger_calibrator_artifact_id: str | None
    champion_policy_versions: PolicyLineage
    challenger_policy_versions: PolicyLineage
    event_ids: tuple[str, ...]
    outcome_keys: tuple[OutcomeKey, ...]
    evaluation_policy_version: str
    bootstrap_seed: int
    moving_block_weeks: int
    interval_confidence: Decimal
    log_loss_difference_interval: Interval
    brier_difference_interval: Interval
    accuracy_difference_interval: Interval
    interval_unavailable_reason: str | None = None


@dataclass(frozen=True)
class ReturnScorecard:
    origin: Origin
    model_role: ModelRole
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: PolicyLineage
    event_ids: tuple[str, ...]
    position_ids: tuple[str, ...]
    outcome_keys: tuple[OutcomeKey, ...]
    candidate_ids: tuple[str, ...]
    ticket_ids: tuple[str, ...]
    settlement_keys: tuple[tuple[str, int, str | None], ...]
    operator_evidence_ids: tuple[str, ...]
    coverage: Decimal
    n_missing: int
    n_positions: int
    n_unresolved: int
    n_void: int
    n_push: int
    total_profit: Decimal
    total_stake: Decimal
    roi: Decimal | None
    evidence_grade: ProvenanceGrade
    roi_interval: Interval = None
    zero_stake_bootstrap_replicates: int = 0
    interval_unavailable_reason: str | None = None


@dataclass(frozen=True)
class WeeklyScorecards:
    probability: tuple[ProbabilityScorecard, ...]
    winner: tuple[WinnerScorecard, ...]
    market: tuple[MarketScorecard, ...]
    pairwise: tuple[PairwiseScorecard, ...]
    displayed_price: tuple[ReturnScorecard, ...]
    actual_tickets: tuple[ReturnScorecard, ...]


class ScorecardRepository(Protocol):
    def prediction_score_inputs(
        self, season: int, through_week: int
    ) -> tuple[PredictionScoreInput, ...]: ...
    def forecast_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ForecastObligationInput, ...]: ...
    def market_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ForecastObligationInput, ...]: ...
    def displayed_return_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnScoreInput, ...]: ...
    def displayed_return_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnObligationInput, ...]: ...
    def actual_ticket_return_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnScoreInput, ...]: ...
    def actual_ticket_return_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnObligationInput, ...]: ...


@dataclass(frozen=True)
class _BootstrapSpec:
    replicates: int
    seed: int
    block_weeks: int
    low: float
    high: float
    policy_version: str
    confidence: Decimal


def _prediction_key(row: PredictionScoreInput) -> GroupKey:
    p = row.prediction
    return (
        p.origin,
        p.model_role,
        p.model_artifact_id,
        p.calibrator_artifact_id,
        tuple(sorted(p.policy_versions.items())),
        p.provenance_grade,
    )


def _return_key(row: ReturnScoreInput | ReturnObligationInput) -> GroupKey:
    return (
        row.origin,
        row.model_role,
        row.artifact_id,
        row.calibrator_artifact_id,
        row.policy_versions,
        row.evidence_grade,
    )


def _obligation_key(row: ForecastObligationInput) -> GroupKey:
    return (
        row.origin,
        row.model_role,
        row.artifact_id,
        row.calibrator_artifact_id,
        row.policy_versions,
        row.evidence_grade,
    )


def _coverage(n: int, denominator: int) -> Decimal:
    return Decimal(n) / Decimal(denominator) if denominator else Decimal(0)


def _interval(
    bundles: list[GameBundle], name: str, spec: _BootstrapSpec
) -> tuple[Interval, str | None]:
    if not bundles:
        return None, "NO_ELIGIBLE_ROWS"
    try:
        draws = moving_week_bootstrap(
            bundles,
            lambda sample: sum(x.values[name] for x in sample) / len(sample),
            spec.replicates,
            spec.seed,
            spec.block_weeks,
        )
    except ValueError as error:
        if "weeks must be contiguous" in str(error):
            return None, "NON_CONTIGUOUS_WEEKS"
        raise
    return (float(np.quantile(draws, spec.low)), float(np.quantile(draws, spec.high))), None


def _multiclass_bundles(rows: list[PredictionScoreInput]) -> list[GameBundle]:
    result: list[GameBundle] = []
    for row in rows:
        if row.outcome is None:
            raise ValueError("metric bundle requires a resolved outcome record")
        p = (
            float(row.prediction.p_home),
            float(row.prediction.p_away),
            float(row.prediction.p_tie),
        )
        values = {
            "log": multinomial_log_loss([p], [row.outcome.result]),
            "brier": multiclass_brier([p], [row.outcome.result]),
        }
        if row.outcome.result != "tie":
            values["accuracy"] = straight_up_accuracy([p], [row.outcome.result])
        result.append(
            GameBundle(
                row.season,
                row.week,
                row.prediction.canonical_event_id,
                values,
            )
        )
    return result


def _mean(bundles: list[GameBundle], name: str) -> float | None:
    return sum(row.values[name] for row in bundles) / len(bundles) if bundles else None


def _prediction_cards(
    rows: tuple[PredictionScoreInput, ...],
    forecasts: tuple[ForecastObligationInput, ...],
    markets_in: tuple[ForecastObligationInput, ...],
    spec: _BootstrapSpec,
) -> tuple[
    tuple[ProbabilityScorecard, ...],
    tuple[WinnerScorecard, ...],
    tuple[MarketScorecard, ...],
    tuple[PairwiseScorecard, ...],
]:
    grouped: dict[GroupKey, list[PredictionScoreInput]] = defaultdict(list)
    forecasts_by_key: dict[GroupKey, set[str]] = defaultdict(set)
    markets_by_key: dict[GroupKey, set[str]] = defaultdict(set)
    for row in rows:
        grouped[_prediction_key(row)].append(row)
    for forecast_obligation in forecasts:
        forecasts_by_key[_obligation_key(forecast_obligation)].add(
            forecast_obligation.canonical_event_id
        )
    for market_obligation in markets_in:
        markets_by_key[_obligation_key(market_obligation)].add(market_obligation.canonical_event_id)
    probabilities: list[ProbabilityScorecard] = []
    winners: list[WinnerScorecard] = []
    for key in sorted(forecasts_by_key, key=str):
        origin, role, artifact, calibrator, policies, grade = key
        events = tuple(sorted(forecasts_by_key[key]))
        group = sorted(grouped.get(key, []), key=lambda x: x.prediction.canonical_event_id)
        row_events = [x.prediction.canonical_event_id for x in group]
        if len(row_events) != len(set(row_events)) or not set(row_events) <= set(events):
            raise ValueError("prediction rows must uniquely belong to the obligation cohort")
        settled = [x for x in group if x.outcome is not None and x.outcome.game_status == "final"]
        non_ties = [x for x in settled if x.outcome is not None and x.outcome.result != "tie"]
        p_bundles = _multiclass_bundles(settled)
        w_bundles = _multiclass_bundles(non_ties)
        log_i, log_r = _interval(p_bundles, "log", spec)
        brier_i, brier_r = _interval(p_bundles, "brier", spec)
        acc_i, acc_r = _interval(w_bundles, "accuracy", spec)
        outcome_keys = tuple(
            (x.outcome.outcome_id, x.outcome.outcome_version)
            for x in group
            if x.outcome is not None
        )
        common = (origin, role, artifact, calibrator, policies, events, outcome_keys)
        missing = len(set(events) - set(row_events))
        coverage = _coverage(len(group), len(events))
        unresolved_count = sum(x.outcome is None or x.outcome.game_status != "final" for x in group)
        probabilities.append(
            ProbabilityScorecard(
                *common,
                tuple(sorted(x.prediction.canonical_event_id for x in settled)),
                coverage,
                missing,
                len(settled),
                sum(x.outcome is not None and x.outcome.result == "tie" for x in settled),
                unresolved_count,
                _mean(p_bundles, "log"),
                _mean(p_bundles, "brier"),
                grade,
                log_i,
                brier_i,
                log_r or brier_r,
            )
        )
        winners.append(
            WinnerScorecard(
                *common,
                tuple(sorted(x.prediction.canonical_event_id for x in non_ties)),
                coverage,
                missing,
                len(non_ties),
                len(settled) - len(non_ties),
                unresolved_count,
                _mean(w_bundles, "accuracy"),
                grade,
                acc_i,
                acc_r,
            )
        )
    market_cards: list[MarketScorecard] = []
    for key in sorted(markets_by_key, key=str):
        origin, role, artifact, calibrator, policies, grade = key
        events = tuple(sorted(markets_by_key[key]))
        group = sorted(grouped.get(key, []), key=lambda x: x.prediction.canonical_event_id)
        if not {x.prediction.canonical_event_id for x in group} <= set(events):
            raise ValueError("market row outside obligation cohort")
        ties = [
            x
            for x in group
            if x.outcome is not None
            and x.outcome.game_status == "final"
            and x.outcome.result == "tie"
        ]
        unresolved_rows = [
            x for x in group if x.outcome is None or x.outcome.game_status != "final"
        ]
        eligible = [
            x
            for x in group
            if x.outcome is not None
            and x.outcome.game_status == "final"
            and x.outcome.result != "tie"
            and x.market_comparator is not None
        ]
        bundles: list[GameBundle] = []
        for row in eligible:
            comparator = row.market_comparator
            outcome = row.outcome
            if comparator is None or outcome is None:
                raise ValueError("eligible market row is missing comparator/outcome")
            label = int(outcome.result == "home")
            total = row.prediction.p_home + row.prediction.p_away
            if total <= 0:
                raise ValueError("model home/away probability is undefined")
            mp = float(row.prediction.p_home / total)
            cp = float(comparator.home_probability)
            ml = binary_log_loss([mp], [label])
            cl = binary_log_loss([cp], [label])
            mb = binary_brier([mp], [label])
            cb = binary_brier([cp], [label])
            bundles.append(
                GameBundle(
                    row.season,
                    row.week,
                    row.prediction.canonical_event_id,
                    {
                        "log": cl,
                        "brier": cb,
                        "model_log": ml,
                        "model_brier": mb,
                        "log_diff": ml - cl,
                        "brier_diff": mb - cb,
                    },
                )
            )
        log_i, log_r = _interval(bundles, "log", spec)
        brier_i, brier_r = _interval(bundles, "brier", spec)
        pl_i, pl_r = _interval(bundles, "log_diff", spec)
        pb_i, pb_r = _interval(bundles, "brier_diff", spec)
        excluded = {x.prediction.canonical_event_id for x in ties + unresolved_rows + eligible}
        market_cards.append(
            MarketScorecard(
                origin,
                role,
                artifact,
                calibrator,
                policies,
                events,
                tuple(
                    (x.outcome.outcome_id, x.outcome.outcome_version)
                    for x in group
                    if x.outcome is not None
                ),
                tuple(sorted(x.prediction.canonical_event_id for x in eligible)),
                tuple(
                    x.market_comparator.market_comparator_id
                    for x in eligible
                    if x.market_comparator
                ),
                tuple(
                    quote_id
                    for x in eligible
                    if x.market_comparator
                    for quote_id in x.market_comparator.quote_ids
                ),
                tuple(
                    capture_id
                    for x in eligible
                    if x.market_comparator
                    for capture_id in x.market_comparator.capture_ids
                ),
                tuple(x.market_comparator.quote_ids for x in eligible if x.market_comparator),
                tuple(x.market_comparator.capture_ids for x in eligible if x.market_comparator),
                _coverage(len(eligible), len(events)),
                len(set(events) - excluded),
                len(eligible),
                len(ties),
                len(unresolved_rows),
                _mean(bundles, "log"),
                _mean(bundles, "brier"),
                _mean(bundles, "model_log"),
                _mean(bundles, "model_brier"),
                grade,
                log_i,
                brier_i,
                pl_i,
                pb_i,
                log_r or brier_r or pl_r or pb_r,
            )
        )
    return tuple(probabilities), tuple(winners), tuple(market_cards), _pairwise(grouped, spec)


def _pairwise(
    grouped: Mapping[GroupKey, list[PredictionScoreInput]], spec: _BootstrapSpec
) -> tuple[PairwiseScorecard, ...]:
    cards: list[PairwiseScorecard] = []
    for champion_key in sorted((k for k in grouped if k[1] == "champion"), key=str):
        for challenger_key in sorted((k for k in grouped if k[1] == "challenger"), key=str):
            if (champion_key[0], champion_key[5]) != (challenger_key[0], challenger_key[5]):
                continue
            champion = {
                x.prediction.canonical_event_id: x
                for x in grouped[champion_key]
                if x.outcome is not None and x.outcome.game_status == "final"
            }
            challenger = {
                x.prediction.canonical_event_id: x
                for x in grouped[challenger_key]
                if x.outcome is not None and x.outcome.game_status == "final"
            }
            events = tuple(sorted(set(champion) & set(challenger)))
            bundles: list[GameBundle] = []
            winner_bundles: list[GameBundle] = []
            outcomes: list[OutcomeKey] = []
            for event in events:
                c, x = champion[event], challenger[event]
                if c.outcome is None or x.outcome is None:
                    raise ValueError("paired score row is missing outcome")
                if (c.outcome.outcome_id, c.outcome.outcome_version) != (
                    x.outcome.outcome_id,
                    x.outcome.outcome_version,
                ):
                    raise ValueError("pairwise rows require exact outcome lineage")
                cp = (
                    float(c.prediction.p_home),
                    float(c.prediction.p_away),
                    float(c.prediction.p_tie),
                )
                xp = (
                    float(x.prediction.p_home),
                    float(x.prediction.p_away),
                    float(x.prediction.p_tie),
                )
                result = c.outcome.result
                bundles.append(
                    GameBundle(
                        c.season,
                        c.week,
                        event,
                        {
                            "log": multinomial_log_loss([xp], [result])
                            - multinomial_log_loss([cp], [result]),
                            "brier": multiclass_brier([xp], [result])
                            - multiclass_brier([cp], [result]),
                        },
                    )
                )
                if result != "tie":
                    winner_bundles.append(
                        GameBundle(
                            c.season,
                            c.week,
                            event,
                            {
                                "accuracy": straight_up_accuracy([xp], [result])
                                - straight_up_accuracy([cp], [result])
                            },
                        )
                    )
                outcomes.append((c.outcome.outcome_id, c.outcome.outcome_version))
            li, lr = _interval(bundles, "log", spec)
            bi, br = _interval(bundles, "brier", spec)
            ai, ar = _interval(winner_bundles, "accuracy", spec)
            cards.append(
                PairwiseScorecard(
                    champion_key[0],
                    champion_key[5],
                    champion_key[2],
                    challenger_key[2],
                    champion_key[3],
                    challenger_key[3],
                    champion_key[4],
                    challenger_key[4],
                    events,
                    tuple(outcomes),
                    spec.policy_version,
                    spec.seed,
                    spec.block_weeks,
                    spec.confidence,
                    li,
                    bi,
                    ai,
                    lr or br or ar,
                )
            )
    return tuple(cards)


def _return_cards(
    rows: tuple[ReturnScoreInput, ...],
    obligations: tuple[ReturnObligationInput, ...],
    spec: _BootstrapSpec,
) -> tuple[ReturnScorecard, ...]:
    grouped: dict[GroupKey, list[ReturnScoreInput]] = defaultdict(list)
    cohorts: dict[GroupKey, dict[str, ReturnObligationInput]] = defaultdict(dict)
    for row in rows:
        grouped[_return_key(row)].append(row)
    for return_obligation in obligations:
        if return_obligation.position_id in cohorts[_return_key(return_obligation)]:
            raise ValueError("duplicate return obligation position")
        cohorts[_return_key(return_obligation)][return_obligation.position_id] = return_obligation
    cards: list[ReturnScorecard] = []
    for key in sorted(cohorts, key=str):
        origin, role, artifact, calibrator, policies, grade = key
        cohort = cohorts[key]
        group = sorted(grouped.get(key, []), key=lambda x: x.position_id)
        positions = [x.position_id for x in group]
        if len(positions) != len(set(positions)) or not set(positions) <= set(cohort):
            raise ValueError("return rows must uniquely belong to position cohort")
        for row in group:
            obligation = cohort[row.position_id]
            if (row.canonical_event_id, row.season, row.week) != (
                obligation.canonical_event_id,
                obligation.season,
                obligation.week,
            ):
                raise ValueError("return row position lineage mismatch")
        eligible_by_position = {
            x.position_id: x for x in group if x.settlement_result != "unresolved"
        }
        by_event: dict[str, list[ReturnObligationInput]] = defaultdict(list)
        for cohort_obligation in cohort.values():
            by_event[cohort_obligation.canonical_event_id].append(cohort_obligation)
        bundles: list[GameBundle] = []
        for event, event_obligations in sorted(by_event.items()):
            periods = {(x.season, x.week) for x in event_obligations}
            if len(periods) != 1:
                raise ValueError("positions for one event must share a period")
            season, week = next(iter(periods))
            profit = sum(
                (
                    (eligible_by_position[x.position_id].profit or Decimal(0))
                    for x in event_obligations
                    if x.position_id in eligible_by_position
                ),
                Decimal(0),
            )
            stake = sum(
                (
                    eligible_by_position[x.position_id].stake
                    for x in event_obligations
                    if x.position_id in eligible_by_position
                ),
                Decimal(0),
            )
            bundles.append(
                GameBundle(season, week, event, {"profit": float(profit), "stake": float(stake)})
            )
        interval: Interval = None
        zero = 0
        reason = "NO_OBLIGATIONS" if not bundles else None
        if bundles:
            try:
                result = moving_week_roi_bootstrap(
                    bundles, spec.replicates, spec.seed, spec.block_weeks
                )
            except ValueError as error:
                if "weeks must be contiguous" not in str(error):
                    raise
                reason = "NON_CONTIGUOUS_WEEKS"
            else:
                zero = result.zero_stake_replicates
                if zero:
                    reason = "ZERO_STAKE_REPLICATES"
                else:
                    interval = (
                        float(np.quantile(result.draws, spec.low)),
                        float(np.quantile(result.draws, spec.high)),
                    )
        eligible_group = [x for x in group if x.profit is not None]
        profit = sum((x.profit for x in eligible_group if x.profit is not None), Decimal(0))
        stake = sum((x.stake for x in eligible_group), Decimal(0))
        cards.append(
            ReturnScorecard(
                origin,
                role,
                artifact,
                calibrator,
                policies,
                tuple(sorted(by_event)),
                tuple(sorted(cohort)),
                tuple((x.outcome_id, x.outcome_version) for x in group),
                tuple(x.candidate_id for x in group if x.candidate_id),
                tuple(x.ticket_id for x in group if x.ticket_id),
                tuple(
                    (x.settlement_id, x.settlement_version, x.supersedes_settlement_id)
                    for x in group
                ),
                tuple(x.operator_evidence_id for x in group),
                _coverage(len(group), len(cohort)),
                len(cohort) - len(group),
                len(group),
                sum(x.settlement_result == "unresolved" for x in group),
                sum(x.settlement_result == "void" for x in group),
                sum(x.settlement_result == "push" for x in group),
                profit,
                stake,
                profit / stake if stake else None,
                grade,
                interval,
                zero,
                reason,
            )
        )
    return tuple(cards)


def build_weekly_scorecards(
    ledger: ScorecardRepository,
    season: int,
    through_week: int,
    *,
    evaluation_policy: EvaluationPolicy,
    interval_confidence: Decimal,
) -> WeeklyScorecards:
    if not isinstance(evaluation_policy, EvaluationPolicy):
        raise TypeError("scorecard builder requires an EvaluationPolicy")
    if not isinstance(interval_confidence, Decimal):
        raise TypeError("interval confidence must be a Decimal")
    if not Decimal(0) < interval_confidence < Decimal(1):
        raise ValueError("interval confidence must be inside (0, 1)")
    tail = (Decimal(1) - interval_confidence) / Decimal(2)
    spec = _BootstrapSpec(
        evaluation_policy.bootstrap_replicates,
        evaluation_policy.bootstrap_seed,
        evaluation_policy.moving_block_weeks,
        float(tail),
        float(Decimal(1) - tail),
        evaluation_policy.policy_version,
        interval_confidence,
    )
    prediction, winner, market, pairwise = _prediction_cards(
        ledger.prediction_score_inputs(season, through_week),
        ledger.forecast_obligation_inputs(season, through_week),
        ledger.market_obligation_inputs(season, through_week),
        spec,
    )
    return WeeklyScorecards(
        prediction,
        winner,
        market,
        pairwise,
        _return_cards(
            ledger.displayed_return_inputs(season, through_week),
            ledger.displayed_return_obligation_inputs(season, through_week),
            spec,
        ),
        _return_cards(
            ledger.actual_ticket_return_inputs(season, through_week),
            ledger.actual_ticket_return_obligation_inputs(season, through_week),
            spec,
        ),
    )
