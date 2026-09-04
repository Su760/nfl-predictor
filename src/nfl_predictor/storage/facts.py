from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import duckdb

from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import NormalizedFact


@dataclass(frozen=True)
class FactQuery:
    fact_type: str
    entity_keys: dict[str, str]
    decision_at_utc: datetime
    provenance_grade: ProvenanceGrade


class PointInTimeStore:
    def __init__(self, records: list[NormalizedFact]) -> None:
        self.records = tuple(records)

    @classmethod
    def from_records(cls, records: list[NormalizedFact]) -> PointInTimeStore:
        return cls(records)

    @staticmethod
    def _eligible(fact: NormalizedFact, cutoff: datetime) -> bool:
        if fact.available_at_utc > cutoff:
            return False
        if fact.provenance_grade is ProvenanceGrade.A:
            return fact.captured_at_utc <= cutoff
        if fact.provenance_grade is ProvenanceGrade.B:
            return (
                fact.archive_published_at_utc is not None
                and fact.archive_published_at_utc <= cutoff
            )
        return True

    @staticmethod
    def _latest(records: list[NormalizedFact]) -> list[NormalizedFact]:
        latest: dict[tuple[str, tuple[tuple[str, str], ...], str], NormalizedFact] = {}
        for fact in sorted(
            records,
            key=lambda item: (item.available_at_utc, item.captured_at_utc, item.fact_id),
        ):
            key = (
                fact.fact_type,
                tuple(sorted(fact.entity_keys.items())),
                fact.provider_record_id or fact.fact_id,
            )
            latest[key] = fact
        return sorted(latest.values(), key=lambda item: item.fact_id)

    def select(self, query: FactQuery) -> list[NormalizedFact]:
        matches = [
            fact
            for fact in self.records
            if fact.fact_type == query.fact_type
            and fact.provenance_grade is query.provenance_grade
            and all(fact.entity_keys.get(key) == value for key, value in query.entity_keys.items())
            and self._eligible(fact, query.decision_at_utc)
        ]
        return self._latest(matches)

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        event_teams = {event.home_team, event.away_team}
        relevant = [
            fact
            for fact in self.records
            if self._eligible(fact, cutoff)
            and (
                fact.entity_keys.get("canonical_event_id") == event.canonical_event_id
                or any(
                    fact.entity_keys.get(key) in event_teams
                    for key in ("team", "offense_team", "defense_team")
                )
                or (
                    event.venue_id is not None
                    and fact.entity_keys.get("venue_id") == event.venue_id
                )
            )
        ]
        return self._latest(relevant)


class DuckDbPointInTimeStore(PointInTimeStore):
    """DuckDB-backed predicates with the same ordering and deduplication as the memory store."""

    def __init__(self, records: list[NormalizedFact]) -> None:
        super().__init__(records)
        self._connection = duckdb.connect(":memory:")
        self._connection.execute(
            """
            CREATE TABLE facts (
                fact_type VARCHAR NOT NULL,
                entity_keys_json JSON NOT NULL,
                available_at_utc TIMESTAMPTZ NOT NULL,
                captured_at_utc TIMESTAMPTZ NOT NULL,
                archive_published_at_utc TIMESTAMPTZ,
                provenance_grade VARCHAR NOT NULL,
                record_json JSON NOT NULL
            )
            """
        )
        if records:
            self._connection.executemany(
                "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fact.fact_type,
                        json.dumps(fact.entity_keys, sort_keys=True, separators=(",", ":")),
                        fact.available_at_utc,
                        fact.captured_at_utc,
                        fact.archive_published_at_utc,
                        fact.provenance_grade.value,
                        fact.model_dump_json(),
                    )
                    for fact in records
                ],
            )

    @staticmethod
    def _json_path(key: str) -> str:
        escaped = key.replace("\\", "\\\\").replace('"', '\\"')
        return f'$."{escaped}"'

    @staticmethod
    def _eligibility_sql() -> str:
        return """
            available_at_utc <= ?
            AND CASE provenance_grade
                WHEN 'A' THEN captured_at_utc <= ?
                WHEN 'B' THEN archive_published_at_utc IS NOT NULL
                              AND archive_published_at_utc <= ?
                WHEN 'C' THEN TRUE
                ELSE FALSE
            END
        """

    def _fetch(self, where: str, parameters: list[object]) -> list[NormalizedFact]:
        rows = self._connection.execute(
            f"SELECT CAST(record_json AS VARCHAR) FROM facts WHERE {where}", parameters
        ).fetchall()
        return [NormalizedFact.model_validate_json(row[0]) for row in rows]

    def select(self, query: FactQuery) -> list[NormalizedFact]:
        clauses = ["fact_type = ?", "provenance_grade = ?", self._eligibility_sql()]
        parameters: list[object] = [
            query.fact_type,
            query.provenance_grade.value,
            query.decision_at_utc,
            query.decision_at_utc,
            query.decision_at_utc,
        ]
        for key, value in sorted(query.entity_keys.items()):
            clauses.append("json_extract_string(entity_keys_json, ?) = ?")
            parameters.extend([self._json_path(key), value])
        return self._latest(self._fetch(" AND ".join(clauses), parameters))

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        relationships = [
            "json_extract_string(entity_keys_json, '$.canonical_event_id') = ?",
            "json_extract_string(entity_keys_json, '$.team') IN (?, ?)",
            "json_extract_string(entity_keys_json, '$.offense_team') IN (?, ?)",
            "json_extract_string(entity_keys_json, '$.defense_team') IN (?, ?)",
        ]
        parameters: list[object] = [cutoff, cutoff, cutoff, event.canonical_event_id]
        parameters.extend([event.home_team, event.away_team])
        parameters.extend([event.home_team, event.away_team])
        parameters.extend([event.home_team, event.away_team])
        if event.venue_id is not None:
            relationships.append("json_extract_string(entity_keys_json, '$.venue_id') = ?")
            parameters.append(event.venue_id)
        where = f"{self._eligibility_sql()} AND ({' OR '.join(relationships)})"
        return self._latest(self._fetch(where, parameters))
