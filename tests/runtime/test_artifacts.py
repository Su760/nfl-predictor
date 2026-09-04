from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

import nfl_predictor.runtime.artifacts as runtime_artifacts
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade, SnapshotStatus
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.lineage import FeatureSnapshot
from nfl_predictor.features.schema import FEATURE_SCHEMA_V1
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.models.artifacts import ArtifactIntegrityError, ArtifactMetadata, ArtifactStore
from nfl_predictor.models.tie import TieLayer
from nfl_predictor.runtime.artifacts import (
    FrozenForecastArtifact,
    VerifiedArtifactRegistry,
    VerifiedForecastPredictor,
)
from nfl_predictor.workflows.forecast import ForecastExecutionContext

NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Model:
    expected: tuple[float, ...]

    def predict_r_home(self, features: np.ndarray) -> np.ndarray:
        assert features.dtype == np.float64
        assert features.shape == (1, len(FEATURE_SCHEMA_V1))
        assert tuple(features[0]) == self.expected
        return np.asarray([0.6], dtype=np.float64)


@dataclass(frozen=True)
class _Calibrator:
    def transform(self, values: list[float]) -> np.ndarray:
        assert values == [0.6]
        return np.asarray([0.6], dtype=np.float64)


def _metadata(artifact_id: str = "champion-v1", **changes: object) -> ArtifactMetadata:
    values: dict[str, object] = {
        "artifact_id": artifact_id,
        "model_family": "test-model",
        "calibrator_family": "test-calibrator",
        "origin": Origin.T60,
        "model_lane": "football_only",
        "feature_schema_version": "schema-v1",
        "feature_policy_version": "features-v1",
        "model_policy_version": "model-v1",
        "calibration_policy_version": "calibration-v1",
        "split_policy_version": "split-v1",
        "input_manifest_sha256s": ("a" * 64,),
        "training_event_ids": ("training-1",),
        "calibration_event_ids": ("calibration-1",),
        "training_cutoff_at_utc": NOW - timedelta(days=8),
        "calibration_cutoff_at_utc": NOW - timedelta(days=7),
        "code_sha": "c" * 40,
        "dependency_lock_sha256": "d" * 64,
        "seeds": {"model": 1},
        "fold_ledger_path": "folds/ledger.json",
        "fold_ledger_sha256": "e" * 64,
        "metrics": {"brier": 0.2},
        "python_version": "3.11.13",
    }
    values.update(changes)
    return ArtifactMetadata.model_validate(values)


def _entry(
    artifact_id: str = "champion-v1", *, root: Path, **changes: object
) -> dict[str, object]:
    store = ArtifactStore(root)
    values: dict[str, object] = {
        "artifact_id": artifact_id,
        "origin": "T60",
        "model_role": "champion",
        "model_lane": "football_only",
        "feature_schema_version": "schema-v1",
        "feature_policy_version": "features-v1",
        "policy_versions": {"model": "model-v1", "calibration": "calibration-v1"},
        "market_policy_version": "odds-v1",
        "candidate_policy_version": "candidate-v1",
        "code_sha": "c" * 40,
        "dependency_lock_sha256": "d" * 64,
        "marker_sha256": _sha(store.marker_path(artifact_id)),
        "metadata_sha256": _sha(store.metadata_path(artifact_id)),
        "payload_sha256": _sha(store.payload_path(artifact_id)),
    }
    values.update(changes)
    return values


def _write_registry(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"entries": entries}, sort_keys=True), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def artifact_fixture(tmp_path: Path):
    private_root = tmp_path / "private"
    root = private_root / "artifacts"
    registry_path = private_root / "config" / "artifact-registry.json"
    registry_path.parent.mkdir(parents=True)
    expected = tuple(float(index) for index, _ in enumerate(FEATURE_SCHEMA_V1))
    ArtifactStore(root).save(
        FrozenForecastArtifact(_Model(expected), _Calibrator(), "calibrator-v1", TieLayer(0.02)),
        _metadata(),
    )
    _write_registry(registry_path, [_entry(root=root)])
    expected_registry_sha256 = _sha(registry_path)
    registry = VerifiedArtifactRegistry.from_private_config(
        private_root, root, registry_path, expected_registry_sha256
    )
    return private_root, root, registry_path, registry, expected, expected_registry_sha256


# Catches: registry entries becoming trusted without pinned bytes, committed payload verification,
# or exactly one frozen champion for the requested origin.
def test_registry_returns_one_verified_frozen_champion_per_origin(artifact_fixture) -> None:
    _, _, _, registry, _, _ = artifact_fixture

    bindings = registry.for_origin(Origin.T60)

    assert [item.model_role for item in bindings].count("champion") == 1
    assert all(item.frozen and item.verified for item in bindings)
    assert bindings[0].expected_calibrator_artifact_id == "calibrator-v1"


