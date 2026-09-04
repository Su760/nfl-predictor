import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel

from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import OriginRun, Prediction
from nfl_predictor.contracts.lineage import CaptureManifest, FeatureSnapshot, NormalizedFact
from nfl_predictor.contracts.markets import (
    ActualTicket,
    DisplayedQuoteCandidate,
    MarketComparator,
    MoneylineQuote,
)
from nfl_predictor.contracts.outcomes import Outcome, Settlement
from nfl_predictor.storage.layout import StorageLayout
from nfl_predictor.storage.ledger import (
    AppendResult,
    DataIntegrityError,
    IdempotencyConflict,
    LedgerStore,
)
from nfl_predictor.storage.parquet import CONTRACT_SCHEMAS_V1, schema_for, write_contracts


def test_append_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    first = store.append("origin_runs", "game:T72:v1", {"status": "COMPLETE"})
    duplicate = store.append("origin_runs", "game:T72:v1", {"status": "COMPLETE"})

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.content_sha256 == first.content_sha256
    assert store.read("origin_runs", "game:T72:v1") == {"status": "COMPLETE"}

    with pytest.raises(IdempotencyConflict):
        store.append("origin_runs", "game:T72:v1", {"status": "FAILED"})


def test_uncommitted_object_is_not_visible(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    orphan = store.object_path("f" * 64)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"interrupted")

    assert store.read("origin_runs", "missing") is None


