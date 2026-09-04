from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from nfl_predictor.contracts.enums import Origin, PredictionStatus, ProvenanceGrade
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.outcomes import Outcome
from nfl_predictor.evaluation.promotion import load_evaluation_policy
from nfl_predictor.evaluation.scorecards import (
    ForecastObligationInput,
    MarketComparatorInput,
    PredictionScoreInput,
    ReturnObligationInput,
    ReturnScoreInput,
)
from nfl_predictor.evaluation.scorecards import (
    build_weekly_scorecards as _build_weekly_scorecards,
)
from nfl_predictor.workflows.report import ReportWorkflow

NOW = datetime(2026, 10, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
EVALUATION_POLICY = load_evaluation_policy(ROOT / "configs" / "evaluation_policy_v1.toml")


def build_weekly_scorecards(repository, season: int, through_week: int):
    return _build_weekly_scorecards(
        repository,
        season,
        through_week,
        evaluation_policy=EVALUATION_POLICY,
        interval_confidence=Decimal("0.95"),
    )


@dataclass(frozen=True)
class PositionObligation:
    position_id: str
    canonical_event_id: str
    origin: Origin
    model_role: str
    artifact_id: str
    calibrator_artifact_id: str | None
    policy_versions: tuple[tuple[str, str], ...]
    evidence_grade: ProvenanceGrade
    season: int = 2026
    week: int = 1
    candidate_id: str | None = None
    ticket_id: str | None = None


def comparator(event: str, origin: Origin) -> MarketComparatorInput:
    return MarketComparatorInput(
        market_comparator_id=f"comparator-{event}",
        canonical_event_id=event,
        origin=origin,
        cutoff_at_utc=NOW,
        decision_at_utc=NOW,
        quote_ids=(f"quote-{event}",),
        capture_ids=(f"capture-{event}",),
        home_probability=Decimal("0.52"),
    )


def prediction(event: str, origin: Origin, artifact: str) -> Prediction:
    return Prediction(
        prediction_id=f"prediction-{event}-{origin}",
        origin_run_id=f"run-{event}-{origin}",
        canonical_event_id=event,
        event_version=1,
        origin=origin,
        target_at_utc=NOW,
        decision_at_utc=NOW,
        model_lane="football_only",
        model_role="champion" if artifact == "champion" else "challenger",
        model_artifact_id=artifact,
        calibrator_artifact_id=f"calibrator-{artifact}",
        feature_snapshot_id=f"snapshot-{event}-{origin}",
        market_snapshot_id=None,
        p_home=Decimal("0.55"),
        p_away=Decimal("0.43"),
        p_tie=Decimal("0.02"),
        predicted_winner="home",
        provenance_grade=ProvenanceGrade.A,
        status=PredictionStatus.COMPLETE,
        reason_codes=[],
        code_sha="1" * 40,
        policy_versions={"forecast": "v1", "calibration": "v2"},
        created_at_utc=NOW,
    )


def outcome(event: str, result: str, version: int = 1) -> Outcome:
    scores = {"home": (20, 17), "away": (17, 20), "tie": (20, 20)}
    home, away = scores[result]
    return Outcome(
        outcome_id=f"outcome-{event}",
        outcome_version=version,
        canonical_event_id=event,
        event_version_at_final=1,
        game_status="final",
        home_score=home,
        away_score=away,
        result=result,
        finalized_at_utc=NOW,
        source="nflverse",
        source_version_or_retrieved_at_utc=NOW,
        raw_payload_sha256="a" * 64,
    )


class Repository:
    def prediction_score_inputs(self, season: int, through_week: int):
        return (
            PredictionScoreInput(
                season,
                1,
                prediction("event-1", Origin.T60, "champion"),
                outcome("event-1", "home"),
                comparator("event-1", Origin.T60),
            ),
            PredictionScoreInput(
                season,
                1,
                prediction("event-2", Origin.T60, "champion"),
                outcome("event-2", "tie"),
                None,
            ),
            PredictionScoreInput(
                season,
                2,
                prediction("event-3", Origin.T60, "champion"),
                outcome("event-3", "away"),
                comparator("event-3", Origin.T60),
            ),
            PredictionScoreInput(
                season,
                2,
                prediction("event-3", Origin.T72, "challenger"),
                outcome("event-3", "away"),
                None,
            ),
        )

    def displayed_return_inputs(self, season: int, through_week: int):
        return (
            ReturnScoreInput(
                season=season,
                week=1,
                canonical_event_id="event-1",
                outcome_id="outcome-event-1",
                outcome_version=1,
                origin=Origin.T60,
                model_role="champion",
                artifact_id="champion",
                calibrator_artifact_id="calibrator-champion",
                policy_versions=(("candidate", "v1"),),
                profit=Decimal("1.0"),
                stake=Decimal("1.0"),
                evidence_grade=ProvenanceGrade.A,
                candidate_id="candidate-event-1",
                settlement_id="settlement-candidate-event-1",
                operator_evidence_id="evidence-event-1",
            ),
        )

    def actual_ticket_return_inputs(self, season: int, through_week: int):
        return (
            ReturnScoreInput(
                season=season,
                week=2,
                canonical_event_id="event-3",
                outcome_id="outcome-event-3",
                outcome_version=1,
                origin=Origin.T60,
                model_role="champion",
                artifact_id="champion",
                calibrator_artifact_id="calibrator-champion",
                policy_versions=(("candidate", "v1"),),
                profit=Decimal("9.5"),
                stake=Decimal(10),
                evidence_grade=ProvenanceGrade.A,
                ticket_id="ticket-event-3",
                settlement_id="settlement-ticket-event-3",
                operator_evidence_id="evidence-event-3",
            ),
        )

    def forecast_obligation_inputs(self, season: int, through_week: int):
        return tuple(
            ForecastObligationInput(
                row.prediction.canonical_event_id,
                row.prediction.origin,
                row.prediction.model_role,
                row.prediction.model_artifact_id,
                row.prediction.calibrator_artifact_id,
                tuple(sorted(row.prediction.policy_versions.items())),
                row.prediction.provenance_grade,
            )
            for row in self.prediction_score_inputs(season, through_week)
        )

    def market_obligation_inputs(self, season: int, through_week: int):
        return self.forecast_obligation_inputs(season, through_week)

    def displayed_return_obligation_inputs(self, season: int, through_week: int):
        row = self.displayed_return_inputs(season, through_week)[0]
        return (
            PositionObligation(
                "candidate-event-1",
                row.canonical_event_id,
                row.origin,
                row.model_role,
                row.artifact_id,
                row.calibrator_artifact_id,
                row.policy_versions,
                row.evidence_grade,
                season=season,
                week=row.week,
                candidate_id="candidate-event-1",
            ),
        )

    def actual_ticket_return_obligation_inputs(self, season: int, through_week: int):
        row = self.actual_ticket_return_inputs(season, through_week)[0]
        return (
            PositionObligation(
                "ticket-event-3",
                row.canonical_event_id,
                row.origin,
                row.model_role,
                row.artifact_id,
                row.calibrator_artifact_id,
                row.policy_versions,
                row.evidence_grade,
                season=season,
                week=row.week,
                ticket_id="ticket-event-3",
            ),
        )


class SingleResultRepository(Repository):
    def __init__(self, result: str) -> None:
        self.result = result

    def prediction_score_inputs(self, season: int, through_week: int):
        resolved_outcome = outcome("event-only", "tie")
        if self.result == "unresolved":
            resolved_outcome = resolved_outcome.model_copy(
                update={
                    "game_status": "suspended",
                    "home_score": None,
                    "away_score": None,
                    "result": "unresolved",
                    "finalized_at_utc": None,
                }
            )
        return (
            PredictionScoreInput(
                season,
                1,
                prediction("event-only", Origin.T60, "champion"),
                resolved_outcome,
                None,
            ),
        )

    def displayed_return_inputs(self, season: int, through_week: int):
        return ()

    def actual_ticket_return_inputs(self, season: int, through_week: int):
        return ()

    def displayed_return_obligation_inputs(self, season: int, through_week: int):
        return ()

    def actual_ticket_return_obligation_inputs(self, season: int, through_week: int):
        return ()


def test_scorecard_families_have_separate_denominators_and_lineage() -> None:
    report = build_weekly_scorecards(Repository(), season=2026, through_week=2)
    t60_probability = next(row for row in report.probability if row.origin is Origin.T60)
    t60_winner = next(row for row in report.winner if row.origin is Origin.T60)
    market = next(row for row in report.market if row.origin is Origin.T60)
    displayed = report.displayed_price[0]
    ticket = report.actual_tickets[0]

    assert t60_probability.n_all_settled == 3
    assert t60_winner.n_non_ties == 2
    assert market.n_comparators == 2
    assert market.comparator_ids == ("comparator-event-1", "comparator-event-3")
    assert displayed.n_positions == 1
    assert ticket.n_positions == 1
    assert displayed is not ticket
    assert t60_probability.event_ids == ("event-1", "event-2", "event-3")
    assert t60_probability.outcome_keys == (
        ("outcome-event-1", 1),
        ("outcome-event-2", 1),
        ("outcome-event-3", 1),
    )
    assert t60_probability.policy_versions == (("calibration", "v2"), ("forecast", "v1"))


def test_market_scorecard_retains_per_comparator_quote_and_capture_boundaries() -> None:
    class MultiBookRepository(Repository):
        def prediction_score_inputs(self, season: int, through_week: int):
            rows = list(super().prediction_score_inputs(season, through_week))
            row = rows[2]
            original = row.market_comparator
            assert original is not None
            rows[2] = replace(
                row,
                market_comparator=replace(
                    original,
                    quote_ids=("quote-event-3-a", "quote-event-3-b"),
                    capture_ids=("capture-event-3-a", "capture-event-3-b"),
                ),
            )
            return tuple(rows)

    card = next(
        row
        for row in build_weekly_scorecards(
            MultiBookRepository(), season=2026, through_week=2
        ).market
        if row.origin is Origin.T60
    )

    assert card.quote_id_groups == (
        ("quote-event-1",),
        ("quote-event-3-a", "quote-event-3-b"),
    )
    assert card.capture_id_groups == (
        ("capture-event-1",),
        ("capture-event-3-a", "capture-event-3-b"),
    )


def test_origin_and_challenger_rows_are_never_pooled() -> None:
    report = build_weekly_scorecards(Repository(), season=2026, through_week=2)

    assert {(row.origin, row.model_role, row.artifact_id) for row in report.probability} == {
        (Origin.T60, "champion", "champion"),
        (Origin.T72, "challenger", "challenger"),
    }


def test_scorecards_are_frozen_and_report_coverage_missingness_and_evidence() -> None:
    report = build_weekly_scorecards(Repository(), season=2026, through_week=2)
    probability = next(row for row in report.probability if row.origin is Origin.T60)
    market = next(row for row in report.market if row.origin is Origin.T60)

    assert probability.coverage == Decimal(1)
    assert probability.n_missing == 0
    assert probability.n_ties == 1
    assert probability.n_unresolved == 0
    assert probability.evidence_grade is ProvenanceGrade.A
    assert market.coverage == Decimal("0.6666666666666666666666666667")
    assert market.n_missing == 0
    with pytest.raises(FrozenInstanceError):
        probability.n_all_settled = 0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("result", "settled", "ties", "unresolved"),
    [("tie", 1, 1, 0), ("unresolved", 0, 0, 1)],
)
def test_zero_non_tie_denominators_remain_reportable(
    result: str, settled: int, ties: int, unresolved: int
) -> None:
    report = build_weekly_scorecards(SingleResultRepository(result), 2026, 1)

    assert report.probability[0].n_all_settled == settled
    assert report.probability[0].n_ties == ties
    assert report.probability[0].n_unresolved == unresolved
    assert report.winner[0].n_non_ties == 0
    assert report.winner[0].straight_up_accuracy is None


