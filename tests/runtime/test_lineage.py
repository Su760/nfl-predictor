from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.lineage import CaptureManifest, NormalizedFact
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.runtime.lineage import DurableLineageRepository
from nfl_predictor.storage import DataIntegrityError, schema_for, write_contracts
from nfl_predictor.storage.ledger import canonical_json_bytes


class RuntimeFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.data_root = tmp_path / "private-data"
        self.data_root.mkdir()
        self.lineage = DurableLineageRepository(tmp_path / "lineage", self.data_root)
        self.event_file = self.data_root / "active" / "events.parquet"

    @staticmethod
    def instant() -> datetime:
        return datetime(2026, 9, 1, tzinfo=UTC)

    def event(self) -> EventVersion:
        instant = self.instant()
        return EventVersion(
            canonical_event_id="event-1",
            event_version=1,
            source_event_ids={"nflverse": "source-1"},
            season=2026,
            season_type="REG",
            week=1,
            home_team="SEA",
            away_team="SF",
            kickoff_at_utc=datetime(2026, 9, 7, 20, 20, tzinfo=UTC),
            venue_id="lumen",
            neutral_site=False,
            observed_at_utc=instant,
            available_at_utc=instant,
            captured_at_utc=instant,
            raw_payload_sha256="a" * 64,
        )

    def fact(self) -> NormalizedFact:
        instant = self.instant()
        return NormalizedFact(
            fact_id="fact-1",
            capture_id="capture-1",
            fact_type="team_week",
            entity_keys={"team": "SEA"},
            payload={"rating": 1},
            raw_pointer="raw/fact-1.json",
            available_at_utc=instant,
            captured_at_utc=instant,
            provenance_grade="A",
            normalization_schema_version="fact-v1",
            fact_content_sha256="b" * 64,
        )

    def manifest(self) -> CaptureManifest:
        instant = self.instant()
        return CaptureManifest(
            capture_id="capture-1",
            run_id="attempt-1",
            source="fixture",
            request_fingerprint="request-1",
            request_started_at_utc=instant,
            response_received_at_utc=instant,
            http_status=200,
            raw_path="raw/fixture.bin",
            raw_payload_sha256="c" * 64,
            response_headers_allowlisted={},
            code_sha="d" * 40,
            dependency_lock_sha256="e" * 64,
            schema_version="capture-v1",
        )

    def install_active_events(self) -> Path:
        write_contracts(self.event_file, [self.event()], schema_for(EventVersion))
        event_digest = hashlib.sha256(self.event_file.read_bytes()).hexdigest()
        manifest_path = self.data_root / "active-events.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "active-events-v1",
                    "season": 2026,
                    "files": [
                        {
                            "path": "active/events.parquet",
                            "sha256": event_digest,
                            "events": [
                                {"canonical_event_id": "event-1", "event_version": 1}
                            ],
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return manifest_path

    def rewrite_fact_object_id(self, fact_id: str, replacement: str) -> None:
        marker = self.lineage.ledger.marker_path("normalized-fact-v1", fact_id)
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        digest = marker_payload["content_sha256"]
        object_path = self.lineage.ledger.object_path(digest)
        payload = json.loads(object_path.read_text(encoding="utf-8"))
        payload["fact_id"] = replacement
        replacement_bytes = canonical_json_bytes(payload)
        replacement_digest = hashlib.sha256(replacement_bytes).hexdigest()
        replacement_path = self.lineage.ledger.object_path(replacement_digest)
        replacement_path.parent.mkdir(parents=True, exist_ok=True)
        replacement_path.write_bytes(replacement_bytes)
        marker_payload["content_sha256"] = replacement_digest
        marker_payload["object_path"] = replacement_path.relative_to(self.lineage.ledger.root).as_posix()
        marker.write_bytes(canonical_json_bytes(marker_payload))


@pytest.fixture
def runtime_fixture(tmp_path: Path) -> RuntimeFixture:
    return RuntimeFixture(tmp_path)


def test_active_schedule_hashes_actual_manifest_and_event_bytes(
    runtime_fixture: RuntimeFixture,
) -> None:
    manifest = runtime_fixture.install_active_events()

    events = runtime_fixture.lineage.load_active_events(
        manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()
    )

    assert [event.canonical_event_id for event in events] == ["event-1"]
    assert pq.read_schema(runtime_fixture.event_file).equals(
        schema_for(EventVersion), check_metadata=True
    )
    runtime_fixture.event_file.write_bytes(b"tampered")

    with pytest.raises(DataIntegrityError, match="active event bytes"):
        runtime_fixture.lineage.load_active_events(
            manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()
        )


def test_lineage_resolution_rejects_substituted_logical_ids(
    runtime_fixture: RuntimeFixture,
) -> None:
    fact = runtime_fixture.fact()
    runtime_fixture.lineage.append_facts((fact,))
    runtime_fixture.rewrite_fact_object_id(fact.fact_id, "different-id")

    with pytest.raises(DataIntegrityError, match="logical ID"):
        runtime_fixture.lineage.resolve_facts([fact.fact_id])


def test_lineage_round_trips_typed_records_and_filters_event_facts(
    runtime_fixture: RuntimeFixture,
) -> None:
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact()
    event = runtime_fixture.event()

    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    runtime_fixture.lineage.append_events((event,))
    runtime_fixture.lineage.publish_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    assert runtime_fixture.lineage.resolve_manifests([manifest.capture_id]) == [manifest]
    assert runtime_fixture.lineage.resolve_facts([fact.fact_id]) == [fact]
    assert runtime_fixture.lineage.for_event_features(event, runtime_fixture.instant()) == [fact]


def test_fact_is_invisible_until_capture_batch_marker_commits(
    runtime_fixture: RuntimeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact()
    original_append = runtime_fixture.lineage.ledger.append

    def fail_batch_marker(namespace: str, identifier: str, record: object):
        if namespace == "capture-batch-v1":
            raise OSError("batch marker interrupted")
        return original_append(namespace, identifier, record)

    monkeypatch.setattr(runtime_fixture.lineage.ledger, "append", fail_batch_marker)
    with pytest.raises(OSError, match="batch marker"):
        runtime_fixture.lineage.publish_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    assert list(runtime_fixture.lineage.iter_facts()) == []
    assert runtime_fixture.lineage.for_event_features(event, runtime_fixture.instant()) == []

    monkeypatch.setattr(runtime_fixture.lineage.ledger, "append", original_append)
    runtime_fixture.lineage.publish_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    assert list(runtime_fixture.lineage.iter_facts()) == [fact]
    assert runtime_fixture.lineage.for_event_features(event, runtime_fixture.instant()) == [fact]


def test_publication_validates_model_copy_fact_before_any_marker_and_allows_retry(
    runtime_fixture: RuntimeFixture,
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact()
    invalid = fact.model_copy(
        update={"input_capture_ids": (fact.capture_id, fact.capture_id)}
    )

    with pytest.raises(ValidationError, match="input capture IDs"):
        runtime_fixture.lineage.publish_capture_batch(obligation, "attempt-1", (manifest,), (invalid,))

    assert not runtime_fixture.lineage.ledger.marker_path(
        "capture-manifest-v1", manifest.capture_id
    ).exists()
    assert not runtime_fixture.lineage.ledger.marker_path(
        "normalized-fact-v1", fact.fact_id
    ).exists()
    assert not runtime_fixture.lineage.ledger.marker_path(
        "capture-batch-v1", f"{obligation.idempotency_key}|attempt-1"
    ).exists()

    runtime_fixture.lineage.publish_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    assert list(runtime_fixture.lineage.iter_facts()) == [fact]


def test_capture_batch_requires_every_derived_fact_input_capture_manifest(
    runtime_fixture: RuntimeFixture,
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    manifest = runtime_fixture.manifest().model_copy(update={"capture_id": "pbp-1"})
    derived = runtime_fixture.fact().model_copy(
        update={"capture_id": "pbp-1", "input_capture_ids": ("schedule-1", "pbp-1")}
    )
    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((derived,))

    with pytest.raises(DataIntegrityError, match="lineage capture ID"):
        runtime_fixture.lineage.append_capture_batch(obligation, "attempt-1", (manifest,), (derived,))


def test_capture_batch_persists_exact_obligation_attempt_and_record_ids(
    runtime_fixture: RuntimeFixture,
) -> None:
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact()
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((fact,))

    batch = runtime_fixture.lineage.append_capture_batch(
        obligation, "attempt-1", (manifest,), (fact,)
    )

    assert batch.obligation_id == obligation.idempotency_key
    assert batch.attempt_id == "attempt-1"
    assert batch.manifest_ids == (manifest.capture_id,)
    assert batch.fact_ids == (fact.fact_id,)


def test_replay_rejects_batch_with_manifest_or_fact_after_cutoff_before_appending(
    runtime_fixture: RuntimeFixture,
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    cutoff = runtime_fixture.instant()
    manifest = runtime_fixture.manifest().model_copy(
        update={"response_received_at_utc": cutoff + timedelta(seconds=1)}
    )
    fact = runtime_fixture.fact()
    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((fact,))
    runtime_fixture.lineage.append_capture_batch(obligation, "attempt-1", (manifest,), (fact,))
    marker_paths_before = sorted(runtime_fixture.lineage.ledger.root.rglob("*.json"))

    with pytest.raises(DataIntegrityError, match="no archived capture batch"):
        runtime_fixture.lineage.replay_capture_batch(obligation, "replay-1", cutoff)

    assert sorted(runtime_fixture.lineage.ledger.root.rglob("*.json")) == marker_paths_before


def test_replay_rejects_batch_with_fact_timestamp_after_cutoff_before_appending(
    runtime_fixture: RuntimeFixture,
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    cutoff = runtime_fixture.instant()
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact().model_copy(
        update={"available_at_utc": cutoff + timedelta(seconds=1)}
    )
    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((fact,))
    runtime_fixture.lineage.append_capture_batch(obligation, "attempt-1", (manifest,), (fact,))
    marker_paths_before = sorted(runtime_fixture.lineage.ledger.root.rglob("*.json"))

    with pytest.raises(DataIntegrityError, match="no archived capture batch"):
        runtime_fixture.lineage.replay_capture_batch(obligation, "replay-1", cutoff)

    assert sorted(runtime_fixture.lineage.ledger.root.rglob("*.json")) == marker_paths_before


@pytest.mark.parametrize("grade", [ProvenanceGrade.B, ProvenanceGrade.C])
def test_replay_accepts_source_snapshot_and_archived_fact_at_cutoff(
    runtime_fixture: RuntimeFixture, grade: ProvenanceGrade
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    cutoff = runtime_fixture.instant()
    manifest = runtime_fixture.manifest().model_copy(
        update={
            "response_received_at_utc": cutoff + timedelta(seconds=1),
            "source_snapshot_at_utc": cutoff,
        }
    )
    fact = runtime_fixture.fact().model_copy(
        update={
            "provenance_grade": grade,
            "observed_at_utc": cutoff + timedelta(seconds=1),
            "captured_at_utc": cutoff + timedelta(seconds=1),
            "archive_published_at_utc": cutoff,
        }
    )
    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((fact,))
    runtime_fixture.lineage.append_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    replay = runtime_fixture.lineage.replay_capture_batch(obligation, "replay-1", cutoff)

    assert len(replay.records) == 2
    assert all(
        record.run_id == "replay-1" for record in replay.records if isinstance(record, CaptureManifest)
    )
    assert all(
        record.provenance_grade is ProvenanceGrade.C
        for record in replay.records
        if isinstance(record, NormalizedFact)
    )


@pytest.mark.parametrize("archive_published_at_utc", [None, "late"])
def test_replay_rejects_grade_b_fact_without_cutoff_safe_archive_publication(
    runtime_fixture: RuntimeFixture, archive_published_at_utc: datetime | str | None
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    cutoff = runtime_fixture.instant()
    archive = (
        cutoff + timedelta(seconds=1)
        if archive_published_at_utc == "late"
        else archive_published_at_utc
    )
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact().model_copy(
        update={
            "provenance_grade": ProvenanceGrade.B,
            "archive_published_at_utc": archive,
        }
    )
    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((fact,))
    runtime_fixture.lineage.append_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    with pytest.raises(DataIntegrityError, match="no archived capture batch"):
        runtime_fixture.lineage.replay_capture_batch(obligation, "replay-1", cutoff)


def test_capture_batch_rejects_unpersisted_incoherent_and_substituted_records(
    runtime_fixture: RuntimeFixture,
) -> None:
    event = runtime_fixture.event()
    obligation = OriginObligation(
        event=event,
        window=event_forecast_origin(event),
        policy_version="forecast-v1",
    )
    manifest = runtime_fixture.manifest()
    fact = runtime_fixture.fact()

    with pytest.raises(DataIntegrityError, match="persisted"):
        runtime_fixture.lineage.append_capture_batch(obligation, "attempt-1", (manifest,), (fact,))

    runtime_fixture.lineage.append_manifests((manifest,))
    runtime_fixture.lineage.append_facts((fact,))

    with pytest.raises(DataIntegrityError, match="run ID"):
        runtime_fixture.lineage.append_capture_batch(
            obligation, "attempt-1", (manifest.model_copy(update={"run_id": "other"}),), (fact,)
        )
    with pytest.raises(DataIntegrityError, match="capture ID"):
        runtime_fixture.lineage.append_capture_batch(
            obligation,
            "attempt-1",
            (manifest,),
            (fact.model_copy(update={"capture_id": "other"}),),
        )
    with pytest.raises(DataIntegrityError, match="persisted"):
        runtime_fixture.lineage.append_capture_batch(
            obligation,
            "attempt-1",
            (manifest.model_copy(update={"source": "other"}),),
            (fact,),
        )
    with pytest.raises(DataIntegrityError, match="unique"):
        runtime_fixture.lineage.append_capture_batch(
            obligation, "attempt-1", (manifest, manifest), (fact,)
        )


def test_active_events_parse_verified_parquet_bytes_without_reopening_path(
    runtime_fixture: RuntimeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = runtime_fixture.install_active_events()

    def reopened_path(*args: object, **kwargs: object) -> None:
        raise AssertionError("active-event parquet path was reopened")

    monkeypatch.setattr(pq, "read_schema", reopened_path)
    monkeypatch.setattr(pq, "read_table", reopened_path)

    events = runtime_fixture.lineage.load_active_events(
        manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()
    )

    assert [event.canonical_event_id for event in events] == ["event-1"]


def test_active_events_reject_unknown_manifest_schema_version(
    runtime_fixture: RuntimeFixture,
) -> None:
    manifest = runtime_fixture.install_active_events()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schema_version"] = "future-active-events-v2"
    manifest.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(DataIntegrityError, match="schema version"):
        runtime_fixture.lineage.load_active_events(
            manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()
        )


def event_forecast_origin(event: EventVersion) -> ForecastOrigin:
    return ForecastOrigin.for_kickoff(Origin.T72, event.kickoff_at_utc)
