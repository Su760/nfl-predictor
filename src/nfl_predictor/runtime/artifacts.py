"""Verified frozen forecast artifacts and deterministic calibrated prediction."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from nfl_predictor.contracts.enums import Origin, PredictionStatus
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.lineage import FeatureSnapshot
from nfl_predictor.features.schema import FEATURE_SCHEMA_V1
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.models.artifacts import ArtifactIntegrityError, ArtifactMetadata, ArtifactStore
from nfl_predictor.models.base import ProbabilityModel
from nfl_predictor.models.tie import TieLayer, to_three_way
from nfl_predictor.workflows.forecast import (
    ArtifactBinding,
    ForecastExecutionContext,
)


@runtime_checkable
class ProbabilityCalibrator(Protocol):
    """The small calibrated-probability boundary serialized in an artifact."""

    def transform(self, values: Sequence[float]) -> NDArray[np.float64]: ...


@dataclass(frozen=True)
class FrozenForecastArtifact:
    model: ProbabilityModel
    calibrator: ProbabilityCalibrator
    calibrator_artifact_id: str
    tie_layer: TieLayer

    def __post_init__(self) -> None:
        if not self.calibrator_artifact_id.strip():
            raise ValueError("calibrator artifact ID must be non-blank")
        if not isinstance(self.tie_layer, TieLayer):
            raise TypeError("frozen artifact requires a TieLayer")
        if not callable(getattr(self.model, "predict_r_home", None)):
            raise TypeError("frozen artifact requires a probability model")
        if not callable(getattr(self.calibrator, "transform", None)):
            raise TypeError("frozen artifact requires a probability calibrator")


class _RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    origin: Origin
    model_role: Literal["champion", "challenger"]
    model_lane: Literal["football_only", "market_blend"]
    feature_schema_version: str = Field(min_length=1)
    feature_policy_version: str = Field(min_length=1)
    policy_versions: dict[str, str] = Field(min_length=1)
    market_policy_version: str = Field(min_length=1)
    candidate_policy_version: str = Field(min_length=1)
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[_RegistryEntry, ...] = Field(min_length=1)


class VerifiedArtifactRegistry:
    """Binds a reviewed registry digest to committed, structurally safe artifacts."""

    def __init__(self, store: ArtifactStore, registry_path: Path, expected_registry_sha256: str) -> None:
        self._store = store
        self._registry_path = registry_path
        self._expected_registry_sha256 = expected_registry_sha256
        self._payloads: dict[str, FrozenForecastArtifact] = {}

    @classmethod
    def from_private_config(cls, artifact_root: Path, registry_path: Path) -> VerifiedArtifactRegistry:
        resolved_registry = registry_path.resolve(strict=True)
        payload = resolved_registry.read_bytes()
        return cls(
            ArtifactStore(artifact_root),
            resolved_registry,
            hashlib.sha256(payload).hexdigest(),
        )

    def _load_and_hash_registry(self) -> _RegistryDocument:
        try:
            payload = self._registry_path.read_bytes()
        except OSError as exc:
            raise ArtifactIntegrityError("could not read artifact registry") from exc
        if hashlib.sha256(payload).hexdigest() != self._expected_registry_sha256:
            raise ArtifactIntegrityError("artifact registry hash does not match private configuration")
        try:
            return _RegistryDocument.model_validate_json(payload)
        except Exception as exc:
            raise ArtifactIntegrityError("invalid artifact registry") from exc

    @staticmethod
    def _verify_metadata(entry: _RegistryEntry, metadata: ArtifactMetadata) -> None:
        if (
            metadata.origin is not entry.origin
            or metadata.model_lane != entry.model_lane
            or metadata.feature_schema_version != entry.feature_schema_version
            or metadata.feature_policy_version != entry.feature_policy_version
            or metadata.model_policy_version != entry.policy_versions.get("model")
            or metadata.calibration_policy_version != entry.policy_versions.get("calibration")
            or metadata.code_sha != entry.code_sha
            or metadata.dependency_lock_sha256 != entry.dependency_lock_sha256
        ):
            raise ArtifactIntegrityError("artifact metadata does not match frozen registry entry")

    def _verify_entry(self, entry: _RegistryEntry) -> ArtifactBinding:
        payload, metadata = self._store.load_verified(entry.artifact_id)
        if not isinstance(payload, FrozenForecastArtifact):
            raise ArtifactIntegrityError("artifact payload must be FrozenForecastArtifact")
        if (
            not isinstance(payload.calibrator_artifact_id, str)
            or not payload.calibrator_artifact_id.strip()
            or not isinstance(payload.tie_layer, TieLayer)
            or not callable(getattr(payload.model, "predict_r_home", None))
            or not callable(getattr(payload.calibrator, "transform", None))
        ):
            raise ArtifactIntegrityError("frozen artifact payload is structurally invalid")
        self._verify_metadata(entry, metadata)
        self._payloads[entry.artifact_id] = payload
        return ArtifactBinding(
            artifact_id=entry.artifact_id,
            origin=entry.origin,
            model_role=entry.model_role,
            frozen=True,
            verified=True,
            calibrator_artifact_id=payload.calibrator_artifact_id,
            model_lane=entry.model_lane,
            feature_policy_version=entry.feature_policy_version,
            feature_schema_version=entry.feature_schema_version,
            policy_versions=tuple(sorted(entry.policy_versions.items())),
            market_policy_version=entry.market_policy_version,
            candidate_policy_version=entry.candidate_policy_version,
        )

    def for_origin(self, origin: Origin) -> tuple[ArtifactBinding, ...]:
        document = self._load_and_hash_registry()
        entries = tuple(entry for entry in document.entries if entry.origin is origin)
        bindings = tuple(self._verify_entry(entry) for entry in entries)
        if sum(binding.model_role == "champion" for binding in bindings) != 1:
            raise ArtifactIntegrityError("origin requires exactly one champion")
        return bindings

    def artifact_for(self, binding: ArtifactBinding) -> FrozenForecastArtifact:
        if not binding.frozen or not binding.verified:
            raise ArtifactIntegrityError("prediction requires a frozen verified artifact binding")
        # Re-verify on every prediction. A prior successful lookup must not authorize a later tamper.
        verified = {item.artifact_id: item for item in self.for_origin(binding.origin)}
        if verified.get(binding.artifact_id) != binding:
            raise ArtifactIntegrityError("artifact binding is not the current verified registry binding")
        try:
            return self._payloads[binding.artifact_id]
        except KeyError as exc:
            raise ArtifactIntegrityError("verified artifact payload is unavailable") from exc


class VerifiedForecastPredictor:
    def __init__(self, registry: VerifiedArtifactRegistry) -> None:
        self._registry = registry

    @staticmethod
    def _feature_row(snapshot: FeatureSnapshot) -> NDArray[np.float64]:
        expected_names = tuple(name for name, _ in FEATURE_SCHEMA_V1)
        if tuple(snapshot.values) != expected_names and set(snapshot.values) != set(expected_names):
            raise ValueError("feature snapshot values do not match FEATURE_SCHEMA_V1")
        if snapshot.feature_schema_version != "schema-v1":
            raise ValueError("feature snapshot schema version is not supported")
        try:
            row = np.asarray([[snapshot.values[name] for name in expected_names]], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("feature snapshot values are not float64-compatible") from exc
        if not np.isfinite(row).all():
            raise ValueError("feature snapshot contains non-finite values")
        return row

    @staticmethod
    def _calibrated_home(artifact: FrozenForecastArtifact, row: NDArray[np.float64]) -> float:
        raw = np.asarray(artifact.model.predict_r_home(row), dtype=np.float64)
        if raw.shape != (1,) or not np.isfinite(raw).all() or not 0.0 <= float(raw[0]) <= 1.0:
            raise ValueError("model must return one finite home probability")
        calibrated = np.asarray(
            artifact.calibrator.transform([float(value) for value in raw]), dtype=np.float64
        )
        if (
            calibrated.shape != (1,)
            or not np.isfinite(calibrated).all()
            or not 0.0 <= float(calibrated[0]) <= 1.0
        ):
            raise ValueError("calibrator must return one finite home probability")
        return float(calibrated[0])

    def predict(
        self,
        snapshot: FeatureSnapshot,
        artifact: ArtifactBinding,
        *,
        origin_run_id: str,
        obligation: OriginObligation,
        created_at_utc: datetime,
        context: ForecastExecutionContext,
    ) -> Prediction:
        if (
            snapshot.origin is not artifact.origin
            or obligation.origin is not artifact.origin
            or snapshot.canonical_event_id != obligation.event.canonical_event_id
            or snapshot.event_version != obligation.event.event_version
            or snapshot.feature_policy_version != artifact.feature_policy_version
            or snapshot.feature_schema_version != artifact.feature_schema_version
        ):
            raise ValueError("prediction inputs do not match artifact lineage")
        frozen = self._registry.artifact_for(artifact)
        home, away, tie = to_three_way(self._calibrated_home(frozen, self._feature_row(snapshot)), frozen.tie_layer.p_tie)
        policy_versions = dict(artifact.policy_versions)
        policy_versions["forecast"] = obligation.policy_version
        prediction_id = hashlib.sha256(
            f"{origin_run_id}|{artifact.artifact_id}|{snapshot.snapshot_id}|"
            f"{artifact.feature_policy_version}|{artifact.feature_schema_version}|"
            f"{obligation.policy_version}".encode()
        ).hexdigest()
        return Prediction(
            prediction_id=prediction_id,
            origin_run_id=origin_run_id,
            canonical_event_id=obligation.event.canonical_event_id,
            event_version=obligation.event.event_version,
            origin=artifact.origin,
            target_at_utc=obligation.window.target_at_utc,
            decision_at_utc=snapshot.decision_at_utc,
            model_lane=artifact.model_lane,
            model_role=artifact.model_role,
            model_artifact_id=artifact.artifact_id,
            calibrator_artifact_id=frozen.calibrator_artifact_id,
            feature_snapshot_id=snapshot.snapshot_id,
            market_snapshot_id=None,
            p_home=_decimal_probability(home),
            p_away=_decimal_probability(away),
            p_tie=_decimal_probability(tie),
            predicted_winner="home" if home >= away else "away",
            provenance_grade=context.provenance_grade,
            status=PredictionStatus.COMPLETE,
            reason_codes=[] if context.mode == "live" else [context.reconstruction_reason or ""],
            code_sha=self._registry._store.load_verified(artifact.artifact_id)[1].code_sha,
            policy_versions=policy_versions,
            created_at_utc=created_at_utc,
        )


def _decimal_probability(value: float) -> Decimal:
    if not math.isfinite(value):
        raise ValueError("probability must be finite")
    return Decimal(str(value))
