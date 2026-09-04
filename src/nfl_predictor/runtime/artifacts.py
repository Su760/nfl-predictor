"""Anchored frozen forecast artifacts and deterministic calibrated prediction."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import joblib  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from nfl_predictor.contracts.enums import Origin, PredictionStatus
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.lineage import FeatureSnapshot
from nfl_predictor.features.schema import FEATURE_SCHEMA_V1
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.models.artifacts import ArtifactIntegrityError, ArtifactMetadata
from nfl_predictor.models.base import ProbabilityModel
from nfl_predictor.models.tie import TieLayer, to_three_way
from nfl_predictor.workflows.forecast import ArtifactBinding, ForecastExecutionContext

_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = r"^[0-9a-f]{64}$"


@runtime_checkable
class ProbabilityCalibrator(Protocol):
    def transform(self, values: Sequence[float]) -> NDArray[np.float64]: ...


@dataclass(frozen=True)
class FrozenForecastArtifact:
    model: ProbabilityModel
    calibrator: ProbabilityCalibrator
    calibrator_artifact_id: str
    tie_layer: TieLayer

    def __post_init__(self) -> None:
        if not isinstance(self.calibrator_artifact_id, str) or not self.calibrator_artifact_id.strip():
            raise ValueError("calibrator artifact ID must be non-blank")
        if not isinstance(self.tie_layer, TieLayer):
            raise TypeError("frozen artifact requires a TieLayer")
        if not callable(getattr(self.model, "predict_r_home", None)):
            raise TypeError("frozen artifact requires a probability model")
        if not callable(getattr(self.calibrator, "transform", None)):
            raise TypeError("frozen artifact requires a probability calibrator")


class _RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: str = Field(min_length=1, pattern=_ARTIFACT_ID.pattern)
    origin: Origin
    model_role: Literal["champion", "challenger"]
    model_lane: Literal["football_only", "market_blend"]
    feature_schema_version: str = Field(min_length=1)
    feature_policy_version: str = Field(min_length=1)
    policy_versions: dict[str, str] = Field(min_length=1)
    market_policy_version: str = Field(min_length=1)
    candidate_policy_version: str = Field(min_length=1)
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    dependency_lock_sha256: str = Field(pattern=_SHA256)
    marker_sha256: str = Field(pattern=_SHA256)
    metadata_sha256: str = Field(pattern=_SHA256)
    payload_sha256: str = Field(pattern=_SHA256)


class _RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    entries: tuple[_RegistryEntry, ...] = Field(min_length=1)


class _CommitMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: str = Field(pattern=_ARTIFACT_ID.pattern)
    payload_sha256: str = Field(pattern=_SHA256)
    metadata_sha256: str = Field(pattern=_SHA256)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _under_private_root(private_root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("runtime path must exist under trusted private root") from exc
    try:
        resolved.relative_to(private_root)
    except ValueError as exc:
        raise ValueError("runtime path must resolve under trusted private root") from exc
    return resolved


class VerifiedArtifactRegistry:
    """Verifies every anchored byte before exact-byte joblib deserialization."""

    def __init__(
        self,
        private_root: Path,
        artifact_root: Path,
        registry_path: Path,
        expected_registry_sha256: str,
    ) -> None:
        try:
            resolved_private_root = private_root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("trusted private root must exist") from exc
        if not resolved_private_root.is_dir():
            raise ValueError("trusted private root must be a directory")
        if re.fullmatch(_SHA256, expected_registry_sha256) is None:
            raise ValueError("expected registry SHA must be SHA-256")
        resolved_artifact_root = _under_private_root(resolved_private_root, artifact_root)
        if not resolved_artifact_root.is_dir():
            raise ValueError("artifact root must be a directory under trusted private root")
        self._private_root = resolved_private_root
        self._artifact_root = resolved_artifact_root
        self._registry_path = _under_private_root(resolved_private_root, registry_path)
        self._expected_registry_sha256 = expected_registry_sha256
        self._verified: dict[str, tuple[FrozenForecastArtifact, ArtifactMetadata]] = {}

    @classmethod
    def from_private_config(
        cls,
        private_root: Path,
        artifact_root: Path,
        registry_path: Path,
        expected_registry_sha256: str,
    ) -> VerifiedArtifactRegistry:
        return cls(private_root, artifact_root, registry_path, expected_registry_sha256)

    def _load_registry(self) -> _RegistryDocument:
        try:
            payload = _under_private_root(self._private_root, self._registry_path).read_bytes()
        except (OSError, ValueError) as exc:
            raise ArtifactIntegrityError("could not read artifact registry") from exc
        if _sha256(payload) != self._expected_registry_sha256:
            raise ArtifactIntegrityError("artifact registry hash does not match private configuration")
        try:
            return _RegistryDocument.model_validate_json(payload)
        except Exception as exc:
            raise ArtifactIntegrityError("invalid artifact registry") from exc

    def _artifact_bytes(self, directory: Path, name: str, expected_sha256: str) -> bytes:
        try:
            path = _under_private_root(self._private_root, directory / name)
            path.relative_to(directory)
            payload = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ArtifactIntegrityError("artifact is not committed") from exc
        if _sha256(payload) != expected_sha256:
            raise ArtifactIntegrityError(f"artifact {name} hash does not match frozen registry")
        return payload

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
            or metadata.payload_sha256 != entry.payload_sha256
        ):
            raise ArtifactIntegrityError("artifact metadata does not match frozen registry entry")

    def _preflight(self, entry: _RegistryEntry) -> tuple[bytes, ArtifactMetadata]:
        if _ARTIFACT_ID.fullmatch(entry.artifact_id) is None:
            raise ArtifactIntegrityError("invalid artifact ID")
        try:
            directory = _under_private_root(self._private_root, self._artifact_root / entry.artifact_id)
            directory.relative_to(self._artifact_root)
        except ValueError as exc:
            raise ArtifactIntegrityError("artifact path escaped trusted private root") from exc
        marker_bytes = self._artifact_bytes(directory, "COMMITTED.json", entry.marker_sha256)
        metadata_bytes = self._artifact_bytes(directory, "metadata.json", entry.metadata_sha256)
        payload_bytes = self._artifact_bytes(directory, "model.joblib", entry.payload_sha256)
        try:
            marker = _CommitMarker.model_validate_json(marker_bytes)
            metadata = ArtifactMetadata.model_validate_json(metadata_bytes)
        except Exception as exc:
            raise ArtifactIntegrityError("invalid anchored artifact metadata") from exc
        if (
            marker.artifact_id != entry.artifact_id
            or marker.metadata_sha256 != entry.metadata_sha256
            or marker.payload_sha256 != entry.payload_sha256
        ):
            raise ArtifactIntegrityError("commit marker does not match frozen registry entry")
        if metadata.artifact_id != entry.artifact_id:
            raise ArtifactIntegrityError("artifact ID does not match frozen registry entry")
        self._verify_metadata(entry, metadata)
        return payload_bytes, metadata

    def _verify_entry(self, entry: _RegistryEntry) -> ArtifactBinding:
        payload_bytes, metadata = self._preflight(entry)
        try:
            payload = joblib.load(BytesIO(payload_bytes))
        except Exception as exc:
            raise ArtifactIntegrityError("invalid anchored artifact payload") from exc
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
        self._verified[entry.artifact_id] = (payload, metadata)
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
        document = self._load_registry()
        bindings = tuple(self._verify_entry(entry) for entry in document.entries if entry.origin is origin)
        if sum(binding.model_role == "champion" for binding in bindings) != 1:
            raise ArtifactIntegrityError("origin requires exactly one champion")
        return bindings

    def artifact_and_metadata_for(
        self, binding: ArtifactBinding
    ) -> tuple[FrozenForecastArtifact, ArtifactMetadata]:
        if not binding.frozen or not binding.verified:
            raise ArtifactIntegrityError("prediction requires a frozen verified artifact binding")
        verified = {item.artifact_id: item for item in self.for_origin(binding.origin)}
        if verified.get(binding.artifact_id) != binding:
            raise ArtifactIntegrityError("artifact binding is not the current verified registry binding")
        try:
            return self._verified[binding.artifact_id]
        except KeyError as exc:
            raise ArtifactIntegrityError("verified artifact payload is unavailable") from exc


class VerifiedForecastPredictor:
    def __init__(self, registry: VerifiedArtifactRegistry) -> None:
        self._registry = registry

    @staticmethod
    def _feature_row(snapshot: FeatureSnapshot) -> NDArray[np.float64]:
        names = tuple(name for name, _ in FEATURE_SCHEMA_V1)
        if set(snapshot.values) != set(names) or snapshot.feature_schema_version != "schema-v1":
            raise ValueError("feature snapshot values do not match FEATURE_SCHEMA_V1")
        try:
            row = np.asarray([[snapshot.values[name] for name in names]], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("feature snapshot values are not float64-compatible") from exc
        if not np.isfinite(row).all():
            raise ValueError("feature snapshot contains non-finite values")
        return row

    @staticmethod
    def _calibrated_home(artifact: FrozenForecastArtifact, row: NDArray[np.float64]) -> float:
        raw = np.asarray(artifact.model.predict_r_home(row), dtype=np.float64)
        if raw.shape != (1,) or not np.isfinite(raw).all() or not 0 <= float(raw[0]) <= 1:
            raise ValueError("model must return one finite home probability")
        calibrated = np.asarray(
            artifact.calibrator.transform([float(value) for value in raw]), dtype=np.float64
        )
        if (
            calibrated.shape != (1,)
            or not np.isfinite(calibrated).all()
            or not 0 <= float(calibrated[0]) <= 1
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
        frozen, metadata = self._registry.artifact_and_metadata_for(artifact)
        home, away, tie = to_three_way(
            self._calibrated_home(frozen, self._feature_row(snapshot)), frozen.tie_layer.p_tie
        )
        policies = dict(artifact.policy_versions)
        policies["forecast"] = obligation.policy_version
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
            code_sha=metadata.code_sha,
            policy_versions=policies,
            created_at_utc=created_at_utc,
        )


def _decimal_probability(value: float) -> Decimal:
    if not math.isfinite(value):
        raise ValueError("probability must be finite")
    return Decimal(str(value))