def test_probability_winner_market_and_return_intervals_are_deterministic() -> None:
    first = build_weekly_scorecards(Repository(), 2026, 2)
    second = build_weekly_scorecards(Repository(), 2026, 2)

    probability = next(row for row in first.probability if row.origin is Origin.T60)
    winner = next(row for row in first.winner if row.origin is Origin.T60)
    market = next(row for row in first.market if row.origin is Origin.T60)
    assert probability.log_loss_interval is not None
    assert probability.brier_interval is not None
    assert winner.accuracy_interval is not None
    assert market.log_loss_interval is not None
    assert market.brier_interval is not None
    assert first.displayed_price[0].roi_interval is not None
    assert first == second


class MixedGradeRepository(Repository):
    def prediction_score_inputs(self, season: int, through_week: int):
        first = super().prediction_score_inputs(season, through_week)[0]
        replay = first.prediction.model_copy(
            update={
                "prediction_id": "prediction-replay",
                "canonical_event_id": "event-replay",
                "provenance_grade": ProvenanceGrade.C,
            }
        )
        return (
            first,
            PredictionScoreInput(
                season,
                1,
                replay,
                outcome("event-replay", "home"),
                None,
            ),
        )


def test_evidence_grades_are_never_pooled() -> None:
    report = build_weekly_scorecards(MixedGradeRepository(), 2026, 1)

    assert {row.evidence_grade for row in report.probability} == {
        ProvenanceGrade.A,
        ProvenanceGrade.C,
    }
    assert len(report.probability) == 2