# Catches: a private registry being changed after its reviewed SHA was bound to the runtime.
def test_registry_rejects_registry_tamper(artifact_fixture) -> None:
    _, _, registry_path, registry, _, _ = artifact_fixture
    registry_path.write_bytes(b"{}")

    with pytest.raises(ArtifactIntegrityError, match="registry hash"):
        registry.for_origin(Origin.T60)


# Catches: registry duplication silently selecting an arbitrary champion rather than failing closed.
def test_registry_rejects_multiple_champions_for_one_origin(artifact_fixture) -> None:
    private_root, root, registry_path, _, _, _ = artifact_fixture
    _write_registry(registry_path, [_entry(root=root), _entry(root=root)])
    registry = VerifiedArtifactRegistry.from_private_config(
        private_root, root, registry_path, _sha(registry_path)
    )

    with pytest.raises(ArtifactIntegrityError, match="exactly one champion"):
        registry.for_origin(Origin.T60)


# Catches: permissive metadata/entry comparison accepting unsafe production lineage.
@pytest.mark.parametrize(
    ("entry_change", "metadata_change"),
    [
        ({"origin": "T72"}, {}),
        ({"code_sha": "f" * 40}, {}),
        ({"dependency_lock_sha256": "f" * 64}, {}),
        ({"feature_policy_version": "other"}, {}),
        ({"feature_schema_version": "other"}, {}),
        ({"model_role": "fallback"}, {}),
        ({}, {"origin": Origin.T72}),
    ],
)
def test_registry_rejects_metadata_or_role_mismatch(
    tmp_path: Path, entry_change: dict[str, object], metadata_change: dict[str, object]
) -> None:
    root = tmp_path / "artifacts"
    registry_path = tmp_path / "artifact-registry.json"
    ArtifactStore(root).save(
        FrozenForecastArtifact(_Model(tuple(float(i) for i in range(42))), _Calibrator(), "cal", TieLayer(0.02)),
        _metadata(**metadata_change),
    )
    _write_registry(registry_path, [_entry(root=root, **entry_change)])

    with pytest.raises(ArtifactIntegrityError):
        VerifiedArtifactRegistry.from_private_config(
            tmp_path, root, registry_path, _sha(registry_path)
        ).for_origin(Origin.T60)


