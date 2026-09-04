from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self

import joblib  # type: ignore[import-untyped]
from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from nfl_predictor.contracts.common import UtcModel
from nfl_predictor.contracts.enums import Origin

_ARTIFACT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

ArtifactId = Annotated[str, Field(pattern=_ARTIFACT_ID_PATTERN)]
Sha1 = Annotated[str, Field(pattern=_SHA1_PATTERN)]
Sha256 = Annotated[str, Field(pattern=_SHA256_PATTERN)]
NonBlank = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
PythonVersion = Annotated[str, Field(pattern=r"^3\.11\.[0-9]+$")]


class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactMetadata(UtcModel):
    artifact_id: ArtifactId
    model_family: NonBlank
    calibrator_family: NonBlank
    origin: Origin
    model_lane: Literal["football_only", "market_blend"]
    feature_schema_version: NonBlank
    feature_policy_version: NonBlank
    model_policy_version: NonBlank
    calibration_policy_version: NonBlank
    split_policy_version: NonBlank
    input_manifest_sha256s: tuple[Sha256, ...] = Field(min_length=1)
    training_event_ids: tuple[NonBlank, ...] = Field(min_length=1)
    calibration_event_ids: tuple[NonBlank, ...] = Field(min_length=1)
    training_cutoff_at_utc: datetime
    calibration_cutoff_at_utc: datetime
    code_sha: Sha1
    dependency_lock_sha256: Sha256
    seeds: dict[NonBlank, StrictInt] = Field(min_length=1)
    fold_ledger_path: NonBlank
    fold_ledger_sha256: Sha256
    metrics: dict[NonBlank, float] = Field(min_length=1)
    python_version: PythonVersion
    payload_sha256: Sha256 | None = None

    @field_validator("fold_ledger_path")
    @classmethod
    def require_safe_fold_ledger_path(cls, value: str) -> str:
        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            value != value.strip()
            or value == "."
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or windows_path.root
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise ValueError("fold ledger path must be a safe relative path")
        return value

    @field_validator("metrics")
    @classmethod
    def require_finite_metrics(cls, metrics: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(value) for value in metrics.values()):
            raise ValueError("artifact metrics must be finite")
        return metrics

    @model_validator(mode="after")
    def require_consistent_lineage(self) -> Self:
        if len(set(self.training_event_ids)) != len(self.training_event_ids):
            raise ValueError("training event IDs must be unique")
        if len(set(self.calibration_event_ids)) != len(self.calibration_event_ids):
            raise ValueError("calibration event IDs must be unique")
        if set(self.training_event_ids) & set(self.calibration_event_ids):
            raise ValueError("training and calibration event IDs must be disjoint")
        if len(set(self.input_manifest_sha256s)) != len(self.input_manifest_sha256s):
            raise ValueError("input manifest hashes must be unique")
        if self.training_cutoff_at_utc > self.calibration_cutoff_at_utc:
            raise ValueError("training cutoff must not be after calibration cutoff")
        return self


class _CommitMarker(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: ArtifactId
    payload_sha256: Sha256
    metadata_sha256: Sha256


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _directory(self, artifact_id: str) -> Path:
        if not re.fullmatch(_ARTIFACT_ID_PATTERN, artifact_id):
            raise ArtifactIntegrityError("invalid artifact id")
        path = (self.root / artifact_id).resolve()
        if path.parent != self.root:
            raise ArtifactIntegrityError("artifact path escaped trusted root")
        if path.name != artifact_id:
            raise ArtifactIntegrityError("artifact id mismatch between request and directory")
        return path

    def payload_path(self, artifact_id: str) -> Path:
        return self._directory(artifact_id) / "model.joblib"

    def metadata_path(self, artifact_id: str) -> Path:
        return self._directory(artifact_id) / "metadata.json"

    def marker_path(self, artifact_id: str) -> Path:
        return self._directory(artifact_id) / "COMMITTED.json"

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _write_bytes_atomic(cls, path: Path, payload: bytes) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            cls._fsync_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def _write_joblib_atomic(cls, path: Path, model: Any) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
            joblib.dump(model, temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            cls._fsync_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, model: Any, metadata: ArtifactMetadata) -> str:
        if metadata.payload_sha256 is not None:
            raise ArtifactIntegrityError("payload hash must be unset before saving")
        directory = self._directory(metadata.artifact_id)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError as exc:
            raise ArtifactIntegrityError(
                "artifact directory already exists and cannot be reused"
            ) from exc
        self._fsync_directory(self.root)

        payload_path = directory / "model.joblib"
        self._write_joblib_atomic(payload_path, model)
        payload_sha256 = self._sha(payload_path)
        completed = metadata.model_copy(update={"payload_sha256": payload_sha256})

        metadata_path = directory / "metadata.json"
        self._write_bytes_atomic(
            metadata_path,
            completed.model_dump_json(indent=2).encode("utf-8"),
        )
        marker = _CommitMarker(
            artifact_id=completed.artifact_id,
            payload_sha256=payload_sha256,
            metadata_sha256=self._sha(metadata_path),
        )
        self._write_bytes_atomic(
            directory / "COMMITTED.json",
            json.dumps(marker.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
        )
        return completed.artifact_id

    def load_verified(self, artifact_id: str) -> tuple[Any, ArtifactMetadata]:
        directory = self._directory(artifact_id)
        marker_path = directory / "COMMITTED.json"
        metadata_path = directory / "metadata.json"
        payload_path = directory / "model.joblib"
        if not marker_path.is_file():
            raise ArtifactIntegrityError("artifact is not committed")

        try:
            marker = _CommitMarker.model_validate_json(marker_path.read_bytes())
        except Exception as exc:
            raise ArtifactIntegrityError("invalid commit marker") from exc
        if marker.artifact_id != artifact_id or directory.name != artifact_id:
            raise ArtifactIntegrityError(
                "artifact id mismatch between request, directory, and marker"
            )
        if not metadata_path.is_file() or not payload_path.is_file():
            raise ArtifactIntegrityError("artifact is not committed")

        try:
            metadata_sha256 = self._sha(metadata_path)
        except OSError as exc:
            raise ArtifactIntegrityError("could not verify metadata") from exc
        if marker.metadata_sha256 != metadata_sha256:
            raise ArtifactIntegrityError("metadata hash mismatch")
        try:
            metadata = ArtifactMetadata.model_validate_json(metadata_path.read_bytes())
        except Exception as exc:
            raise ArtifactIntegrityError("invalid metadata") from exc
        if metadata.artifact_id != artifact_id:
            raise ArtifactIntegrityError(
                "artifact id mismatch between request, directory, and metadata"
            )

        try:
            payload_sha256 = self._sha(payload_path)
        except OSError as exc:
            raise ArtifactIntegrityError("could not verify payload") from exc
        if marker.payload_sha256 != payload_sha256:
            raise ArtifactIntegrityError("payload hash mismatch")
        if metadata.payload_sha256 != marker.payload_sha256:
            raise ArtifactIntegrityError("manifest payload hash mismatch")
        try:
            model = joblib.load(payload_path)
        except Exception as exc:
            raise ArtifactIntegrityError("invalid payload") from exc
        return model, metadata
