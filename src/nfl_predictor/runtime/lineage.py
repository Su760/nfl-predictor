from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from nfl_predictor.config import AppConfig
from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import CaptureManifest, NormalizedFact
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.storage import (
    DataIntegrityError,
    DuckDbPointInTimeStore,
    LedgerStore,
    schema_for,
)
from nfl_predictor.storage.ledger import canonical_json_bytes
from nfl_predictor.workflows.forecast import CaptureBundle

_CAPTURE_MANIFEST_NAMESPACE = "capture-manifest-v1"
_NORMALIZED_FACT_NAMESPACE = "normalized-fact-v1"
_EVENT_VERSION_NAMESPACE = "event-version-v1"
_CAPTURE_BATCH_NAMESPACE = "capture-batch-v1"
_ACTIVE_EVENT_MANIFEST_FIELDS = {"schema_version", "season", "files"}
_ACTIVE_EVENT_FILE_FIELDS = {"path", "sha256", "events"}
_ACTIVE_EVENT_KEY_FIELDS = {"canonical_event_id", "event_version"}
Contract = TypeVar("Contract", bound=BaseModel)


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    lineage_root: Path
    prospective_forecast_root: Path
    replay_forecast_root: Path
    outcome_report_root: Path
    artifact_root: Path

    @classmethod
    def from_config(cls, config: AppConfig) -> RuntimePaths:
        data_root = config.data_root.resolve(strict=True)
        code_root = config.code_root.resolve(strict=True)
        if data_root == code_root or code_root in data_root.parents:
            raise ValueError("private data_root must be outside code_root")
        return cls(
            data_root=data_root,
            lineage_root=data_root / "lineage",
            prospective_forecast_root=data_root / "forecast" / "prospective",
            replay_forecast_root=data_root / "forecast" / "replay",
            outcome_report_root=data_root / "outcomes-and-reports",
            artifact_root=data_root / "artifacts",
        )


