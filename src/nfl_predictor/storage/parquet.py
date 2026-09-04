"""Strict V1 Parquet contracts.

String dictionaries use sorted ``list<struct<key, value>>`` columns because
PyArrow 21 renames Arrow map entry fields on Parquet read, breaking exact schema
round trips. The two ``dict[str, Any]`` fields use canonical JSON strings so no
dynamic key or value can be silently dropped.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
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
from nfl_predictor.storage.ledger import IdempotencyConflict, canonical_json_bytes

_UTC = pa.timestamp("us", tz="UTC")
_DECIMAL = pa.decimal256(76, 38)
_STRING_PAIRS_METADATA = {b"encoding": b"sorted-string-pairs-v1"}
_STRINGS = pa.list_(pa.field("element", pa.string(), nullable=False))
_JSON_METADATA = {b"encoding": b"canonical-json-v1"}


def _field(
    name: str,
    data_type: pa.DataType,
    *,
    nullable: bool = False,
    metadata: Mapping[bytes, bytes] | None = None,
) -> pa.Field:
    return pa.field(name, data_type, nullable=nullable, metadata=metadata)


def _schema(contract: type[BaseModel], fields: list[pa.Field]) -> pa.Schema:
    return pa.schema(
        fields,
        metadata={b"contract": contract.__name__.encode("ascii"), b"schema_version": b"1"},
    )


_STRING_PAIRS = pa.list_(
    pa.field(
        "element",
        pa.struct([_field("key", pa.string()), _field("value", pa.string())]),
        nullable=False,
    )
)


_SELECTION = pa.struct(
    [
        _field("side", pa.string()),
        _field("team", pa.string()),
        _field("decimal_price", _DECIMAL),
        _field("source_outcome_id", pa.string(), nullable=True),
        _field("raw_pointer", pa.string()),
    ]
)


CONTRACT_SCHEMAS_V1: dict[type[BaseModel], pa.Schema] = {
    EventVersion: _schema(
        EventVersion,
        [
            _field("canonical_event_id", pa.string()),
            _field("event_version", pa.int64()),
            _field("source_event_ids", _STRING_PAIRS, metadata=_STRING_PAIRS_METADATA),
            _field("season", pa.int64()),
            _field("season_type", pa.string()),
            _field("week", pa.int64()),
            _field("home_team", pa.string()),
            _field("away_team", pa.string()),
            _field("kickoff_at_utc", _UTC),
            _field("venue_id", pa.string(), nullable=True),
            _field("neutral_site", pa.bool_()),
            _field("observed_at_utc", _UTC),
            _field("available_at_utc", _UTC),
            _field("captured_at_utc", _UTC),
            _field("raw_payload_sha256", pa.string()),
        ],
    ),
    CaptureManifest: _schema(
        CaptureManifest,
        [
            _field("capture_id", pa.string()),
            _field("run_id", pa.string()),
            _field("source", pa.string()),
            _field("request_fingerprint", pa.string()),
            _field("request_started_at_utc", _UTC),
            _field("response_received_at_utc", _UTC),
            _field("http_status", pa.int64()),
            _field("raw_path", pa.string()),
            _field("raw_payload_sha256", pa.string()),
            _field(
                "response_headers_allowlisted",
                _STRING_PAIRS,
                metadata=_STRING_PAIRS_METADATA,
            ),
            _field("source_snapshot_at_utc", _UTC, nullable=True),
            _field("code_sha", pa.string()),
            _field("dependency_lock_sha256", pa.string()),
            _field("schema_version", pa.string()),
        ],
    ),
    NormalizedFact: _schema(
        NormalizedFact,
        [
            _field("fact_id", pa.string()),
            _field("capture_id", pa.string()),
            _field("input_capture_ids", _STRINGS),
            _field("fact_type", pa.string()),
            _field("entity_keys", _STRING_PAIRS, metadata=_STRING_PAIRS_METADATA),
            _field("payload", pa.string(), metadata=_JSON_METADATA),
            _field("provider_record_id", pa.string(), nullable=True),
            _field("raw_pointer", pa.string()),
            _field("observed_at_utc", _UTC, nullable=True),
            _field("available_at_utc", _UTC),
            _field("captured_at_utc", _UTC),
            _field("archive_published_at_utc", _UTC, nullable=True),
            _field("provenance_grade", pa.string()),
            _field("normalization_schema_version", pa.string()),
            _field("fact_content_sha256", pa.string()),
        ],
    ),
    FeatureSnapshot: _schema(
        FeatureSnapshot,
        [
            _field("snapshot_id", pa.string()),
            _field("canonical_event_id", pa.string()),
            _field("event_version", pa.int64()),
            _field("origin", pa.string()),
            _field("decision_at_utc", _UTC),
            _field("feature_policy_version", pa.string()),
            _field("feature_schema_version", pa.string()),
            _field("values", pa.string(), metadata=_JSON_METADATA),
            _field("input_manifest_ids", _STRINGS),
            _field("input_fact_ids", _STRINGS),
            _field("join_policy_version", pa.string()),
            _field("provenance_grade", pa.string()),
            _field("feature_vector_sha256", pa.string()),
            _field("status", pa.string()),
            _field("reason_codes", _STRINGS),
        ],
    ),
    OriginRun: _schema(
        OriginRun,
        [
            _field("origin_run_id", pa.string()),
            _field("canonical_event_id", pa.string()),
            _field("event_version", pa.int64()),
            _field("origin", pa.string()),
            _field("forecast_policy_version", pa.string()),
            _field("target_at_utc", _UTC),
            _field("window_opens_at_utc", _UTC),
            _field("window_closes_at_utc", _UTC),
            _field("run_started_at_utc", _UTC, nullable=True),
            _field("decision_at_utc", _UTC, nullable=True),
            _field("status", pa.string()),
            _field("attempt_ids", _STRINGS),
            _field("prediction_ids", _STRINGS),
            _field("market_comparator_id", pa.string(), nullable=True),
            _field("reason_codes", _STRINGS),
            _field("code_sha", pa.string()),
            _field("created_at_utc", _UTC),
        ],
    ),
    Prediction: _schema(
        Prediction,
        [
            _field("prediction_id", pa.string()),
            _field("origin_run_id", pa.string()),
            _field("canonical_event_id", pa.string()),
            _field("event_version", pa.int64()),
            _field("origin", pa.string()),
            _field("target_at_utc", _UTC),
            _field("decision_at_utc", _UTC),
            _field("model_lane", pa.string()),
            _field("model_role", pa.string()),
            _field("model_artifact_id", pa.string()),
            _field("calibrator_artifact_id", pa.string(), nullable=True),
            _field("feature_snapshot_id", pa.string()),
            _field("market_snapshot_id", pa.string(), nullable=True),
            _field("p_home", _DECIMAL),
            _field("p_away", _DECIMAL),
            _field("p_tie", _DECIMAL),
            _field("predicted_winner", pa.string()),
            _field("provenance_grade", pa.string()),
            _field("status", pa.string()),
            _field("reason_codes", _STRINGS),
            _field("code_sha", pa.string()),
            _field("policy_versions", _STRING_PAIRS, metadata=_STRING_PAIRS_METADATA),
            _field("created_at_utc", _UTC),
        ],
    ),
    MoneylineQuote: _schema(
        MoneylineQuote,
        [
            _field("quote_id", pa.string()),
            _field("capture_id", pa.string()),
            _field("canonical_event_id", pa.string()),
            _field("source_event_id", pa.string()),
            _field("book_key", pa.string()),
            _field("book_name", pa.string()),
            _field("market_key", pa.string()),
            _field("period", pa.string()),
            _field("provider_last_update_at_utc", _UTC),
            _field("response_received_at_utc", _UTC),
            _field("capture_lag_seconds", pa.int64()),
            _field("provider_update_lag_seconds", pa.int64()),
            _field("selections", pa.list_(pa.field("element", _SELECTION, nullable=False), 2)),
            _field("overtime_included", pa.bool_()),
            _field("tie_handling", pa.string()),
            _field("market_semantics_version", pa.string()),
            _field("settlement_policy_url", pa.string()),
            _field("settlement_policy_version", pa.string()),
            _field("provenance_grade", pa.string()),
        ],
    ),
    MarketComparator: _schema(
        MarketComparator,
        [
            _field("market_comparator_id", pa.string()),
            _field("origin_run_id", pa.string()),
            _field("canonical_event_id", pa.string()),
            _field("origin", pa.string()),
            _field("decision_at_utc", _UTC),
            _field("market_policy_version", pa.string()),
            _field("quote_ids", _STRINGS),
            _field("r_home_market", _DECIMAL),
            _field("p_tie_shared_prior", _DECIMAL),
            _field("provenance_grade", pa.string()),
        ],
    ),
    BettingDecision: _schema(
        BettingDecision,
        [
            _field("decision_id", pa.string()),
            _field("prediction_id", pa.string()),
            _field("canonical_event_id", pa.string()),
            _field("origin", pa.string()),
            _field("side", pa.string()),
            _field("quote_id", pa.string(), nullable=True),
            _field("candidate_id", pa.string(), nullable=True),
            _field("evaluated_at_utc", _UTC),
            _field("policy_version", pa.string()),
            _field("status", pa.string()),
            _field("reason_codes", _STRINGS),
        ],
    ),
    DisplayedQuoteCandidate: _schema(
        DisplayedQuoteCandidate,
        [
            _field("candidate_id", pa.string()),
            _field("prediction_id", pa.string()),
            _field("quote_id", pa.string()),
            _field("origin", pa.string()),
            _field("side", pa.string()),
            _field("decision_at_utc", _UTC),
            _field("decimal_price", _DECIMAL),
            _field("raw_p_win", _DECIMAL),
            _field("buffered_p_win", _DECIMAL),
            _field("p_loss", _DECIMAL),
            _field("buffered_p_loss", _DECIMAL),
            _field("p_push", _DECIMAL),
            _field("displayed_ev_per_unit", _DECIMAL),
            _field("fixed_stake_units", _DECIMAL),
            _field("quarter_kelly_fraction", _DECIMAL),
            _field("policy_version", pa.string()),
            _field("decision_status", pa.string()),
            _field("reason_codes", _STRINGS),
        ],
    ),
    ActualTicket: _schema(
        ActualTicket,
        [
            _field("ticket_id", pa.string()),
            _field("candidate_id", pa.string(), nullable=True),
            _field("model_attributed", pa.bool_()),
            _field("operator", pa.string()),
            _field("jurisdiction", pa.string()),
            _field("accepted_at_utc", _UTC),
            _field("accepted_decimal_price", _DECIMAL),
            _field("stake", _DECIMAL),
            _field("currency", pa.string()),
            _field("settlement_id", pa.string(), nullable=True),
        ],
    ),
    Outcome: _schema(
        Outcome,
        [
            _field("outcome_id", pa.string()),
            _field("outcome_version", pa.int64()),
            _field("canonical_event_id", pa.string()),
            _field("event_version_at_final", pa.int64()),
            _field("game_status", pa.string()),
            _field("home_score", pa.int64(), nullable=True),
            _field("away_score", pa.int64(), nullable=True),
            _field("result", pa.string()),
            _field("finalized_at_utc", _UTC, nullable=True),
            _field("source", pa.string()),
            _field("source_version_or_retrieved_at_utc", _UTC),
            _field("raw_payload_sha256", pa.string()),
        ],
    ),
    Settlement: _schema(
        Settlement,
        [
            _field("settlement_id", pa.string()),
            _field("settlement_version", pa.int64()),
            _field("supersedes_settlement_id", pa.string(), nullable=True),
            _field("canonical_event_id", pa.string()),
            _field("candidate_id", pa.string(), nullable=True),
            _field("ticket_id", pa.string(), nullable=True),
            _field("operator", pa.string()),
            _field("market_semantics_version", pa.string()),
            _field("settlement_policy_url", pa.string()),
            _field("settlement_policy_version", pa.string()),
            _field("result", pa.string()),
            _field("profit_units", _DECIMAL, nullable=True),
            _field("profit_amount", _DECIMAL, nullable=True),
            _field("currency", pa.string(), nullable=True),
            _field("settled_at_utc", _UTC, nullable=True),
            _field("manual_review_required", pa.bool_()),
        ],
    ),
}


def schema_for(contract: type[BaseModel] | BaseModel) -> pa.Schema:
    contract_type = contract if isinstance(contract, type) else type(contract)
    try:
        return CONTRACT_SCHEMAS_V1[contract_type]
    except KeyError as error:
        raise KeyError(f"no V1 Parquet schema for {contract_type.__name__}") from error


def _prepare_row(record: BaseModel, schema: pa.Schema) -> dict[str, Any]:
    row = record.model_dump(mode="python")
    expected = set(schema.names)
    actual = set(row)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"row fields must match schema exactly; missing={missing}, extra={extra}")
    for field in schema:
        value = row[field.name]
        if value is None:
            continue
        if field.metadata and field.metadata.get(b"encoding") == b"canonical-json-v1":
            row[field.name] = canonical_json_bytes(value).decode("utf-8")
        elif field.metadata and field.metadata.get(b"encoding") == b"sorted-string-pairs-v1":
            if not isinstance(value, Mapping):
                raise ValueError(f"{field.name} must be a mapping")
            row[field.name] = [{"key": key, "value": item} for key, item in sorted(value.items())]
    return row


def write_contracts(path: Path, records: Sequence[BaseModel], schema: pa.Schema) -> str:
    rows = [_prepare_row(record, schema) for record in records]
    table = pa.Table.from_pylist(rows, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        pq.write_table(table, temporary, compression="zstd")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        actual_schema = pq.read_schema(temporary)
        if not actual_schema.equals(schema, check_metadata=True):
            raise ValueError("Parquet schema round-trip mismatch")
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise IdempotencyConflict(str(path)) from error
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()