class MissingForecastRepository(Repository):
    def forecast_obligation_inputs(self, season: int, through_week: int):
        return (
            ForecastObligationInput(
                "event-1",
                Origin.T60,
                "champion",
                "champion",
                "calibrator-champion",
                (("calibration", "v2"), ("forecast", "v1")),
                ProvenanceGrade.A,
            ),
            ForecastObligationInput(
                "event-missed",
                Origin.T60,
                "champion",
                "champion",
                "calibrator-champion",
                (("calibration", "v2"), ("forecast", "v1")),
                ProvenanceGrade.A,
            ),
        )

    def prediction_score_inputs(self, season: int, through_week: int):
        return (super().prediction_score_inputs(season, through_week)[0],)


def test_missing_forecast_is_counted_separately_from_unresolved_outcome() -> None:
    report = build_weekly_scorecards(MissingForecastRepository(), 2026, 1)
    probability = report.probability[0]

    assert probability.coverage == Decimal("0.5")
    assert probability.n_missing == 1
    assert probability.n_unresolved == 0
    assert probability.event_ids == ("event-1", "event-missed")


class CommonEventRepository(Repository):
    def prediction_score_inputs(self, season: int, through_week: int):
        return (
            PredictionScoreInput(
                season,
                1,
                prediction("event-1", Origin.T60, "champion"),
                outcome("event-1", "home"),
            ),
            PredictionScoreInput(
                season,
                1,
                prediction("event-2", Origin.T60, "champion"),
                outcome("event-2", "away"),
            ),
            PredictionScoreInput(
                season,
                1,
                prediction("event-2", Origin.T60, "challenger"),
                outcome("event-2", "away"),
            ),
        )