def test_read_fails_closed_when_committed_object_hash_does_not_match(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    result = store.append("origin_runs", "game:T72:v1", {"status": "COMPLETE"})
    store.object_path(result.content_sha256).write_bytes(b'{"status":"TAMPERED"}')

    with pytest.raises(DataIntegrityError, match="content hash"):
        store.read("origin_runs", "game:T72:v1")


@pytest.mark.parametrize(
    "unsupported",
    [
        Decimal("1.25"),
        datetime(2026, 9, 7, tzinfo=UTC),
        b"bytes",
        ("tuple",),
        {1: "non-string-key"},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_ledger_rejects_non_json_native_values(tmp_path: Path, unsupported: object) -> None:
    with pytest.raises(TypeError, match="JSON-native"):
        LedgerStore(tmp_path).append("facts", "fact-1", {"payload": unsupported})


def _normalized_fact(payload: dict[str, Any]) -> NormalizedFact:
    instant = datetime(2026, 9, 7, tzinfo=UTC)
    return NormalizedFact(
        fact_id="fact-1",
        capture_id="capture-1",
        fact_type="team_week",
        entity_keys={"team": "SEA"},
        payload=payload,
        raw_pointer="raw/fact-1.json",
        available_at_utc=instant,
        captured_at_utc=instant,
        provenance_grade="A",
        normalization_schema_version="1",
        fact_content_sha256="f" * 64,
    )


def test_normalized_fact_exposes_exact_multi_capture_lineage_and_parquet_round_trip(
    tmp_path: Path,
) -> None:
    derived = _normalized_fact({"rating": 1}).model_copy(
        update={"capture_id": "pbp-1", "input_capture_ids": ("schedule-1", "pbp-1")}
    )

    validated = NormalizedFact.model_validate(derived.model_dump())
    path = tmp_path / "derived-facts.parquet"
    write_contracts(path, [validated], schema_for(NormalizedFact))

    assert validated.lineage_capture_ids == ("schedule-1", "pbp-1")
    assert _normalized_fact({"rating": 1}).lineage_capture_ids == ("capture-1",)
    assert pq.read_table(path).to_pylist()[0]["input_capture_ids"] == ["schedule-1", "pbp-1"]


@pytest.mark.parametrize(
    "input_capture_ids",
    [("schedule-1",), ("pbp-1", "pbp-1"), ("schedule-1", "schedule-1"), ("schedule-1", " ")],
)
def test_normalized_fact_requires_valid_explicit_capture_lineage(
    input_capture_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        NormalizedFact.model_validate(
            _normalized_fact({"rating": 1})
            .model_copy(update={"capture_id": "pbp-1", "input_capture_ids": input_capture_ids})
            .model_dump()
        )


def test_ledger_rejects_non_json_native_value_inside_pydantic_payload(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="JSON-native"):
        LedgerStore(tmp_path).append(
            "facts",
            "fact-1",
            _normalized_fact({"value": Decimal("1.25")}),
        )


def test_ledger_preserves_nested_json_native_values_and_types(tmp_path: Path) -> None:
    payload = {
        "none": None,
        "bool": True,
        "int": 1,
        "float": 1.25,
        "string": "1.25",
        "list": [False, 2, "two"],
        "dict": {"nested": "value"},
    }
    store = LedgerStore(tmp_path)
    store.append("facts", "fact-1", {"payload": payload})

    restored = store.read("facts", "fact-1")

    assert restored == {"payload": payload}
    assert restored is not None
    assert type(restored["payload"]["bool"]) is bool
    assert type(restored["payload"]["int"]) is int
    assert type(restored["payload"]["float"]) is float
    assert type(restored["payload"]["string"]) is str


def test_append_fsyncs_each_new_directory_entry_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ledger"
    fsynced_directories: list[Path] = []
    real_fsync_directory = LedgerStore._fsync_directory

    def recording_fsync(directory: Path) -> None:
        fsynced_directories.append(directory)
        real_fsync_directory(directory)

    monkeypatch.setattr(LedgerStore, "_fsync_directory", staticmethod(recording_fsync))

    result = LedgerStore(root).append("origin_runs", "game:T72:v1", {"status": "COMPLETE"})
    object_parent = root / "objects" / result.content_sha256[:2]

    assert fsynced_directories == [
        tmp_path,
        root,
        root / "objects",
        object_parent,
        root,
        root / "commits",
        root / "commits" / "origin_runs",
    ]
    assert json.loads(result.marker_path.read_text(encoding="utf-8"))["content_sha256"] == (
        result.content_sha256
    )


@pytest.mark.parametrize("namespace", ["", "../origin_runs", "runs/../../escape", "."])
def test_namespace_cannot_escape_commit_root(tmp_path: Path, namespace: str) -> None:
    store = LedgerStore(tmp_path)

    with pytest.raises(ValueError, match="namespace"):
        store.append(namespace, "key", {"status": "COMPLETE"})


@pytest.mark.parametrize("digest", ["../escape", "f" * 63, "F" * 64])
def test_object_digest_must_be_lowercase_sha256(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ValueError, match="digest"):
        LedgerStore(tmp_path).object_path(digest)


def test_identical_payload_duplicate_race_has_one_visible_commit(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    barrier = Barrier(2)

    def append() -> AppendResult:
        barrier.wait()
        return store.append("origin_runs", "game:T72:v1", {"status": "COMPLETE"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result() for future in [executor.submit(append), executor.submit(append)]]

    assert sorted(result.created for result in results) == [False, True]
    assert len({result.content_sha256 for result in results}) == 1
    assert store.read("origin_runs", "game:T72:v1") == {"status": "COMPLETE"}


def test_conflicting_payload_duplicate_race_fails_one_writer_closed(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    barrier = Barrier(2)

    def append(status: str) -> AppendResult | IdempotencyConflict:
        barrier.wait()
        try:
            return store.append("origin_runs", "game:T72:v1", {"status": status})
        except IdempotencyConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in [executor.submit(append, "COMPLETE"), executor.submit(append, "FAILED")]
        ]

    assert sum(isinstance(result, IdempotencyConflict) for result in results) == 1
    assert store.read("origin_runs", "game:T72:v1") in [
        {"status": "COMPLETE"},
        {"status": "FAILED"},
    ]


def test_storage_layout_exposes_every_specification_namespace(tmp_path: Path) -> None:
    layout = StorageLayout(tmp_path)

    assert layout.raw_capture("nflverse", 2026, 9, 7, "cap-1", "json") == (
        tmp_path / "raw/nflverse/2026/09/07/cap-1.json"
    )
    assert layout.capture_manifest(2026, 9, "cap-1") == (
        tmp_path / "manifests/captures/2026/09/cap-1.json"
    )
    assert layout.normalized_events(2026, "events.parquet") == (
        tmp_path / "normalized/events/season=2026/events.parquet"
    )
    assert layout.normalized_quotes(2026, 1, "quotes.parquet") == (
        tmp_path / "normalized/quotes/season=2026/week=1/quotes.parquet"
    )
    assert layout.feature_snapshots(2026, 1, "features.parquet") == (
        tmp_path / "snapshots/features/season=2026/week=1/features.parquet"
    )
    assert layout.artifact("model-1") == tmp_path / "artifacts/model-1"
    assert layout.predictions(2026, 1, "predictions.parquet") == (
        tmp_path / "ledgers/predictions/season=2026/week=1/predictions.parquet"
    )
    assert layout.displayed_quote_candidates(2026, 1, "candidates.parquet") == (
        tmp_path / "ledgers/displayed_quote_candidates/season=2026/week=1/candidates.parquet"
    )
    assert layout.actual_tickets(2026, "tickets.parquet") == (
        tmp_path / "ledgers/actual_tickets/season=2026/tickets.parquet"
    )
    assert layout.outcomes(2026, "outcomes.parquet") == (
        tmp_path / "outcomes/season=2026/outcomes.parquet"
    )
    assert layout.report("weekly-v1") == tmp_path / "reports/weekly-v1"


def test_storage_layout_rejects_path_components(tmp_path: Path) -> None:
    layout = StorageLayout(tmp_path)

    with pytest.raises(ValueError, match="path component"):
        layout.artifact("../../outside")
    with pytest.raises(ValueError, match="path component"):
        layout.raw_capture("source/name", 2026, 9, 7, "cap-1", "json")


def _event() -> EventVersion:
    instant = datetime(2026, 8, 1, tzinfo=UTC)
    return EventVersion(
        canonical_event_id="2026-SEA-SF",
        event_version=1,
        source_event_ids={"nflverse": "game-1"},
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


def test_contract_schema_registry_covers_all_top_level_persistable_records() -> None:
    contract_types = {
        EventVersion,
        CaptureManifest,
        NormalizedFact,
        FeatureSnapshot,
        OriginRun,
        Prediction,
        MoneylineQuote,
        MarketComparator,
        BettingDecision,
        DisplayedQuoteCandidate,
        ActualTicket,
        Outcome,
        Settlement,
    }

    assert set(CONTRACT_SCHEMAS_V1) == contract_types
    assert all(
        schema_for(contract).metadata[b"schema_version"] == b"1" for contract in contract_types
    )


def test_parquet_round_trip_preserves_schema_and_returns_file_hash(tmp_path: Path) -> None:
    path = tmp_path / "events.parquet"
    digest = write_contracts(path, [_event()], schema_for(EventVersion))

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert pq.read_schema(path).equals(schema_for(EventVersion), check_metadata=True)
    assert pq.read_table(path).to_pylist()[0]["canonical_event_id"] == "2026-SEA-SF"


def test_parquet_preserves_nested_json_native_payload_values_and_types(tmp_path: Path) -> None:
    payload = {
        "none": None,
        "bool": True,
        "int": 1,
        "float": 1.25,
        "string": "1.25",
        "list": [False, 2, "two"],
        "dict": {"nested": "value"},
    }
    path = tmp_path / "facts.parquet"
    write_contracts(path, [_normalized_fact(payload)], schema_for(NormalizedFact))

    restored = json.loads(pq.read_table(path).to_pylist()[0]["payload"])

    assert restored == payload
    assert type(restored["bool"]) is bool
    assert type(restored["int"]) is int
    assert type(restored["float"]) is float
    assert type(restored["string"]) is str


@pytest.mark.parametrize(
    "unsupported",
    [
        Decimal("1.25"),
        datetime(2026, 9, 7, tzinfo=UTC),
        b"bytes",
        ("tuple",),
        {1: "non-string-key"},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_parquet_rejects_non_json_native_dynamic_payload(
    tmp_path: Path, unsupported: object
) -> None:
    with pytest.raises(TypeError, match="JSON-native"):
        write_contracts(
            tmp_path / "facts.parquet",
            [_normalized_fact({"value": unsupported})],
            schema_for(NormalizedFact),
        )


class _MissingField(BaseModel):
    first: str


class _ExtraField(BaseModel):
    first: str
    required: str
    second: str


@pytest.mark.parametrize(
    "record",
    [_MissingField(first="one"), _ExtraField(first="one", required="yes", second="two")],
)
def test_parquet_rejects_missing_and_extra_row_fields(tmp_path: Path, record: BaseModel) -> None:
    schema = pa.schema([pa.field("first", pa.string()), pa.field("required", pa.string())])

    with pytest.raises(ValueError, match="row fields must match schema"):
        write_contracts(tmp_path / "strict.parquet", [record], schema)
