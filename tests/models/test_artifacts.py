from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

import nfl_predictor.models.artifacts as artifact_module
from nfl_predictor.contracts.enums import Origin
from nfl_predictor.models.artifacts import (
    ArtifactIntegrityError,
    ArtifactMetadata,
    ArtifactStore,
)
from nfl_predictor.models.baselines import HomePriorModel


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fitted_model() -> HomePriorModel:
    return HomePriorModel().fit(
        np.zeros((4, 42), dtype=np.float64),
        ["home", "tie", "away", "home"],
    )


@pytest.fixture
def artifact_metadata() -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id="elo-rating-v1",
        model_family="rating_probability",
        calibrator_family="none",
        origin=Origin.T72,
        model_lane="football_only",
        feature_schema_version="features-v1",
        feature_policy_version="feature-v1",
        model_policy_version="model-v1",
        calibration_policy_version="calibration-v1",
        split_policy_version="split-v1",
        input_manifest_sha256s=("a" * 64, "b" * 64),
        training_event_ids=("game-1", "game-2"),
        calibration_event_ids=("game-3",),
        training_cutoff_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
        calibration_cutoff_at_utc=datetime(2025, 2, 1, tzinfo=UTC),
        code_sha="c" * 40,
        dependency_lock_sha256="d" * 64,
        seeds={"model": 42},
        fold_ledger_path="folds/ledger.parquet",
        fold_ledger_sha256="e" * 64,
        metrics={"log_loss": 0.5, "brier": 0.2},
        python_version="3.11.13",
    )


# Catches: returning an object without verifying persisted payload/metadata/marker hashes.
def test_artifact_round_trip_verifies_and_restores_model(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)

    restored, completed = store.load_verified(artifact_id)

    assert artifact_id == "elo-rating-v1"
    assert store.marker_path(artifact_id).is_file()
    assert completed.payload_sha256 == _sha(store.payload_path(artifact_id))
    assert completed.origin is Origin.T72
    assert restored.n_fit == 3
    assert restored.predict_r_home(np.zeros((1, 42))).tolist() == pytest.approx([2.0 / 3.0])


# Catches: a changed payload is deserialized despite no longer matching the committed artifact.
def test_modified_payload_is_rejected(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    store.payload_path(artifact_id).write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError, match="payload hash mismatch"):
        store.load_verified(artifact_id)