# Catches: loaded payloads that do not expose the deterministic model/calibrator/tie bundle.
def test_registry_rejects_uncommitted_or_wrong_payload_type(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    registry_path = tmp_path / "artifact-registry.json"
    store = ArtifactStore(root)
    store.save(_Model(tuple(float(i) for i in range(42))), _metadata())
    _write_registry(registry_path, [_entry(root=root)])

    with pytest.raises(ArtifactIntegrityError, match="FrozenForecastArtifact"):
        VerifiedArtifactRegistry.from_private_config(
            tmp_path, root, registry_path, _sha(registry_path)
        ).for_origin(Origin.T60)
    store.marker_path("champion-v1").unlink()
    with pytest.raises(ArtifactIntegrityError, match="not committed"):
        VerifiedArtifactRegistry.from_private_config(
            tmp_path, root, registry_path, _sha(registry_path)
        ).for_origin(Origin.T60)


# Catches: self-pinning a changed registry rather than requiring the independently reviewed digest.
def test_registry_rejects_independent_registry_digest_mismatch(artifact_fixture) -> None:
    private_root, root, registry_path, _, _, _ = artifact_fixture

    registry = VerifiedArtifactRegistry.from_private_config(
        private_root, root, registry_path, "0" * 64
    )

    with pytest.raises(ArtifactIntegrityError, match="registry hash"):
        registry.for_origin(Origin.T60)


# Catches: code/lock lineage mismatch reaching joblib before the byte preflight rejects it.
@pytest.mark.parametrize("field", ["code_sha", "dependency_lock_sha256"])
def test_registry_rejects_wrong_lineage_before_deserialization(
    artifact_fixture, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    private_root, root, registry_path, _, _, _ = artifact_fixture
    changes = {field: "f" * (40 if field == "code_sha" else 64)}
    _write_registry(registry_path, [_entry(root=root, **changes)])
    registry = VerifiedArtifactRegistry.from_private_config(
        private_root, root, registry_path, _sha(registry_path)
    )
    calls = 0

    def deserialization_must_not_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("joblib must not run before lineage verification")

    monkeypatch.setattr(runtime_artifacts.joblib, "load", deserialization_must_not_run)
    with pytest.raises(ArtifactIntegrityError, match="metadata"):
        registry.for_origin(Origin.T60)
    assert calls == 0


# Catches: coordinated marker/metadata/payload replacement being accepted when only their
# self-consistency, rather than registry-anchored digests, is checked.
def test_registry_rejects_coordinated_artifact_tamper_before_deserialization(
    artifact_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root, root, registry_path, _, _, digest = artifact_fixture
    store = ArtifactStore(root)
    store.payload_path("champion-v1").write_bytes(b"tampered-payload")
    metadata = json.loads(store.metadata_path("champion-v1").read_text(encoding="utf-8"))
    metadata["payload_sha256"] = _sha(store.payload_path("champion-v1"))
    store.metadata_path("champion-v1").write_text(json.dumps(metadata), encoding="utf-8")
    marker = json.loads(store.marker_path("champion-v1").read_text(encoding="utf-8"))
    marker["payload_sha256"] = _sha(store.payload_path("champion-v1"))
    marker["metadata_sha256"] = _sha(store.metadata_path("champion-v1"))
    store.marker_path("champion-v1").write_text(json.dumps(marker), encoding="utf-8")
    registry = VerifiedArtifactRegistry.from_private_config(private_root, root, registry_path, digest)
    monkeypatch.setattr(runtime_artifacts.joblib, "load", lambda *_: pytest.fail("loaded"))

    with pytest.raises(ArtifactIntegrityError, match="frozen registry"):
        registry.for_origin(Origin.T60)


# Catches: registry or artifact roots escaping the explicit trusted private root, including via symlink.
def test_registry_rejects_off_private_root_and_symlink_escape(artifact_fixture, tmp_path: Path) -> None:
    private_root, root, registry_path, _, _, digest = artifact_fixture
    outside = tmp_path / "outside.json"
    outside.write_text(registry_path.read_text(encoding="utf-8"), encoding="utf-8")
    escaped = private_root / "config" / "escaped.json"
    escaped.symlink_to(outside)

    with pytest.raises(ValueError, match="private root"):
        VerifiedArtifactRegistry.from_private_config(private_root, root, outside, digest)
    with pytest.raises(ValueError, match="private root"):
        VerifiedArtifactRegistry.from_private_config(private_root, root, escaped, digest)


def _snapshot(values: dict[str, float]) -> FeatureSnapshot:
    return FeatureSnapshot(
        snapshot_id="snapshot-v1",
        canonical_event_id="event-v1",
        event_version=1,
        origin=Origin.T60,
        decision_at_utc=NOW,
        feature_policy_version="features-v1",
        feature_schema_version="schema-v1",
        values=values,
        input_manifest_ids=["manifest-v1"],
        input_fact_ids=["fact-v1"],
        join_policy_version="asof-v1",
        provenance_grade=ProvenanceGrade.A,
        feature_vector_sha256="a" * 64,
        status=SnapshotStatus.COMPLETE,
        reason_codes=[],
    )


# Catches: predictor schema-order drift, bypassed calibrator/tie layer, or non-canonical lineage.
def test_predictor_uses_schema_order_calibrator_and_tie_layer(artifact_fixture) -> None:
    _, _, _, registry, expected, _ = artifact_fixture
    values = {name: expected[index] for index, (name, _) in enumerate(FEATURE_SCHEMA_V1)}
    event = EventVersion(
        canonical_event_id="event-v1", event_version=1, source_event_ids={"nflverse": "e"},
        season=2026, season_type="REG", week=1, home_team="CHI", away_team="GB",
        kickoff_at_utc=NOW + timedelta(hours=1), neutral_site=False,
        observed_at_utc=NOW - timedelta(days=1), available_at_utc=NOW - timedelta(days=1),
        captured_at_utc=NOW - timedelta(days=1), raw_payload_sha256="b" * 64,
    )
    obligation = OriginObligation(event, ForecastOrigin.for_kickoff(Origin.T60, event.kickoff_at_utc), "forecast-v1")
    binding = registry.for_origin(Origin.T60)[0]

    prediction = VerifiedForecastPredictor(registry).predict(
        _snapshot(dict(reversed(tuple(values.items())))), binding, origin_run_id="run-v1",
        obligation=obligation, created_at_utc=NOW, context=ForecastExecutionContext.live(NOW),
    )

    assert prediction.p_home == Decimal("0.588")
    assert prediction.p_away == Decimal("0.392")
    assert prediction.p_tie == Decimal("0.02")
    assert prediction.p_home + prediction.p_away + prediction.p_tie == 1
    assert prediction.prediction_id == hashlib.sha256(
        b"run-v1|champion-v1|snapshot-v1|features-v1|schema-v1|forecast-v1"
    ).hexdigest()
