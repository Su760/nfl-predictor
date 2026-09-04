from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from .common import UtcModel
from .enums import Origin, ProvenanceGrade, SnapshotStatus


class CaptureManifest(UtcModel):
    capture_id: str
    run_id: str
    source: str
    request_fingerprint: str
    request_started_at_utc: datetime
    response_received_at_utc: datetime
    http_status: int
    raw_path: str
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_headers_allowlisted: dict[str, str]
    source_snapshot_at_utc: datetime | None = None
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str


class NormalizedFact(UtcModel):
    fact_id: str
    capture_id: str
    input_capture_ids: tuple[str, ...] = ()
    fact_type: str
    entity_keys: dict[str, str]
    payload: dict[str, Any]
    provider_record_id: str | None = None
    raw_pointer: str
    observed_at_utc: datetime | None = None
    available_at_utc: datetime
    captured_at_utc: datetime
    archive_published_at_utc: datetime | None = None
    provenance_grade: ProvenanceGrade
    normalization_schema_version: str
    fact_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_valid_capture_lineage(self) -> "NormalizedFact":
        if not self.input_capture_ids:
            return self
        if (
            any(not capture_id.strip() for capture_id in self.input_capture_ids)
            or len(set(self.input_capture_ids)) != len(self.input_capture_ids)
            or self.capture_id not in self.input_capture_ids
        ):
            raise ValueError(
                "fact input capture IDs must be non-blank, unique, and include capture ID"
            )
        return self

    @property
    def lineage_capture_ids(self) -> tuple[str, ...]:
        return self.input_capture_ids or (self.capture_id,)


class FeatureSnapshot(UtcModel):
    snapshot_id: str
    canonical_event_id: str
    event_version: int
    origin: Origin
    decision_at_utc: datetime
    feature_policy_version: str
    feature_schema_version: str
    values: dict[str, Any]
    input_manifest_ids: list[str]
    input_fact_ids: list[str]
    join_policy_version: str
    provenance_grade: ProvenanceGrade
    feature_vector_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: SnapshotStatus
    reason_codes: list[str]
