from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade, SnapshotStatus
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.lineage import FeatureSnapshot, NormalizedFact
from nfl_predictor.contracts.serialization import canonical_feature_bytes, sha256_hex

from .policy import FeaturePolicy
from .qb import qb_features
from .schedule import schedule_features
from .schema import (
    FEATURE_SCHEMA_V1,
    QB_FEATURE_NAMES,
    RATING_FEATURE_NAMES,
    SCHEDULE_FEATURE_NAMES,
    VENUE_FEATURE_NAMES,
)

GRADE_RANK = {
    ProvenanceGrade.A: 0,
    ProvenanceGrade.B: 1,
    ProvenanceGrade.C: 2,
}
ALLOWED_FACT_TYPES_V1 = frozenset(
    {
        "completed_game",
        "division_alignment",
        "league_strength_prior",
        "qb_trailing",
        "team_game_epa",
        "team_passing_prior",
        "team_strength_prior",
    }
)


class FactStore(Protocol):
    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]: ...


class RatingService(Protocol):
    def features(
        self, event: EventVersion, cutoff: datetime, facts: list[NormalizedFact]
    ) -> dict[str, float]: ...


class VenueStore(Protocol):
    csv_sha256: str

    def features(self, event: EventVersion, cutoff: datetime) -> dict[str, float]: ...


class FeatureBuilder:
    def __init__(
        self,
        fact_store: FactStore,
        policy: FeaturePolicy,
        venue_store: VenueStore,
        rating_service: RatingService,
    ) -> None:
        self.fact_store = fact_store
        self.policy = policy
        self.venue_store = venue_store
        self.rating_service = rating_service

    @staticmethod
    def _validate_cutoff(decision_at_utc: datetime) -> None:
        if decision_at_utc.tzinfo is None or decision_at_utc.utcoffset() != UTC.utcoffset(
            decision_at_utc
        ):
            raise ValueError("decision_at_utc must be timezone-aware UTC")

    @staticmethod
    def _eligible_facts(
        event: EventVersion,
        facts: list[NormalizedFact],
        decision_at_utc: datetime,
    ) -> list[NormalizedFact]:
        eligible: list[NormalizedFact] = []
        for fact in facts:
            if fact.available_at_utc > decision_at_utc:
                raise ValueError(f"future fact selected: {fact.fact_id} available after cutoff")
            if (
                fact.provenance_grade is ProvenanceGrade.A
                and fact.captured_at_utc > decision_at_utc
            ):
                raise ValueError(f"fact {fact.fact_id} captured after cutoff")
            if fact.provenance_grade is ProvenanceGrade.B and (
                fact.archive_published_at_utc is None
                or fact.archive_published_at_utc > decision_at_utc
            ):
                raise ValueError(f"fact {fact.fact_id} archive unavailable at cutoff")
            if fact.fact_type not in ALLOWED_FACT_TYPES_V1:
                raise ValueError(f"fact family not allowed by feature-v1: {fact.fact_type}")
            if fact.entity_keys.get("canonical_event_id") == event.canonical_event_id:
                if fact.fact_type == "completed_game":
                    raise ValueError("current-game outcome is prohibited")
                if fact.fact_type == "team_game_epa":
                    raise ValueError("current-event team_game_epa is prohibited")
            if fact.fact_type in {"completed_game", "qb_trailing", "team_game_epa"}:
                if fact.observed_at_utc is None:
                    if fact.fact_type in {"completed_game", "team_game_epa"}:
                        raise ValueError(
                            f"{fact.fact_type} facts require a finalized observation timestamp"
                        )
                    continue
                if fact.observed_at_utc >= decision_at_utc:
                    continue
            eligible.append(fact)
        return sorted(eligible, key=lambda fact: fact.fact_id)

    def _assemble_values(
        self,
        event: EventVersion,
        decision_at_utc: datetime,
        facts: list[NormalizedFact],
    ) -> dict[str, float]:
        blocks = (
            (
                RATING_FEATURE_NAMES,
                self.rating_service.features(event, decision_at_utc, facts),
            ),
            (QB_FEATURE_NAMES, qb_features(event, facts, self.policy, decision_at_utc)),
            (
                SCHEDULE_FEATURE_NAMES,
                schedule_features(event, facts, self.policy, decision_at_utc),
            ),
            (VENUE_FEATURE_NAMES, self.venue_store.features(event, decision_at_utc)),
        )
        values: dict[str, float] = {}
        for expected_block, block in blocks:
            if tuple(block) != expected_block:
                raise ValueError(
                    f"feature block mismatch: expected {expected_block}, got {tuple(block)}"
                )
            overlap = values.keys() & block.keys()
            if overlap:
                raise ValueError(f"duplicate feature keys: {sorted(overlap)}")
            values.update((name, float(value)) for name, value in block.items())
        expected = tuple(name for name, _ in FEATURE_SCHEMA_V1)
        if tuple(values) != expected:
            raise ValueError(f"feature schema mismatch: expected {expected}, got {tuple(values)}")
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("all V1 feature values must be finite")
        return values

    def build(
        self,
        event: EventVersion,
        origin: ForecastOrigin | Origin,
        decision_at_utc: datetime,
        mode: str,
        facts: Sequence[NormalizedFact] | None = None,
    ) -> FeatureSnapshot:
        if mode not in {"live", "replay"}:
            raise ValueError("mode must be live or replay")
        self._validate_cutoff(decision_at_utc)
        if event.available_at_utc > decision_at_utc or event.captured_at_utc > decision_at_utc:
            raise ValueError("event version must be available and captured by cutoff")
        origin_value = origin.origin if isinstance(origin, ForecastOrigin) else Origin(origin)
        returned_facts = (
            self.fact_store.for_event_features(event, decision_at_utc)
            if facts is None
            else list(facts)
        )
        facts = self._eligible_facts(event, returned_facts, decision_at_utc)
        values = self._assemble_values(event, decision_at_utc, facts)
        payload = canonical_feature_bytes(FEATURE_SCHEMA_V1, values)
        fact_ids = [fact.fact_id for fact in facts]
        identity = json.dumps(
            [
                event.canonical_event_id,
                str(event.event_version),
                origin_value.value,
                decision_at_utc.isoformat(),
                self.policy.policy_version,
                self.policy.schema_version,
                self.venue_store.csv_sha256,
                *fact_ids,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return FeatureSnapshot(
            snapshot_id=sha256_hex(identity + payload),
            canonical_event_id=event.canonical_event_id,
            event_version=event.event_version,
            origin=origin_value,
            decision_at_utc=decision_at_utc,
            feature_policy_version=self.policy.policy_version,
            feature_schema_version=self.policy.schema_version,
            values=values,
            input_manifest_ids=sorted({fact.capture_id for fact in facts}),
            input_fact_ids=fact_ids,
            join_policy_version="asof-v1",
            provenance_grade=max(
                (fact.provenance_grade for fact in facts),
                key=GRADE_RANK.__getitem__,
                default=ProvenanceGrade.C,
            ),
            feature_vector_sha256=sha256_hex(payload),
            status=SnapshotStatus.COMPLETE if facts else SnapshotStatus.DEGRADED,
            reason_codes=[] if facts else ["NO_POINT_IN_TIME_FACTS"],
        )