# Catches: changed metadata is trusted without matching the final committed marker.
def test_modified_metadata_is_rejected(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    store.metadata_path(artifact_id).write_text("{}", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="metadata hash mismatch"):
        store.load_verified(artifact_id)


# Catches: payload or metadata files alone are treated as a committed artifact after interruption.
@pytest.mark.parametrize(("successful_replaces", "expected_files"), [(1, 1), (2, 2)])
def test_interrupted_save_is_quarantined_and_never_reused(
    tmp_path: Path,
    fitted_model: HomePriorModel,
    artifact_metadata: ArtifactMetadata,
    monkeypatch: pytest.MonkeyPatch,
    successful_replaces: int,
    expected_files: int,
) -> None:
    store = ArtifactStore(tmp_path)
    real_replace = os.replace
    calls = 0

    def interrupt_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        nonlocal calls
        calls += 1
        if calls > successful_replaces:
            raise OSError("simulated interruption")
        real_replace(source, destination)

    monkeypatch.setattr(artifact_module.os, "replace", interrupt_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        store.save(fitted_model, artifact_metadata)
    monkeypatch.setattr(artifact_module.os, "replace", real_replace)

    final_files = [
        path
        for path in (
            store.payload_path(artifact_metadata.artifact_id),
            store.metadata_path(artifact_metadata.artifact_id),
            store.marker_path(artifact_metadata.artifact_id),
        )
        if path.exists()
    ]
    assert len(final_files) == expected_files
    assert not store.marker_path(artifact_metadata.artifact_id).exists()
    with pytest.raises(ArtifactIntegrityError, match="not committed"):
        store.load_verified(artifact_metadata.artifact_id)
    with pytest.raises(ArtifactIntegrityError, match="already exists"):
        store.save(fitted_model, artifact_metadata)


# Catches: missing or malformed markers allow partially written artifacts to load.
@pytest.mark.parametrize("mutate_marker", ["missing", "malformed", "extra-field"])
def test_missing_or_malformed_marker_is_rejected(
    tmp_path: Path,
    fitted_model: HomePriorModel,
    artifact_metadata: ArtifactMetadata,
    mutate_marker: str,
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    marker_path = store.marker_path(artifact_id)
    if mutate_marker == "missing":
        marker_path.unlink()
    elif mutate_marker == "malformed":
        marker_path.write_text("{", encoding="utf-8")
    else:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["unexpected"] = True
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        store.load_verified(artifact_id)


# Catches: requested, marker, and metadata artifact IDs can disagree while hashes remain valid.
@pytest.mark.parametrize("mutate", ["marker-id", "metadata-id"])
def test_artifact_id_disagreement_is_rejected(
    tmp_path: Path,
    fitted_model: HomePriorModel,
    artifact_metadata: ArtifactMetadata,
    mutate: str,
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    marker_path = store.marker_path(artifact_id)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if mutate == "marker-id":
        marker["artifact_id"] = "different-id"
    else:
        metadata_path = store.metadata_path(artifact_id)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["artifact_id"] = "different-id"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        marker["metadata_sha256"] = _sha(metadata_path)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="artifact id mismatch"):
        store.load_verified(artifact_id)


# Catches: an in-root symlink alias bypasses requested/directory artifact-ID agreement.
def test_directory_alias_is_rejected(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    (tmp_path / "alias").symlink_to(tmp_path / artifact_id, target_is_directory=True)

    with pytest.raises(ArtifactIntegrityError, match="artifact id mismatch"):
        store.load_verified("alias")


# Catches: traversal or separator-bearing artifact IDs escape or ambiguously address the trusted root.
@pytest.mark.parametrize("artifact_id", ["../escape", "nested/id", "/absolute", "", "."])
def test_invalid_or_escaping_artifact_id_is_rejected(tmp_path: Path, artifact_id: str) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactIntegrityError, match="invalid artifact id"):
        store.load_verified(artifact_id)


# Catches: malformed metadata parsing leaks a raw Pydantic/JSON exception after hash verification.
def test_malformed_metadata_failure_is_wrapped(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    metadata_path = store.metadata_path(artifact_id)
    metadata_path.write_text("{", encoding="utf-8")
    marker_path = store.marker_path(artifact_id)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["metadata_sha256"] = _sha(metadata_path)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="invalid metadata"):
        store.load_verified(artifact_id)


# Catches: malformed joblib data leaks a raw deserialization exception after all hashes verify.
def test_malformed_payload_failure_is_wrapped(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    artifact_id = store.save(fitted_model, artifact_metadata)
    payload_path = store.payload_path(artifact_id)
    payload_path.write_bytes(b"not-joblib")
    payload_sha = _sha(payload_path)

    metadata_path = store.metadata_path(artifact_id)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["payload_sha256"] = payload_sha
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    marker_path = store.marker_path(artifact_id)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["payload_sha256"] = payload_sha
    marker["metadata_sha256"] = _sha(metadata_path)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="invalid payload"):
        store.load_verified(artifact_id)


# Catches: caller-supplied payload hashes can be persisted without describing the actual payload.
def test_save_rejects_prepopulated_payload_hash(
    tmp_path: Path, fitted_model: HomePriorModel, artifact_metadata: ArtifactMetadata
) -> None:
    store = ArtifactStore(tmp_path)
    metadata = artifact_metadata.model_copy(update={"payload_sha256": "f" * 64})
    with pytest.raises(ArtifactIntegrityError, match="payload hash"):
        store.save(fitted_model, metadata)


# Catches: blank model, calibrator, schema, or policy identifiers erase reproducibility lineage.
@pytest.mark.parametrize(
    "field",
    [
        "model_family",
        "calibrator_family",
        "feature_schema_version",
        "feature_policy_version",
        "model_policy_version",
        "calibration_policy_version",
        "split_policy_version",
    ],
)
def test_metadata_rejects_blank_provenance_identifiers(
    artifact_metadata: ArtifactMetadata, field: str
) -> None:
    data = artifact_metadata.model_dump()
    data[field] = "   "
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: an unsafe ledger path can escape the artifact repository or name no ledger.
@pytest.mark.parametrize(
    "fold_ledger_path",
    [
        "",
        "   ",
        "/tmp/ledger.parquet",
        "../ledger.parquet",
        "folds/../ledger.parquet",
        r"C:ledger.parquet",
        r"\ledger.parquet",
    ],
)
def test_metadata_rejects_unsafe_fold_ledger_path(
    artifact_metadata: ArtifactMetadata, fold_ledger_path: str
) -> None:
    data = artifact_metadata.model_dump()
    data["fold_ledger_path"] = fold_ledger_path
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: empty or blank event IDs make training/calibration membership unverifiable.
@pytest.mark.parametrize(
    ("field", "event_ids"),
    [
        ("training_event_ids", ()),
        ("calibration_event_ids", ()),
        ("training_event_ids", ("game-1", "   ")),
        ("calibration_event_ids", ("",)),
    ],
)
def test_metadata_rejects_empty_or_blank_event_ids(
    artifact_metadata: ArtifactMetadata, field: str, event_ids: tuple[str, ...]
) -> None:
    data = artifact_metadata.model_dump()
    data[field] = event_ids
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: empty or duplicate input hashes weaken the immutable input manifest.
@pytest.mark.parametrize("hashes", [(), ("a" * 64, "a" * 64)])
def test_metadata_rejects_empty_or_duplicate_input_hashes(
    artifact_metadata: ArtifactMetadata, hashes: tuple[str, ...]
) -> None:
    data = artifact_metadata.model_dump()
    data["input_manifest_sha256s"] = hashes
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: absent, unnamed, coerced, or boolean seeds cannot reproduce model fitting.
@pytest.mark.parametrize(
    "seeds",
    [{}, {"": 42}, {"   ": 42}, {"model": True}, {"model": 42.0}, {"model": "42"}],
)
def test_metadata_rejects_invalid_seeds(
    artifact_metadata: ArtifactMetadata, seeds: dict[str, object]
) -> None:
    data = artifact_metadata.model_dump()
    data["seeds"] = seeds
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: absent or unnamed metrics make a trusted artifact unevaluable.
@pytest.mark.parametrize("metrics", [{}, {"": 0.5}, {"   ": 0.5}])
def test_metadata_rejects_empty_or_blank_metrics(
    artifact_metadata: ArtifactMetadata, metrics: dict[str, float]
) -> None:
    data = artifact_metadata.model_dump()
    data["metrics"] = metrics
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: unsupported or ambiguous Python versions break artifact runtime reproducibility.
@pytest.mark.parametrize("python_version", ["", "3.11", "3.11.x", "3.12.0", "Python 3.11.14"])
def test_metadata_requires_supported_python_patch_version(
    artifact_metadata: ArtifactMetadata, python_version: str
) -> None:
    data = artifact_metadata.model_dump()
    data["python_version"] = python_version
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: calibration lineage predates the training cutoff despite claiming a later stage.
def test_metadata_rejects_training_cutoff_after_calibration(
    artifact_metadata: ArtifactMetadata,
) -> None:
    data = artifact_metadata.model_dump()
    data["training_cutoff_at_utc"] = datetime(2025, 3, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="training cutoff"):
        ArtifactMetadata.model_validate(data)


# Catches: non-finite metrics or inconsistent/duplicate lineage metadata enter a trusted manifest.
@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(metrics={"log_loss": float("nan")}),
        lambda data: data.update(metrics={"log_loss": float("inf")}),
        lambda data: data.update(training_event_ids=("game-1", "game-1")),
        lambda data: data.update(calibration_event_ids=("game-2",)),
        lambda data: data.update(input_manifest_sha256s=("a" * 64, "a" * 64)),
        lambda data: data.update(input_manifest_sha256s=("not-a-sha",)),
    ],
)
def test_metadata_rejects_non_finite_or_inconsistent_lineage(
    artifact_metadata: ArtifactMetadata, mutate: Callable[[dict[str, Any]], None]
) -> None:
    data = artifact_metadata.model_dump()
    mutate(data)
    with pytest.raises(ValidationError):
        ArtifactMetadata.model_validate(data)


# Catches: naive or offset cutoffs violate the normalized UTC artifact contract.
@pytest.mark.parametrize(
    "bad_cutoff",
    [
        datetime.fromisoformat("2025-01-01"),
        datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=-6))),
    ],
)
def test_metadata_requires_normalized_utc_cutoffs(
    artifact_metadata: ArtifactMetadata, bad_cutoff: datetime
) -> None:
    data = artifact_metadata.model_dump()
    data["training_cutoff_at_utc"] = bad_cutoff
    with pytest.raises(ValidationError, match="normalized to UTC|timezone-aware UTC"):
        ArtifactMetadata.model_validate(data)