def test_champion_challenger_metrics_retain_exact_common_event_set() -> None:
    report = build_weekly_scorecards(CommonEventRepository(), 2026, 1)

    assert {row.metric_event_ids for row in report.probability} == {
        ("event-1", "event-2"),
        ("event-2",),
    }
    assert report.pairwise[0].event_ids == ("event-2",)


def test_typed_comparator_rejects_wrong_event_origin_cutoff_or_decision() -> None:
    item = prediction("event-1", Origin.T60, "champion")
    base = comparator("event-1", Origin.T60)
    for changes in (
        {"canonical_event_id": "wrong"},
        {"origin": Origin.T72},
        {"decision_at_utc": NOW.replace(hour=1)},
        {"cutoff_at_utc": NOW.replace(hour=1)},
    ):
        with pytest.raises(ValueError, match="lineage"):
            PredictionScoreInput(
                2026,
                1,
                item,
                outcome("event-1", "home"),
                MarketComparatorInput(**{**base.__dict__, **changes}),
            )


def test_market_comparator_input_retains_all_ordered_quote_and_capture_lineage() -> None:
    item = MarketComparatorInput(
        market_comparator_id="comparator-multi",
        canonical_event_id="event-1",
        origin=Origin.T60,
        cutoff_at_utc=NOW,
        decision_at_utc=NOW,
        quote_ids=("quote-book-a", "quote-book-b"),
        capture_ids=("capture-book-a", "capture-book-b"),
        home_probability=Decimal("0.52"),
    )

    assert item.quote_ids == ("quote-book-a", "quote-book-b")
    assert item.capture_ids == ("capture-book-a", "capture-book-b")


