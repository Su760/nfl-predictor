from __future__ import annotations

from decimal import Decimal

from nfl_predictor.evaluation.promotion import EvaluationPolicy
from nfl_predictor.evaluation.scorecards import (
    ScorecardRepository,
    WeeklyScorecards,
    build_weekly_scorecards,
)


class ReportWorkflow:
    def __init__(
        self,
        repository: ScorecardRepository,
        *,
        evaluation_policy: EvaluationPolicy,
        interval_confidence: Decimal,
    ) -> None:
        if not isinstance(evaluation_policy, EvaluationPolicy):
            raise TypeError("report workflow requires an EvaluationPolicy")
        self.repository = repository
        self.evaluation_policy = evaluation_policy
        self.interval_confidence = interval_confidence

    def weekly(self, season: int, through_week: int) -> WeeklyScorecards:
        return build_weekly_scorecards(
            self.repository,
            season,
            through_week,
            evaluation_policy=self.evaluation_policy,
            interval_confidence=self.interval_confidence,
        )
