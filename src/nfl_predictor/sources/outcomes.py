from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from nfl_predictor.capture.service import CaptureService
from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.sources.base import SourceAdapter

OutcomeStatus = Literal["final", "postponed", "cancelled", "suspended", "unresolved"]
ObservationTimeBasis = Literal["source_snapshot", "retrieval_fallback"]


@dataclass(frozen=True)
class OutcomeObservation:
    source: str
    source_event_id: str
    game_status: OutcomeStatus
    home_score: int | None
    away_score: int | None
    home_team: str | None
    away_team: str | None
    finalized_at_utc: datetime | None
    source_observed_at_utc: datetime
    retrieved_at_utc: datetime
    observation_time_basis: ObservationTimeBasis
    capture_id: str
    raw_payload_sha256: str
    code_sha: str
    dependency_lock_sha256: str


@dataclass(frozen=True)
class OutcomeCapture:
    manifest: CaptureManifest
    observation: OutcomeObservation


class OutcomeAdapter:
    """Canonical CaptureService-backed official nflverse outcome adapter."""

    def __init__(self, capture_service: CaptureService, source: SourceAdapter) -> None:
        if source.source != "nflverse":
            raise ValueError("official outcomes require the nflverse source")
        self.capture_service = capture_service
        self.source = source

    def capture(self, request: dict[str, object], run_id: str) -> OutcomeCapture:
        manifest = self.capture_service.capture(self.source, request, run_id)
        payload = (self.capture_service.data_root / manifest.raw_path).read_bytes()
        return OutcomeCapture(manifest, self.normalize(payload, manifest))

    @staticmethod
    def normalize(payload: bytes, manifest: CaptureManifest) -> OutcomeObservation:
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("nflverse outcome payload is not valid JSON") from error
        if not isinstance(value, dict):
            raise TypeError("nflverse outcome payload must be an object")
        source_event_id = value.get("game_id")
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            raise ValueError("nflverse outcome requires game_id")
        status = value.get("game_status")
        allowed = {"final", "postponed", "cancelled", "suspended", "unresolved"}
        if status not in allowed:
            raise ValueError("nflverse outcome game_status is unsupported")
        home_score = OutcomeAdapter._score(value.get("home_score"), "home_score")
        away_score = OutcomeAdapter._score(value.get("away_score"), "away_score")
        finalized_raw = value.get("finalized_at_utc")
        finalized_at = None
        if status == "final":
            if home_score is None or away_score is None:
                raise ValueError("final nflverse outcome requires both scores")
            if not isinstance(finalized_raw, str):
                raise ValueError("final nflverse outcome requires finalized_at_utc")
            finalized_at = datetime.fromisoformat(finalized_raw)
            if finalized_at.tzinfo is None or finalized_at.utcoffset() != UTC.utcoffset(
                finalized_at
            ):
                raise ValueError("finalized_at_utc must use UTC")
        else:
            home_score = None
            away_score = None
        source_observed = manifest.source_snapshot_at_utc or manifest.response_received_at_utc
        return OutcomeObservation(
            source=manifest.source,
            source_event_id=source_event_id,
            game_status=cast(OutcomeStatus, status),
            home_score=home_score,
            away_score=away_score,
            home_team=OutcomeAdapter._team(value.get("home_team"), "home_team"),
            away_team=OutcomeAdapter._team(value.get("away_team"), "away_team"),
            finalized_at_utc=finalized_at,
            source_observed_at_utc=source_observed,
            retrieved_at_utc=manifest.response_received_at_utc,
            observation_time_basis=(
                "source_snapshot"
                if manifest.source_snapshot_at_utc is not None
                else "retrieval_fallback"
            ),
            capture_id=manifest.capture_id,
            raw_payload_sha256=manifest.raw_payload_sha256,
            code_sha=manifest.code_sha,
            dependency_lock_sha256=manifest.dependency_lock_sha256,
        )

    @staticmethod
    def _score(value: object, name: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer or null")
        return value

    @staticmethod
    def _team(value: object, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-blank string or null")
        return value