class MultiBookRepository(Repository):
    def prediction_score_inputs(self, season: int, through_week: int):
        rows = list(super().prediction_score_inputs(season, through_week))
        first = rows[0]
        assert first.market_comparator is not None
        rows[0] = replace(
            first,
            market_comparator=replace(
                first.market_comparator,
                quote_ids=("quote-book-a", "quote-book-b"),
                capture_ids=("capture-book-a", "capture-book-b"),
            ),
        )
        return tuple(rows)


def test_market_scorecard_retains_every_book_quote_and_capture_id() -> None:
    report = build_weekly_scorecards(MultiBookRepository(), 2026, 2)
    market = report.market[0]

    assert market.quote_ids[:2] == ("quote-book-a", "quote-book-b")
    assert market.capture_ids[:2] == ("capture-book-a", "capture-book-b")


class EmptyCohortRepository(SingleResultRepository):
    def displayed_return_obligation_inputs(self, season: int, through_week: int):
        return (
            ReturnObligationInput(
                season=season,
                week=1,
                position_id="candidate-event-only",
                canonical_event_id="event-only",
                origin=Origin.T60,
                model_role="champion",
                artifact_id="champion",
                calibrator_artifact_id="calibrator-champion",
                policy_versions=(("candidate", "v1"),),
                evidence_grade=ProvenanceGrade.A,
                candidate_id="candidate-event-only",
            ),
        )


def test_zero_comparator_and_zero_return_groups_remain_explicit() -> None:
    report = build_weekly_scorecards(EmptyCohortRepository("unresolved"), 2026, 1)

    assert len(report.market) == 1
    assert report.market[0].coverage == 0
    assert report.market[0].n_comparators == 0
    assert report.market[0].binary_log_loss is None
    assert len(report.displayed_price) == 1
    assert report.displayed_price[0].coverage == 0
    assert report.displayed_price[0].n_missing == 1
    assert report.displayed_price[0].n_positions == 0
    assert report.displayed_price[0].roi is None
    assert report.displayed_price[0].roi_interval is None
    assert (
        report.displayed_price[0].zero_stake_bootstrap_replicates
        == EVALUATION_POLICY.bootstrap_replicates
    )


def test_return_scorecards_retain_exact_settlement_and_operator_lineage() -> None:
    report = build_weekly_scorecards(Repository(), 2026, 2)
    displayed = report.displayed_price[0]
    ticket = report.actual_tickets[0]

    assert displayed.candidate_ids == ("candidate-event-1",)
    assert displayed.ticket_ids == ()
    assert displayed.settlement_keys == (("settlement-candidate-event-1", 1, None),)
    assert displayed.operator_evidence_ids == ("evidence-event-1",)
    assert ticket.ticket_ids == ("ticket-event-3",)
    assert ticket.candidate_ids == ()


def test_scorecard_builder_requires_typed_policy_and_all_obligation_methods() -> None:
    signature = inspect.signature(_build_weekly_scorecards)
    assert signature.parameters["evaluation_policy"].default is inspect.Parameter.empty
    assert signature.parameters["interval_confidence"].default is inspect.Parameter.empty

    class MissingCohorts:
        def prediction_score_inputs(self, season: int, through_week: int):
            return ()

        def displayed_return_inputs(self, season: int, through_week: int):
            return ()

        def actual_ticket_return_inputs(self, season: int, through_week: int):
            return ()

    with pytest.raises((AttributeError, TypeError), match="obligation"):
        build_weekly_scorecards(MissingCohorts(), 2026, 1)


