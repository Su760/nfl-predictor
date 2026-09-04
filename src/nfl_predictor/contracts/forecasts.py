from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import UtcModel
from .enums import Origin, PredictionStatus, ProvenanceGrade, RunStatus


class OriginRun(UtcModel):
    origin_run_id: str
    canonical_event_id: str
    event_version: int
    origin: Origin
    forecast_policy_version: str
    target_at_utc: datetime
    window_opens_at_utc: datetime
    window_closes_at_utc: datetime
    run_started_at_utc: datetime | None = None
    decision_at_utc: datetime | None = None
    status: RunStatus
    attempt_ids: list[str]
    prediction_ids: list[str]
    market_comparator_id: str | None = None
    reason_codes: list[str]
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    created_at_utc: datetime

    @model_validator(mode="after")
    def require_terminal_shape(self) -> "OriginRun":
        successful = self.status in {RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY}
        if successful != (self.decision_at_utc is not None and bool(self.prediction_ids)):
            raise ValueError("successful runs require a cutoff and prediction")
        if self.status in {RunStatus.MISSED, RunStatus.FAILED} and self.prediction_ids:
            raise ValueError("missed/failed origin runs cannot contain predictions")
        return self


class Prediction(UtcModel):
    prediction_id: str
    origin_run_id: str
    canonical_event_id: str
    event_version: int
    origin: Origin
    target_at_utc: datetime
    decision_at_utc: datetime
    model_lane: Literal["football_only", "market_blend"]
    model_role: Literal["champion", "fallback", "challenger"]
    model_artifact_id: str
    calibrator_artifact_id: str | None
    feature_snapshot_id: str
    market_snapshot_id: str | None
    p_home: Decimal = Field(ge=0, le=1)
    p_away: Decimal = Field(ge=0, le=1)
    p_tie: Decimal = Field(ge=0, le=1)
    predicted_winner: Literal["home", "away"]
    provenance_grade: ProvenanceGrade
    status: PredictionStatus
    reason_codes: list[str]
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    policy_versions: dict[str, str]
    created_at_utc: datetime

    @model_validator(mode="after")
    def validate_vector_and_pick(self) -> "Prediction":
        if abs(self.p_home + self.p_away + self.p_tie - Decimal(1)) > Decimal("0.000001"):
            raise ValueError("probabilities must sum to 1")
        expected = "home" if self.p_home >= self.p_away else "away"
        if self.predicted_winner != expected:
            raise ValueError("predicted_winner does not match probability rule")
        if self.provenance_grade is ProvenanceGrade.A and not (
            self.target_at_utc - timedelta(minutes=10)
            <= self.decision_at_utc
            <= self.target_at_utc + timedelta(minutes=10)
        ):
            raise ValueError("Grade A decision must be inside its origin window")
        return self