class CaptureBatch(BaseModel):
    """Immutable capture lineage keyed by obligation and capture attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    obligation_id: str
    attempt_id: str
    manifest_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]

    @model_validator(mode="after")
    def require_exact_identifiers(self) -> CaptureBatch:
        values = (
            self.obligation_id,
            self.attempt_id,
            *self.manifest_ids,
            *self.fact_ids,
        )
        if any(not value.strip() for value in values):
            raise ValueError("capture batch identifiers must be non-blank")
        if len(set(self.manifest_ids)) != len(self.manifest_ids):
            raise ValueError("capture batch manifest IDs must be unique")
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("capture batch fact IDs must be unique")
        return self


class DurableLineageRepository:
    def __init__(self, lineage_root: Path, data_root: Path) -> None:
        self.ledger = LedgerStore(lineage_root)
        self.data_root = data_root.resolve(strict=True)

    def append_manifests(self, manifests: Sequence[CaptureManifest]) -> None:
        for manifest in manifests:
            self.ledger.append(_CAPTURE_MANIFEST_NAMESPACE, manifest.capture_id, manifest)

    def append_facts(self, facts: Sequence[NormalizedFact]) -> None:
        for fact in facts:
            self.ledger.append(_NORMALIZED_FACT_NAMESPACE, fact.fact_id, fact)

    def append_events(self, events: Sequence[EventVersion]) -> None:
        for event in events:
            self.ledger.append(_EVENT_VERSION_NAMESPACE, self._event_key(event), event)

    def publish_capture_batch(
        self,
        obligation: OriginObligation,
        attempt_id: str,
        manifests: Sequence[CaptureManifest],
        facts: Sequence[NormalizedFact],
    ) -> CaptureBatch:
        """Publish capture records atomically at the batch-marker visibility boundary."""
        manifest_items = tuple(
            CaptureManifest.model_validate(manifest.model_dump()) for manifest in manifests
        )
        fact_items = tuple(
            NormalizedFact.model_validate(fact.model_dump()) for fact in facts
        )
        self._validated_capture_batch(obligation, attempt_id, manifest_items, fact_items)
        self.append_manifests(manifest_items)
        self.append_facts(fact_items)
        return self.append_capture_batch(obligation, attempt_id, manifest_items, fact_items)

    def append_capture_batch(
        self,
        obligation: OriginObligation,
        attempt_id: str,
        manifests: Sequence[CaptureManifest],
        facts: Sequence[NormalizedFact],
    ) -> CaptureBatch:
        manifest_items = tuple(manifests)
        fact_items = tuple(facts)
        batch = self._validated_capture_batch(obligation, attempt_id, manifest_items, fact_items)
        manifest_ids = batch.manifest_ids
        fact_ids = batch.fact_ids
        try:
            persisted_manifests = tuple(self.resolve_manifests(list(manifest_ids)))
            persisted_facts = tuple(self.resolve_facts(list(fact_ids)))
        except DataIntegrityError as error:
            raise DataIntegrityError("capture batch records must already be persisted") from error
        if persisted_manifests != manifest_items or persisted_facts != fact_items:
            raise DataIntegrityError("capture batch records differ from persisted lineage")
        self.ledger.append(_CAPTURE_BATCH_NAMESPACE, self._batch_key(batch), batch)
        return batch

    @staticmethod
    def _validated_capture_batch(
        obligation: OriginObligation,
        attempt_id: str,
        manifests: Sequence[CaptureManifest],
        facts: Sequence[NormalizedFact],
    ) -> CaptureBatch:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise DataIntegrityError("capture batch attempt ID must be non-blank")
        manifest_ids = tuple(manifest.capture_id for manifest in manifests)
        fact_ids = tuple(fact.fact_id for fact in facts)
        if len(set(manifest_ids)) != len(manifest_ids) or len(set(fact_ids)) != len(fact_ids):
            raise DataIntegrityError("capture batch record IDs must be unique")
        if any(manifest.run_id != attempt_id for manifest in manifests):
            raise DataIntegrityError("capture batch manifest run ID does not match attempt")
        manifest_id_set = set(manifest_ids)
        if any(
            not set(fact.lineage_capture_ids).issubset(manifest_id_set) for fact in facts
        ):
            raise DataIntegrityError("capture batch fact lineage capture ID is not in its manifests")
        return CaptureBatch(
            obligation_id=obligation.idempotency_key,
            attempt_id=attempt_id,
            manifest_ids=manifest_ids,
            fact_ids=fact_ids,
        )

    def replay_capture_batch(
        self, obligation: OriginObligation, attempt_id: str, cutoff: datetime
    ) -> CaptureBundle:
        candidates: list[tuple[CaptureBatch, list[CaptureManifest], list[NormalizedFact]]] = []
        for batch in self._iter_batches():
            if batch.obligation_id != obligation.idempotency_key:
                continue
            manifests = self.resolve_manifests(list(batch.manifest_ids))
            facts = self.resolve_facts(list(batch.fact_ids))
            if not self._coherent_batch(batch, manifests, facts):
                raise DataIntegrityError("archived capture batch lineage is incoherent")
            if self._cutoff_safe(manifests, facts, cutoff):
                candidates.append((batch, manifests, facts))
        if not candidates:
            raise DataIntegrityError("no archived capture batch exists at the requested cutoff")
        _, manifests, facts = max(
            candidates,
            key=lambda item: (
                max(manifest.response_received_at_utc for manifest in item[1]),
                item[0].attempt_id,
            ),
        )
        reconstructed_manifests = tuple(
            manifest.model_copy(
                update={
                    "capture_id": self._replay_capture_id(manifest.capture_id, attempt_id),
                    "run_id": attempt_id,
                }
            )
            for manifest in manifests
        )
        capture_ids = {
            source.capture_id: reconstructed.capture_id
            for source, reconstructed in zip(manifests, reconstructed_manifests, strict=True)
        }
        reconstructed_facts = tuple(
            self._replay_fact(fact, capture_ids, attempt_id) for fact in facts
        )
        self.append_manifests(reconstructed_manifests)
        self.append_facts(reconstructed_facts)
        self.append_capture_batch(obligation, attempt_id, reconstructed_manifests, reconstructed_facts)
        return CaptureBundle(
            records=(*reconstructed_manifests, *reconstructed_facts),
            payload={"fact_ids": [fact.fact_id for fact in reconstructed_facts]},
        )

    def resolve_manifests(self, ids: list[str]) -> list[CaptureManifest]:
        return [self._manifest(identifier) for identifier in ids]

    def resolve_facts(self, ids: list[str]) -> list[NormalizedFact]:
        return [self._fact(identifier) for identifier in ids]

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        return DuckDbPointInTimeStore(list(self.iter_facts())).for_event_features(event, cutoff)

    def iter_facts(self) -> Iterator[NormalizedFact]:
        yielded_fact_ids: set[str] = set()
        for batch in self._iter_batches():
            manifests = self.resolve_manifests(list(batch.manifest_ids))
            facts = self.resolve_facts(list(batch.fact_ids))
            if not self._coherent_batch(batch, manifests, facts):
                raise DataIntegrityError("committed capture batch lineage is incoherent")
            for fact in facts:
                if fact.fact_id not in yielded_fact_ids:
                    yielded_fact_ids.add(fact.fact_id)
                    yield fact

    def load_active_events(self, manifest_path: Path, expected_sha256: str) -> list[EventVersion]:
        manifest_bytes = self._verified_bytes(
            manifest_path, expected_sha256, "active event manifest bytes"
        )
        manifest = self._active_manifest(manifest_bytes)
        season = manifest["season"]
        files = manifest["files"]
        assert isinstance(season, int)
        assert isinstance(files, list)
        events: list[EventVersion] = []
        for file_entry in files:
            path, digest, event_keys = self._active_file_entry(file_entry)
            event_path = self._safe_data_path(path)
            event_bytes = self._verified_bytes(event_path, digest, "active event bytes")
            try:
                parquet = pq.ParquetFile(BytesIO(event_bytes))
                schema = parquet.schema_arrow
                rows = parquet.read().to_pylist()
            except Exception as error:
                raise DataIntegrityError("active event Parquet schema is unreadable") from error
            if not schema.equals(schema_for(EventVersion), check_metadata=True):
                raise DataIntegrityError("active event Parquet schema does not match EventVersion V1")
            try:
                file_events = [self._event_from_parquet_row(row) for row in rows]
            except Exception as error:
                raise DataIntegrityError("active event Parquet records are invalid") from error
            actual_keys = [self._event_key(event) for event in file_events]
            if actual_keys != event_keys:
                raise DataIntegrityError("active event keys do not match declared file order")
            if any(event.season != season for event in file_events):
                raise DataIntegrityError("active event season does not match manifest")
            events.extend(file_events)
        versions = [(event.canonical_event_id, event.event_version) for event in events]
        if len(set(versions)) != len(versions):
            raise DataIntegrityError("active event versions are duplicated")
        canonical_ids = [event.canonical_event_id for event in events]
        if len(set(canonical_ids)) != len(canonical_ids):
            raise DataIntegrityError("active event manifest contains more than one version per event")
        return events

    def _manifest(self, identifier: str) -> CaptureManifest:
        record = self.ledger.read(_CAPTURE_MANIFEST_NAMESPACE, identifier)
        return self._typed_record(record, CaptureManifest, "capture_id", identifier)

    def _fact(self, identifier: str) -> NormalizedFact:
        record = self.ledger.read(_NORMALIZED_FACT_NAMESPACE, identifier)
        return self._typed_record(record, NormalizedFact, "fact_id", identifier)

    def _iter_batches(self) -> Iterator[CaptureBatch]:
        for identifier in self._namespace_keys(_CAPTURE_BATCH_NAMESPACE):
            record = self.ledger.read(_CAPTURE_BATCH_NAMESPACE, identifier)
            batch = self._typed_record(record, CaptureBatch, None, identifier)
            if self._batch_key(batch) != identifier:
                raise DataIntegrityError("capture batch logical ID does not match lookup key")
            yield batch

    def _namespace_keys(self, namespace: str) -> Iterator[str]:
        marker_root = self.ledger.root / "commits" / namespace
        if not marker_root.exists():
            return
        if not marker_root.is_dir():
            raise DataIntegrityError("lineage marker namespace is not a directory")
        for marker in sorted(marker_root.glob("*.json")):
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DataIntegrityError("lineage marker is unreadable") from error
            if not isinstance(value, dict) or not isinstance(value.get("idempotency_key"), str):
                raise DataIntegrityError("lineage marker identity is invalid")
            identifier = value["idempotency_key"]
            if marker != self.ledger.marker_path(namespace, identifier):
                raise DataIntegrityError("lineage marker path does not match its logical ID")
            yield identifier

    @staticmethod
    def _typed_record(
        value: dict[str, Any] | None,
        contract: type[Contract],
        identifier_field: str | None,
        identifier: str,
    ) -> Contract:
        if value is None:
            raise DataIntegrityError("lineage record is missing")
        try:
            record = contract.model_validate(value)
        except ValidationError as error:
            raise DataIntegrityError("lineage record does not match its contract") from error
        if identifier_field is not None and getattr(record, identifier_field) != identifier:
            raise DataIntegrityError("lineage record logical ID does not match lookup key")
        return record

    @staticmethod
    def _event_key(event: EventVersion) -> str:
        return f"{event.canonical_event_id}:{event.event_version}"

    @staticmethod
    def _batch_key(batch: CaptureBatch) -> str:
        return f"{batch.obligation_id}|{batch.attempt_id}"

    @staticmethod
    def _coherent_batch(
        batch: CaptureBatch,
        manifests: Sequence[CaptureManifest],
        facts: Sequence[NormalizedFact],
    ) -> bool:
        manifest_ids = tuple(manifest.capture_id for manifest in manifests)
        fact_ids = tuple(fact.fact_id for fact in facts)
        return (
            tuple(batch.manifest_ids) == manifest_ids
            and tuple(batch.fact_ids) == fact_ids
            and len(set(manifest_ids)) == len(manifest_ids)
            and len(set(fact_ids)) == len(fact_ids)
            and all(manifest.run_id == batch.attempt_id for manifest in manifests)
            and all(
                set(fact.lineage_capture_ids).issubset(set(manifest_ids)) for fact in facts
            )
        )

    @staticmethod
    def _cutoff_safe(
        manifests: Sequence[CaptureManifest], facts: Sequence[NormalizedFact], cutoff: datetime
    ) -> bool:
        for manifest in manifests:
            if not (
                manifest.response_received_at_utc <= cutoff
                or (
                    manifest.source_snapshot_at_utc is not None
                    and manifest.source_snapshot_at_utc <= cutoff
                )
            ):
                return False
        for fact in facts:
            if fact.available_at_utc > cutoff:
                return False
            if fact.provenance_grade is ProvenanceGrade.A:
                if fact.captured_at_utc > cutoff:
                    return False
            elif fact.provenance_grade in {ProvenanceGrade.B, ProvenanceGrade.C}:
                if (
                    fact.archive_published_at_utc is None
                    or fact.archive_published_at_utc > cutoff
                ):
                    return False
            else:
                return False
        return True

    @staticmethod
    def _replay_capture_id(capture_id: str, attempt_id: str) -> str:
        payload = canonical_json_bytes({"archived_capture_id": capture_id, "attempt_id": attempt_id})
        return f"replay-{hashlib.sha256(payload).hexdigest()}"

    @staticmethod
    def _replay_fact(
        fact: NormalizedFact, capture_ids: dict[str, str], attempt_id: str
    ) -> NormalizedFact:
        capture_id = capture_ids[fact.capture_id]
        input_capture_ids = (
            tuple(capture_ids[item] for item in fact.input_capture_ids)
            if fact.input_capture_ids
            else ()
        )
        identity = canonical_json_bytes(
            {
                "archived_fact_id": fact.fact_id,
                "attempt_id": attempt_id,
                "reason": "archived-capture-batch-v1",
            }
        )
        fact_id = f"replay-{hashlib.sha256(identity).hexdigest()}"
        content = canonical_json_bytes(
            {
                "archived_fact_id": fact.fact_id,
                "capture_id": capture_id,
                "input_capture_ids": list(input_capture_ids),
                "payload": fact.payload,
                "reason": "archived-capture-batch-v1",
            }
        )
        return fact.model_copy(
            update={
                "fact_id": fact_id,
                "capture_id": capture_id,
                "input_capture_ids": input_capture_ids,
                "provenance_grade": ProvenanceGrade.C,
                "archive_published_at_utc": fact.archive_published_at_utc or fact.captured_at_utc,
                "fact_content_sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    @staticmethod
    def _verified_bytes(path: Path, expected_sha256: str, label: str) -> bytes:
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise DataIntegrityError(f"{label} expected SHA-256 is invalid")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise DataIntegrityError(f"{label} are unavailable") from error
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise DataIntegrityError(f"{label} do not match expected SHA-256")
        return payload

    @staticmethod
    def _active_manifest(payload: bytes) -> dict[str, object]:
        try:
            manifest = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DataIntegrityError("active event manifest is not valid JSON") from error
        if not isinstance(manifest, dict) or set(manifest) != _ACTIVE_EVENT_MANIFEST_FIELDS:
            raise DataIntegrityError("active event manifest fields are invalid")
        if manifest["schema_version"] != "active-events-v1":
            raise DataIntegrityError("active event manifest schema version is invalid")
        if not isinstance(manifest["season"], int) or isinstance(manifest["season"], bool):
            raise DataIntegrityError("active event manifest season is invalid")
        if not isinstance(manifest["files"], list):
            raise DataIntegrityError("active event manifest files are invalid")
        return manifest

    @staticmethod
    def _active_file_entry(value: object) -> tuple[str, str, list[str]]:
        if not isinstance(value, dict) or set(value) != _ACTIVE_EVENT_FILE_FIELDS:
            raise DataIntegrityError("active event file fields are invalid")
        path = value["path"]
        digest = value["sha256"]
        keys = value["events"]
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(keys, list):
            raise DataIntegrityError("active event file values are invalid")
        event_keys: list[str] = []
        for key in keys:
            if not isinstance(key, dict) or set(key) != _ACTIVE_EVENT_KEY_FIELDS:
                raise DataIntegrityError("active event key fields are invalid")
            canonical_event_id = key["canonical_event_id"]
            event_version = key["event_version"]
            if (
                not isinstance(canonical_event_id, str)
                or not canonical_event_id.strip()
                or not isinstance(event_version, int)
                or isinstance(event_version, bool)
                or event_version < 1
            ):
                raise DataIntegrityError("active event key values are invalid")
            event_keys.append(f"{canonical_event_id}:{event_version}")
        if len(set(event_keys)) != len(event_keys):
            raise DataIntegrityError("active event keys are duplicated")
        return path, digest, event_keys

    def _safe_data_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if not relative_path or candidate.is_absolute() or "\x00" in relative_path:
            raise DataIntegrityError("active event path must be safe and relative")
        resolved = (self.data_root / candidate).resolve()
        if self.data_root != resolved and self.data_root not in resolved.parents:
            raise DataIntegrityError("active event path escapes data_root")
        return resolved

    @staticmethod
    def _event_from_parquet_row(row: object) -> EventVersion:
        if not isinstance(row, dict):
            raise DataIntegrityError("active event row is invalid")
        source_event_ids = row.get("source_event_ids")
        if not isinstance(source_event_ids, list):
            raise DataIntegrityError("active event source IDs are invalid")
        row = dict(row)
        try:
            row["source_event_ids"] = {
                item["key"]: item["value"]
                for item in source_event_ids
                if isinstance(item, dict)
                and isinstance(item.get("key"), str)
                and isinstance(item.get("value"), str)
            }
            if len(row["source_event_ids"]) != len(source_event_ids):
                raise ValueError("source event IDs contain invalid pairs")
            return EventVersion.model_validate(row)
        except (TypeError, ValidationError, ValueError) as error:
            raise DataIntegrityError("active event row does not match EventVersion") from error