def test_market_missingness_distinguishes_tie_and_unresolved_outcomes() -> None:
    tied = build_weekly_scorecards(SingleResultRepository("tie"), 2026, 1).market[0]
    unresolved = build_weekly_scorecards(SingleResultRepository("unresolved"), 2026, 1).market[0]

    assert (tied.n_missing, tied.n_ties, tied.n_unresolved) == (0, 1, 0)
    assert (unresolved.n_missing, unresolved.n_ties, unresolved.n_unresolved) == (0, 0, 1)


class PairwiseRepository(Repository):
    def prediction_score_inputs(self, season: int, through_week: int):
        rows = []
        for event_id in ("event-1", "event-2", "event-3"):
            rows.append(
                PredictionScoreInput(
                    season,
                    1,
                    prediction(event_id, Origin.T60, "champion"),
                    outcome(event_id, "home"),
                )
            )
        for artifact, event_ids in (
            ("challenger-a", ("event-2", "event-3")),
            ("challenger-b", ("event-1", "event-3")),
        ):
            for event_id in event_ids:
                rows.append(
                    PredictionScoreInput(
                        season,
                        1,
                        prediction(event_id, Origin.T60, artifact),
                        outcome(event_id, "home"),
                    )
                )
        return tuple(rows)


def test_pairwise_cohorts_use_each_challengers_exact_intersection() -> None:
    report = build_weekly_scorecards(PairwiseRepository(), 2026, 1)

    assert {(row.challenger_artifact_id, row.event_ids) for row in report.pairwise} == {
        ("challenger-a", ("event-2", "event-3")),
        ("challenger-b", ("event-1", "event-3")),
    }
    assert all(row.log_loss_difference_interval is not None for row in report.pairwise)


class MultiPositionRepository(Repository):
    def displayed_return_inputs(self, season: int, through_week: int):
        base = super().displayed_return_inputs(season, through_week)[0]
        return (
            base,
            replace(
                base,
                candidate_id="candidate-event-1-second",
                settlement_id="settlement-candidate-event-1-second",
                operator_evidence_id="evidence-event-1-second",
                profit=Decimal(-1),
            ),
        )

    def displayed_return_obligation_inputs(self, season: int, through_week: int):
        base = super().displayed_return_obligation_inputs(season, through_week)[0]
        return (
            base,
            replace(
                base,
                position_id="candidate-event-1-second",
                candidate_id="candidate-event-1-second",
            ),
            replace(
                base,
                position_id="candidate-event-missing",
                canonical_event_id="event-missing",
                week=2,
                candidate_id="candidate-event-missing",
            ),
        )


def test_return_coverage_is_position_level_and_bootstrap_aggregates_one_event_bundle() -> None:
    card = build_weekly_scorecards(MultiPositionRepository(), 2026, 1).displayed_price[0]

    assert card.coverage == Decimal("0.6666666666666666666666666667")
    assert card.n_positions == 2
    assert card.n_missing == 1
    assert card.position_ids == (
        "candidate-event-1",
        "candidate-event-1-second",
        "candidate-event-missing",
    )
    assert card.zero_stake_bootstrap_replicates == 0
    assert card.roi_interval is not None


class SparseWeekRepository(Repository):
    def prediction_score_inputs(self, season: int, through_week: int):
        first, _, third, _ = super().prediction_score_inputs(season, through_week)
        return (first, replace(third, week=3))


def test_sparse_weeks_keep_scorecard_with_explicit_unavailable_interval() -> None:
    report = build_weekly_scorecards(SparseWeekRepository(), 2026, 3)
    probability = report.probability[0]

    assert probability.multinomial_log_loss is not None
    assert probability.log_loss_interval is None
    assert probability.interval_unavailable_reason == "NON_CONTIGUOUS_WEEKS"


def test_report_workflow_requires_frozen_evaluation_policy() -> None:
    signature = inspect.signature(ReportWorkflow)
    assert signature.parameters["evaluation_policy"].default is inspect.Parameter.empty
    assert signature.parameters["interval_confidence"].default is inspect.Parameter.empty
    report = ReportWorkflow(
        Repository(),
        evaluation_policy=EVALUATION_POLICY,
        interval_confidence=Decimal("0.95"),
    ).weekly(2026, 2)
    assert report.probability


def test_pairwise_scorecard_retains_both_complete_variant_and_evaluation_lineage() -> None:
    report = build_weekly_scorecards(PairwiseRepository(), 2026, 1)
    card = next(row for row in report.pairwise if row.challenger_artifact_id == "challenger-a")

    assert card.champion_calibrator_artifact_id == "calibrator-champion"
    assert card.challenger_calibrator_artifact_id == "calibrator-challenger-a"
    assert card.champion_policy_versions == (
        ("calibration", "v2"),
        ("forecast", "v1"),
    )
    assert card.challenger_policy_versions == (
        ("calibration", "v2"),
        ("forecast", "v1"),
    )
    assert card.evaluation_policy_version == EVALUATION_POLICY.policy_version
    assert card.bootstrap_seed == EVALUATION_POLICY.bootstrap_seed
    assert card.moving_block_weeks == EVALUATION_POLICY.moving_block_weeks
    assert card.interval_confidence == Decimal("0.95")


class PendingOutcomeRepository(Repository):
    def prediction_score_inputs(self, season: int, through_week: int):
        return (
            PredictionScoreInput(
                season,
                1,
                prediction("event-pending", Origin.T60, "champion"),
                None,
            ),
        )


def test_pending_prediction_is_present_but_outcome_unresolved_not_forecast_missing() -> None:
    report = build_weekly_scorecards(PendingOutcomeRepository(), 2026, 1)
    card = report.probability[0]

    assert card.coverage == 1
    assert card.n_missing == 0
    assert card.n_unresolved == 1
    assert card.outcome_keys == ()


class SettlementStateRepository(Repository):
    policies = (
        ("candidate", "candidate-v1"),
        ("feature", "features-v1"),
        ("forecast", "forecast-v1"),
        ("model", "artifact-v1"),
        ("operator", "operator-v1"),
        ("settlement", "settlement-v1"),
    )

    def displayed_return_inputs(self, season: int, through_week: int):
        base = ReturnScoreInput(
            season=season,
            week=1,
            canonical_event_id="event-return",
            outcome_id="outcome-return",
            outcome_version=1,
            origin=Origin.T60,
            model_role="champion",
            artifact_id="artifact-v1",
            calibrator_artifact_id="calibrator-v1",
            policy_versions=self.policies,
            profit=None,
            stake=Decimal(1),
            evidence_grade=ProvenanceGrade.A,
            candidate_id="candidate-unresolved",
            settlement_id="settlement-unresolved",
            operator_evidence_id="evidence-conflict",
            settlement_result="unresolved",
        )
        return (
            base,
            replace(
                base,
                candidate_id="candidate-void",
                settlement_id="settlement-void",
                operator_evidence_id="evidence-void",
                settlement_result="void",
                profit=Decimal(0),
            ),
            replace(
                base,
                candidate_id="candidate-push",
                settlement_id="settlement-push",
                operator_evidence_id="evidence-push",
                settlement_result="push",
                profit=Decimal(0),
            ),
        )

    def displayed_return_obligation_inputs(self, season: int, through_week: int):
        return tuple(
            ReturnObligationInput(
                season,
                1,
                candidate_id,
                "event-return",
                Origin.T60,
                "champion",
                "artifact-v1",
                "calibrator-v1",
                self.policies,
                ProvenanceGrade.A,
                candidate_id=candidate_id,
            )
            for candidate_id in (
                "candidate-unresolved",
                "candidate-void",
                "candidate-push",
                "candidate-missing",
            )
        )


def test_return_states_and_full_policy_lineage_are_distinct_from_missing_positions() -> None:
    card = build_weekly_scorecards(SettlementStateRepository(), 2026, 1).displayed_price[0]

    assert card.policy_versions == SettlementStateRepository.policies
    assert card.coverage == Decimal("0.75")
    assert card.n_missing == 1
    assert card.n_unresolved == 1
    assert card.n_void == 1
    assert card.n_push == 1
    assert card.n_positions == 3
    assert card.total_profit == 0
    assert card.total_stake == 2
