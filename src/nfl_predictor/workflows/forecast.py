from __future__ import annotations

import fcntl
import inspect
import json
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from nfl_predictor.betting.math import displayed_ev, quarter_kelly
from nfl_predictor.contracts.attempts import AttemptTrigger, RunAttempt
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade, RunStatus
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.forecasts import OriginRun, Prediction
from nfl_predictor.contracts.lineage import CaptureManifest, FeatureSnapshot, NormalizedFact
from nfl_predictor.contracts.markets import (
    DisplayedQuoteCandidate,
    MarketComparator,
    MoneylineQuote,
)
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.markets.budget import BudgetExceeded
from nfl_predictor.markets.comparator import median_market_home
from nfl_predictor.storage.ledger import (
    DataIntegrityError,
    IdempotencyConflict,
    LedgerStore,
    canonical_json_bytes,
)

RepositoryNamespace = Literal["prospective", "replay"]
ExecutionMode = Literal["live", "replay"]
ModelT = TypeVar("ModelT", bound=BaseModel)


class MarketCaptureDisabled(RuntimeError):
    pass


class MarketCaptureStale(RuntimeError):
    pass


class MarketSchemaDrift(RuntimeError):
    pass


@dataclass(frozen=True)
class ForecastExecutionContext:
    mode: ExecutionMode
    namespace: RepositoryNamespace
    provenance_grade: ProvenanceGrade
    as_of_utc: datetime | None = None
    reconstruction_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or not isinstance(self.namespace, str):
            raise TypeError("execution context mode and namespace must be strings")
        if not isinstance(self.provenance_grade, ProvenanceGrade):
            raise TypeError("execution context provenance grade must be validated")
        if self.mode == "live":
            if (
                self.namespace != "prospective"
                or self.provenance_grade is not ProvenanceGrade.A
                or self.as_of_utc is None
                or self.reconstruction_reason is not None
            ):
                raise ValueError("live execution requires prospective Grade A and real as-of")
            self._require_utc(self.as_of_utc)
            return
        if (
            self.namespace != "replay"
            or self.provenance_grade is not ProvenanceGrade.C
            or self.as_of_utc is None
            or self.reconstruction_reason is None
            or not self.reconstruction_reason.strip()
        ):
            raise ValueError("replay requires cutoff, namespace, Grade C, and reason")
        self._require_utc(self.as_of_utc)

    @staticmethod
    def _require_utc(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("execution context as-of must be timezone-aware UTC")

    @classmethod
    def live(cls, as_of_utc: datetime) -> ForecastExecutionContext:
        return cls("live", "prospective", ProvenanceGrade.A, as_of_utc)

    @classmethod
    def replay(cls, cutoff: datetime, reason: str) -> ForecastExecutionContext:
        return cls("replay", "replay", ProvenanceGrade.C, cutoff, reason)


@dataclass(frozen=True)
class CaptureBundle:
    records: tuple[object, ...]
    payload: object


@dataclass(frozen=True)
class ArtifactBinding:
    artifact_id: str
    origin: Origin
    model_role: Literal["champion", "challenger"]
    frozen: bool
    verified: bool
    calibrator_artifact_id: str | None = None
    model_lane: Literal["football_only", "market_blend"] = "football_only"
    feature_policy_version: str = "features-v1"
    feature_schema_version: str = "schema-v1"
    policy_versions: tuple[tuple[str, str], ...] = (("forecast", "forecast-v1"),)
    market_policy_version: str = "market-v1"
    candidate_policy_version: str = "candidate-v1"

    def __post_init__(self) -> None:
        required = (
            self.artifact_id,
            self.feature_policy_version,
            self.feature_schema_version,
            self.market_policy_version,
            self.candidate_policy_version,
        )
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ValueError("artifact binding identifiers and policies must be non-blank")
        if self.calibrator_artifact_id is not None and not self.calibrator_artifact_id.strip():
            raise ValueError("artifact calibrator identity must be non-blank when present")
        if self.model_role not in {"champion", "challenger"}:
            raise ValueError("artifact binding role is invalid")
        if self.model_lane not in {"football_only", "market_blend"}:
            raise ValueError("artifact binding lane is invalid")
        policy_keys = [key for key, _ in self.policy_versions]
        if len(policy_keys) != len(set(policy_keys)) or any(
            not key.strip() or not value.strip() for key, value in self.policy_versions
        ):
            raise ValueError("artifact policy lineage must be unique and non-blank")

    @property
    def expected_calibrator_artifact_id(self) -> str | None:
        return self.calibrator_artifact_id or f"calibrator-{self.artifact_id}"


@dataclass(frozen=True)
class MarketEvaluation:
    comparator: MarketComparator | None
    decisions: tuple[BettingDecision, ...]
    candidates: tuple[DisplayedQuoteCandidate, ...] = ()
    quotes: tuple[MoneylineQuote, ...] = ()


@dataclass(frozen=True)
class OptionalFailure:
    attempt_id: str
    origin_run_id: str
    origin: Origin
    artifact_id: str
    safe_error_category: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                self.attempt_id,
                self.origin_run_id,
                self.artifact_id,
                self.safe_error_category,
            )
        ):
            raise ValueError("optional failure attribution must be non-blank")


@dataclass(frozen=True)
class ArtifactSelection:
    attempt_id: str
    origin_run_id: str
    binding: ArtifactBinding

    def __post_init__(self) -> None:
        if not self.attempt_id.strip() or not self.origin_run_id.strip():
            raise ValueError("artifact selection attempt lineage must be non-blank")


class ArtifactRegistry(Protocol):
    def for_origin(self, origin: Origin) -> tuple[ArtifactBinding, ...]: ...


@dataclass(frozen=True)
class TerminalPublicationReceipt:
    execution_key: str
    run_content_sha256: str
    candidate_durable_at_utc: datetime
    state: Literal["COMMITTED"] = "COMMITTED"

    def __post_init__(self) -> None:
        if not self.execution_key or re.fullmatch(r"[0-9a-f]{64}", self.run_content_sha256) is None:
            raise ValueError("terminal publication receipt identity is invalid")
        if (
            self.candidate_durable_at_utc.tzinfo is None
            or self.candidate_durable_at_utc.utcoffset()
            != UTC.utcoffset(self.candidate_durable_at_utc)
        ):
            raise ValueError("terminal publication receipt time must be UTC")
        if self.state != "COMMITTED":
            raise ValueError("terminal publication receipt state is invalid")


@dataclass(frozen=True)
class MarketValidationBinding:
    market_comparator_id: str
    champion_prediction_id: str
    market_policy_version: str
    candidate_policy_version: str
    comparator_math_version: str = "median-no-vig-v1"
    candidate_math_version: str = "displayed-ev-quarter-kelly-v1"


@dataclass(frozen=True)
class ForecastObligationRecord:
    execution_key: str
    event: EventVersion
    origin: Origin
    target_at_utc: datetime
    window_opens_at_utc: datetime
    window_closes_at_utc: datetime
    forecast_policy_version: str
    mode: ExecutionMode
    namespace: RepositoryNamespace
    provenance_grade: ProvenanceGrade
    as_of_utc: datetime | None
    reconstruction_reason: str | None
    champion_binding: ArtifactBinding | None = None


@dataclass(frozen=True)
class CommittedForecastGraph:
    obligation: ForecastObligationRecord
    run: OriginRun
    attempts: tuple[RunAttempt, ...]
    manifests: tuple[CaptureManifest, ...]
    facts: tuple[NormalizedFact, ...]
    quotes: tuple[MoneylineQuote, ...]
    optional_failures: tuple[OptionalFailure, ...]
    artifact_selections: tuple[ArtifactSelection, ...]
    snapshot: FeatureSnapshot | None
    predictions: tuple[Prediction, ...]
    comparator: MarketComparator | None
    decisions: tuple[BettingDecision, ...]
    candidates: tuple[DisplayedQuoteCandidate, ...]
    market_validation: MarketValidationBinding | None = None


def validate_obligation_record(
    obligation: ForecastObligationRecord,
) -> ForecastObligationRecord:
    live = obligation.mode == "live"
    canonical_key = (
        f"{obligation.event.canonical_event_id}:{obligation.origin.value}:"
        f"{obligation.forecast_policy_version}"
    )
    expected_window = ForecastOrigin.for_kickoff(obligation.origin, obligation.event.kickoff_at_utc)
    expected_key = canonical_key
    if not live and obligation.as_of_utc is not None and obligation.reconstruction_reason:
        replay_identity = (
            f"{canonical_key}|{obligation.namespace}|{obligation.as_of_utc.isoformat()}|"
            f"{obligation.reconstruction_reason}"
        )
        expected_key = f"{canonical_key}:replay:{sha256(replay_identity.encode()).hexdigest()}"
    if (
        obligation.execution_key != expected_key
        or obligation.target_at_utc != expected_window.target_at_utc
        or obligation.window_opens_at_utc != expected_window.window_opens_at_utc
        or obligation.window_closes_at_utc != expected_window.window_closes_at_utc
        or (
            live
            and (
                obligation.namespace != "prospective"
                or obligation.provenance_grade is not ProvenanceGrade.A
                or obligation.as_of_utc is not None
                or obligation.reconstruction_reason is not None
            )
        )
        or (
            not live
            and (
                obligation.mode != "replay"
                or obligation.namespace != "replay"
                or obligation.provenance_grade is not ProvenanceGrade.C
                or obligation.as_of_utc is None
                or not obligation.reconstruction_reason
            )
        )
    ):
        raise DataIntegrityError("forecast obligation execution identity is invalid")
    champion = obligation.champion_binding
    if champion is not None and (
        champion.origin is not obligation.origin
        or champion.model_role != "champion"
        or not champion.frozen
        or not champion.verified
    ):
        raise DataIntegrityError("forecast obligation champion binding is invalid")
    return obligation


def _trusted_artifact_bindings(
    resolver: ArtifactRegistry | None,
    origin: Origin,
) -> dict[tuple[str, str], ArtifactBinding]:
    if resolver is None:
        raise DataIntegrityError("independent artifact trust resolver is required")
    try:
        bindings = resolver.for_origin(origin)
    except Exception as error:
        raise DataIntegrityError("independent artifact trust resolution failed") from error
    if not isinstance(bindings, tuple) or any(
        not isinstance(binding, ArtifactBinding) for binding in bindings
    ):
        raise DataIntegrityError("independent artifact trust result is invalid")
    champions = tuple(binding for binding in bindings if binding.model_role == "champion")
    valid_champions = tuple(
        binding
        for binding in champions
        if binding.origin is origin and binding.frozen and binding.verified
    )
    if len(champions) != 1 or len(valid_champions) != 1:
        raise DataIntegrityError("independent artifact registry must resolve exactly one champion")
    champion = valid_champions[0]
    challenger_counts: dict[str, int] = {}
    for binding in bindings:
        if binding.model_role == "challenger":
            challenger_counts[binding.artifact_id] = (
                challenger_counts.get(binding.artifact_id, 0) + 1
            )
    trusted: dict[tuple[str, str], ArtifactBinding] = {
        (champion.model_role, champion.artifact_id): champion
    }
    for binding in bindings:
        if (
            binding.model_role == "challenger"
            and binding.artifact_id != champion.artifact_id
            and challenger_counts[binding.artifact_id] == 1
            and binding.origin is origin
            and binding.frozen
            and binding.verified
        ):
            trusted[(binding.model_role, binding.artifact_id)] = binding
    return trusted


def validate_obligation_artifact_trust(
    obligation: ForecastObligationRecord,
    artifact_resolver: ArtifactRegistry | None,
) -> ForecastObligationRecord:
    champion = obligation.champion_binding
    trusted = _trusted_artifact_bindings(
        artifact_resolver,
        obligation.origin,
    )
    if (
        champion is not None
        and trusted.get((champion.model_role, champion.artifact_id)) != champion
    ):
        raise DataIntegrityError("forecast obligation champion is not independently trusted")
    return obligation


def validate_committed_graph(
    graph: CommittedForecastGraph,
    *,
    artifact_resolver: ArtifactRegistry | None = None,
) -> CommittedForecastGraph:
    """Validate the complete marker-visible forecast graph as one semantic unit."""

    obligation = validate_obligation_record(graph.obligation)
    run = graph.run
    expected_origin_run_id = sha256(
        f"{obligation.namespace}|{obligation.execution_key}".encode()
    ).hexdigest()
    if (
        not obligation.execution_key
        or run.origin_run_id != expected_origin_run_id
        or run.canonical_event_id != obligation.event.canonical_event_id
        or run.event_version != obligation.event.event_version
        or run.origin is not obligation.origin
        or run.forecast_policy_version != obligation.forecast_policy_version
        or run.target_at_utc != obligation.target_at_utc
        or run.window_opens_at_utc != obligation.window_opens_at_utc
        or run.window_closes_at_utc != obligation.window_closes_at_utc
    ):
        raise DataIntegrityError("committed forecast run does not match obligation")
    champion_binding = obligation.champion_binding
    trusted_artifacts = _trusted_artifact_bindings(
        artifact_resolver,
        obligation.origin,
    )
    if (
        champion_binding is not None
        and trusted_artifacts.get((champion_binding.model_role, champion_binding.artifact_id))
        != champion_binding
    ):
        raise DataIntegrityError("committed champion is not independently trusted")
    attempts = {item.attempt_id: item for item in graph.attempts}
    if (
        len(attempts) != len(graph.attempts)
        or not run.attempt_ids
        or not set(run.attempt_ids).issubset(attempts)
        or any(item.origin_run_id != run.origin_run_id for item in graph.attempts)
    ):
        raise DataIntegrityError("committed forecast attempt lineage is invalid")
    children: dict[str, list[str]] = {item: [] for item in attempts}
    roots: list[str] = []
    for item in graph.attempts:
        if item.retry_of_attempt_id is None:
            roots.append(item.attempt_id)
            continue
        parent = attempts.get(item.retry_of_attempt_id)
        if (
            parent is None
            or parent.ended_at_utc is None
            or item.started_at_utc < parent.ended_at_utc
        ):
            raise DataIntegrityError("committed forecast retry chain is dangling or unordered")
        children[parent.attempt_id].append(item.attempt_id)
    if len(roots) != 1 or any(len(value) > 1 for value in children.values()):
        raise DataIntegrityError("committed forecast retry chain must be singular")
    visited: list[str] = []
    current = roots[0]
    while current not in visited:
        visited.append(current)
        next_items = children[current]
        if not next_items:
            break
        current = next_items[0]
    if len(visited) != len(attempts):
        raise DataIntegrityError("committed forecast retry chain is cyclic or disconnected")
    terminal_attempt = attempts[current]
    expected_attempt_status = (
        "COMPLETE" if run.status in {RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY} else "FAILED"
    )
    if (
        run.attempt_ids != [terminal_attempt.attempt_id]
        or terminal_attempt.status != expected_attempt_status
        or terminal_attempt.ended_at_utc is None
        or any(item.status == "STARTED" for item in graph.attempts)
    ):
        raise DataIntegrityError("committed forecast terminal attempt does not match run status")
    if run.run_started_at_utc != attempts[roots[0]].started_at_utc:
        raise DataIntegrityError("committed forecast run start does not match attempt lineage")
    if (
        obligation.mode == "live"
        and run.status in {RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY}
        and (
            run.decision_at_utc is None
            or not obligation.window_opens_at_utc
            <= run.decision_at_utc
            <= obligation.window_closes_at_utc
            or terminal_attempt.ended_at_utc > obligation.window_closes_at_utc
        )
    ):
        raise DataIntegrityError("prospective forecast chronology is outside its window")
    manifests = {item.capture_id: item for item in graph.manifests}
    if len(manifests) != len(graph.manifests) or any(
        item.run_id != terminal_attempt.attempt_id for item in graph.manifests
    ):
        raise DataIntegrityError("committed forecast manifest lineage is invalid")
    if graph.manifests and (
        any(item.code_sha != run.code_sha for item in graph.manifests)
        or len({item.dependency_lock_sha256 for item in graph.manifests}) != 1
    ):
        raise DataIntegrityError("committed forecast build lineage is inconsistent")
    artifact_selections_by_identity: dict[tuple[str, str], ArtifactSelection] = {
        (item.binding.model_role, item.binding.artifact_id): item
        for item in graph.artifact_selections
    }
    if len(artifact_selections_by_identity) != len(graph.artifact_selections) or any(
        item.attempt_id != terminal_attempt.attempt_id
        or item.origin_run_id != run.origin_run_id
        or item.binding.origin is not run.origin
        or not item.binding.frozen
        or not item.binding.verified
        or trusted_artifacts.get((item.binding.model_role, item.binding.artifact_id))
        != item.binding
        for item in graph.artifact_selections
    ):
        raise DataIntegrityError(
            "committed artifact selection lineage is invalid or independently untrusted"
        )
    selected_champions = tuple(
        item.binding for item in graph.artifact_selections if item.binding.model_role == "champion"
    )
    if obligation.champion_binding is None:
        if selected_champions:
            raise DataIntegrityError("committed champion selection lacks obligation binding")
    elif selected_champions != (obligation.champion_binding,):
        raise DataIntegrityError("committed champion selection changed after obligation freeze")
    predictions = {item.prediction_id: item for item in graph.predictions}
    if (
        len(predictions) != len(graph.predictions)
        or tuple(item.prediction_id for item in graph.predictions) != tuple(run.prediction_ids)
        or any(
            item.origin_run_id != run.origin_run_id
            or item.canonical_event_id != run.canonical_event_id
            or item.event_version != run.event_version
            or item.origin is not run.origin
            or item.decision_at_utc != run.decision_at_utc
            or item.code_sha != run.code_sha
            or item.provenance_grade is not obligation.provenance_grade
            or item.status.value != "COMPLETE"
            for item in graph.predictions
        )
    ):
        raise DataIntegrityError("committed forecast prediction lineage is invalid")
    successful = run.status in {RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY}
    champions = tuple(item for item in graph.predictions if item.model_role == "champion")
    if successful != (
        len(champions) == 1
        and graph.snapshot is not None
        and obligation.champion_binding is not None
    ):
        raise DataIntegrityError("committed forecast champion/snapshot shape is invalid")
    for prediction in graph.predictions:
        selection = artifact_selections_by_identity.get(
            (prediction.model_role, prediction.model_artifact_id)
        )
        if selection is None:
            raise DataIntegrityError("committed prediction has no trusted artifact selection")
        binding = selection.binding
        expected_policies = dict(binding.policy_versions)
        expected_policies["forecast"] = obligation.forecast_policy_version
        if (
            prediction.calibrator_artifact_id != binding.expected_calibrator_artifact_id
            or prediction.model_lane != binding.model_lane
            or prediction.policy_versions != expected_policies
        ):
            raise DataIntegrityError("committed prediction artifact/policy trust is invalid")
    if graph.snapshot is not None:
        snapshot = graph.snapshot
        champion_binding = obligation.champion_binding
        if champion_binding is None:
            raise DataIntegrityError("committed feature snapshot lacks frozen champion")
        if (
            snapshot.canonical_event_id != run.canonical_event_id
            or snapshot.event_version != run.event_version
            or snapshot.origin is not run.origin
            or snapshot.decision_at_utc != run.decision_at_utc
            or snapshot.status.value != "COMPLETE"
            or snapshot.provenance_grade is not obligation.provenance_grade
            or snapshot.feature_policy_version != champion_binding.feature_policy_version
            or snapshot.feature_schema_version != champion_binding.feature_schema_version
            or any(item.feature_snapshot_id != snapshot.snapshot_id for item in graph.predictions)
            or not set(snapshot.input_manifest_ids).issubset(manifests)
        ):
            raise DataIntegrityError("committed forecast feature lineage is invalid")
        facts = {item.fact_id: item for item in graph.facts}
        if (
            len(facts) != len(graph.facts)
            or tuple(item.fact_id for item in graph.facts) != tuple(snapshot.input_fact_ids)
            or any(item.capture_id not in snapshot.input_manifest_ids for item in graph.facts)
            or any(
                not set(item.lineage_capture_ids).issubset(snapshot.input_manifest_ids)
                for item in graph.facts
            )
        ):
            raise DataIntegrityError("committed forecast fact lineage is invalid")
        if obligation.mode == "live" and (
            any(
                item.response_received_at_utc > snapshot.decision_at_utc
                for item in manifests.values()
            )
            or any(
                item.available_at_utc > snapshot.decision_at_utc
                or item.captured_at_utc > snapshot.decision_at_utc
                or item.provenance_grade is not ProvenanceGrade.A
                for item in graph.facts
            )
        ):
            raise DataIntegrityError("prospective forecast uses late or reconstructed evidence")
    elif graph.facts:
        raise DataIntegrityError("committed forecast has facts without a feature snapshot")
    failures = {(item.attempt_id, item.artifact_id): item for item in graph.optional_failures}
    if len(failures) != len(graph.optional_failures) or any(
        item.attempt_id != terminal_attempt.attempt_id
        or item.origin_run_id != run.origin_run_id
        or item.origin is not run.origin
        for item in graph.optional_failures
    ):
        raise DataIntegrityError("committed optional failure lineage is invalid")
    if run.market_comparator_id is None:
        if (
            graph.comparator is not None
            or graph.quotes
            or graph.decisions
            or graph.candidates
            or graph.market_validation is not None
        ):
            raise DataIntegrityError("committed forecast has orphan market components")
        return graph
    comparator = graph.comparator
    if comparator is None or comparator.market_comparator_id != run.market_comparator_id:
        raise DataIntegrityError("committed market comparator is missing")
    if len(champions) != 1:
        raise DataIntegrityError("committed market graph has no unique champion")
    champion = champions[0]
    market_binding = graph.market_validation
    if (
        market_binding is None
        or market_binding.market_comparator_id != comparator.market_comparator_id
        or market_binding.champion_prediction_id != champion.prediction_id
        or market_binding.market_policy_version != comparator.market_policy_version
        or market_binding.comparator_math_version != "median-no-vig-v1"
        or market_binding.candidate_math_version != "displayed-ev-quarter-kelly-v1"
    ):
        raise DataIntegrityError("committed market validation binding is invalid")
    if (
        comparator.origin_run_id != run.origin_run_id
        or comparator.canonical_event_id != run.canonical_event_id
        or comparator.origin is not run.origin
        or comparator.decision_at_utc != run.decision_at_utc
        or comparator.p_tie_shared_prior != champion.p_tie
        or tuple(item.quote_id for item in graph.quotes) != tuple(comparator.quote_ids)
        or len({item.quote_id for item in graph.quotes}) != len(graph.quotes)
        or len({item.book_key for item in graph.quotes}) != len(graph.quotes)
        or comparator.r_home_market != median_market_home(graph.quotes)
    ):
        raise DataIntegrityError("committed market comparator lineage is invalid")
    quotes = {item.quote_id: item for item in graph.quotes}
    for quote in graph.quotes:
        manifest = manifests.get(quote.capture_id)
        if manifest is None or (
            quote.canonical_event_id != run.canonical_event_id
            or quote.source_event_id not in obligation.event.source_event_ids.values()
            or quote.response_received_at_utc != manifest.response_received_at_utc
            or {item.side: item.team for item in quote.selections}
            != {
                "home": obligation.event.home_team,
                "away": obligation.event.away_team,
            }
        ):
            raise DataIntegrityError("committed market quote/capture lineage is invalid")
    candidates = {item.candidate_id: item for item in graph.candidates}
    decisions = {item.decision_id: item for item in graph.decisions}
    if len(candidates) != len(graph.candidates) or len(decisions) != len(graph.decisions):
        raise DataIntegrityError("committed market IDs are duplicated")
    accepted = {
        item.candidate_id: item for item in graph.decisions if item.candidate_id is not None
    }
    if len(accepted) != len(graph.candidates) or set(accepted) != set(candidates):
        raise DataIntegrityError("committed decisions and candidates are not bijective")
    for decision in graph.decisions:
        if (
            decision.prediction_id != champion.prediction_id
            or decision.canonical_event_id != run.canonical_event_id
            or decision.origin is not run.origin
            or decision.evaluated_at_utc != run.decision_at_utc
            or (decision.quote_id is not None and decision.quote_id not in quotes)
        ):
            raise DataIntegrityError("committed betting decision lineage is invalid")
        if decision.candidate_id is None:
            if decision.status != "rejected" or not decision.reason_codes:
                raise DataIntegrityError("committed rejected decision is invalid")
            continue
        candidate = candidates[decision.candidate_id]
        linked_quote = quotes.get(candidate.quote_id)
        quote_selections = (
            []
            if linked_quote is None
            else [row for row in linked_quote.selections if row.side == candidate.side]
        )
        if (
            candidate.prediction_id != champion.prediction_id
            or candidate.origin is not run.origin
            or candidate.decision_at_utc != run.decision_at_utc
            or candidate.quote_id != decision.quote_id
            or candidate.side != decision.side
            or candidate.policy_version != decision.policy_version
            or candidate.policy_version != market_binding.candidate_policy_version
            or candidate.decision_status != decision.status
            or candidate.reason_codes != decision.reason_codes
            or len(quote_selections) != 1
            or candidate.decimal_price != quote_selections[0].decimal_price
            or candidate.raw_p_win
            != (champion.p_home if candidate.side == "home" else champion.p_away)
            or candidate.p_loss
            != (champion.p_away if candidate.side == "home" else champion.p_home)
            or candidate.p_push != champion.p_tie
            or candidate.buffered_p_win + candidate.buffered_p_loss + candidate.p_push != Decimal(1)
            or candidate.displayed_ev_per_unit
            != displayed_ev(
                candidate.buffered_p_win,
                candidate.buffered_p_loss,
                candidate.decimal_price,
            )
            or candidate.quarter_kelly_fraction
            != quarter_kelly(
                candidate.buffered_p_win,
                candidate.buffered_p_loss,
                candidate.decimal_price,
            )
            or candidate.displayed_ev_per_unit <= 0
        ):
            raise DataIntegrityError("committed candidate economics/lineage are invalid")
    return graph


class ForecastRepository(Protocol):
    namespace: RepositoryNamespace

    @property
    def storage_identity(self) -> str: ...

    @property
    def backing_identity(self) -> tuple[int, int] | None: ...

    def transact(self, key: str, operation: Callable[[], OriginRun | None]) -> OriginRun | None: ...

    def terminal_run(self, key: str) -> OriginRun | None: ...

    def validated_terminal_run(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
    ) -> OriginRun | None: ...

    def recover_prepared_terminal(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
        *,
        deadline: datetime,
        clock: Callable[[], datetime],
    ) -> OriginRun | None: ...

    def load_obligation(self, key: str) -> ForecastObligationRecord | None: ...

    def register_obligation(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
        champion_binding: ArtifactBinding | None,
    ) -> None: ...

    def latest_attempt(self, origin_run_id: str) -> RunAttempt | None: ...

    def append_attempt(self, attempt: RunAttempt) -> None: ...

    def append_capture_record(self, record: object) -> None: ...

    def append_artifact_selection(self, selection: ArtifactSelection) -> None: ...

    def append_feature_snapshot(self, snapshot: FeatureSnapshot) -> None: ...

    def append_prediction(self, prediction: Prediction) -> None: ...

    def append_market_comparator(self, comparator: MarketComparator) -> None: ...

    def append_market_validation(self, binding: MarketValidationBinding) -> None: ...

    def append_betting_decision(self, decision: BettingDecision) -> None: ...

    def append_candidate(self, candidate: DisplayedQuoteCandidate) -> None: ...

    def commit_terminal(
        self,
        key: str,
        run: OriginRun,
        attempt: RunAttempt,
        *,
        deadline: datetime,
        context: ForecastExecutionContext,
        clock: Callable[[], datetime],
    ) -> OriginRun: ...


@dataclass(frozen=True)
class ForecastRepositories:
    prospective: ForecastRepository
    replay: ForecastRepository | None = None
    allow_ephemeral: bool = False

    def __post_init__(self) -> None:
        if self.prospective.namespace != "prospective":
            raise ValueError("prospective repository namespace is invalid")
        durable_prospective = type(self.prospective) is DurableForecastRepository
        replay_is_durable = type(self.replay) is DurableForecastRepository
        durable_replay_valid = self.replay is None or replay_is_durable
        if not self.allow_ephemeral and (not durable_prospective or not durable_replay_valid):
            raise TypeError(
                "production forecast repositories must be concrete durable repositories"
            )
        if self.allow_ephemeral and (durable_prospective or replay_is_durable):
            raise TypeError("ephemeral repository mode cannot wrap a durable repository")
        if durable_prospective and replay_is_durable:
            prospective_root = cast(DurableForecastRepository, self.prospective).root
            replay_root = cast(DurableForecastRepository, self.replay).root
            prospective_stat = os.stat(prospective_root, follow_symlinks=True)
            replay_stat = os.stat(replay_root, follow_symlinks=True)
            shared_backing = os.path.samefile(prospective_root, replay_root) or (
                prospective_stat.st_dev,
                prospective_stat.st_ino,
            ) == (replay_stat.st_dev, replay_stat.st_ino)
        else:
            prospective_backing = getattr(self.prospective, "backing_identity", None)
            replay_backing = (
                None if self.replay is None else getattr(self.replay, "backing_identity", None)
            )
            shared_backing = (
                prospective_backing is not None
                and replay_backing is not None
                and prospective_backing == replay_backing
            )
        if self.replay is not None and (
            self.replay.namespace != "replay"
            or shared_backing
            or self.replay.storage_identity == self.prospective.storage_identity
        ):
            raise ValueError("replay requires a physically distinct replay repository")

    def for_context(self, context: ForecastExecutionContext) -> ForecastRepository:
        if context.namespace == "prospective":
            return self.prospective
        if self.replay is None:
            raise RuntimeError("replay repository is not configured")
        return self.replay


class RequiredCapture(Protocol):
    def capture_live(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle: ...

    def capture_replay(
        self, obligation: OriginObligation, attempt_id: str, cutoff: datetime
    ) -> CaptureBundle: ...


class OptionalMarketCapture(Protocol):
    @property
    def enabled(self) -> bool: ...

    def capture(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle: ...


class SnapshotBuilder(Protocol):
    def build(
        self,
        event: object,
        origin: Origin,
        cutoff: datetime,
        mode: str,
        facts: Sequence[NormalizedFact] | None = None,
    ) -> FeatureSnapshot: ...


class LineageRepository(Protocol):
    def resolve_facts(self, fact_ids: list[str]) -> list[NormalizedFact]: ...

    def resolve_manifests(self, manifest_ids: list[str]) -> list[CaptureManifest]: ...


class ForecastPredictor(Protocol):
    def predict(
        self,
        snapshot: FeatureSnapshot,
        artifact: ArtifactBinding,
        *,
        origin_run_id: str,
        obligation: OriginObligation,
        created_at_utc: datetime,
        context: ForecastExecutionContext,
    ) -> Prediction: ...


class MarketLayer(Protocol):
    def evaluate(
        self, prediction: Prediction, market_payload: object, decision_at_utc: datetime
    ) -> MarketEvaluation: ...


class ForecastWorkflow:
    def __init__(
        self,
        *,
        repositories: ForecastRepositories,
        required_capture: RequiredCapture,
        market_capture: OptionalMarketCapture,
        feature_builder: SnapshotBuilder,
        lineage_repository: LineageRepository,
        artifact_registry: ArtifactRegistry,
        predictor: ForecastPredictor,
        market_layer: MarketLayer,
        clock: Callable[[], datetime],
        code_sha: str,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", code_sha) is None:
            raise ValueError("code_sha must be a Git SHA")
        self.repositories = repositories
        self.required_capture = required_capture
        self.market_capture = market_capture
        self.feature_builder = feature_builder
        self.lineage_repository = lineage_repository
        self.artifact_registry = artifact_registry
        self.predictor = predictor
        self.market_layer = market_layer
        self.clock = clock
        self.code_sha = code_sha
        self.id_factory = id_factory or (lambda: uuid4().hex)
        for repository in (repositories.prospective, repositories.replay):
            if type(repository) is DurableForecastRepository:
                repository.artifact_resolver = artifact_registry

    def run(
        self,
        obligation: OriginObligation,
        trigger: AttemptTrigger,
        context: ForecastExecutionContext,
    ) -> OriginRun | None:
        if not isinstance(context, ForecastExecutionContext):
            raise TypeError("context must be a validated ForecastExecutionContext")
        repository = self.repositories.for_context(context)
        if repository.namespace != context.namespace:
            raise RuntimeError("forecast repository namespace mismatch")
        key = self._execution_key(obligation, context)
        return repository.transact(
            key,
            lambda: self._run_locked(repository, obligation, trigger, context, key),
        )

    @staticmethod
    def _execution_key(obligation: OriginObligation, context: ForecastExecutionContext) -> str:
        if context.mode == "live":
            return obligation.idempotency_key
        if context.as_of_utc is None or context.reconstruction_reason is None:
            raise ValueError("validated replay context is missing identity fields")
        identity = (
            f"{obligation.idempotency_key}|{context.namespace}|"
            f"{context.as_of_utc.isoformat()}|{context.reconstruction_reason}"
        )
        return f"{obligation.idempotency_key}:replay:{sha256(identity.encode()).hexdigest()}"

    def _run_locked(
        self,
        repository: ForecastRepository,
        obligation: OriginObligation,
        trigger: AttemptTrigger,
        context: ForecastExecutionContext,
        key: str,
    ) -> OriginRun | None:
        prior = repository.validated_terminal_run(key, obligation, context)
        if prior is not None:
            return prior
        recovered = repository.recover_prepared_terminal(
            key,
            obligation,
            context,
            deadline=obligation.window.window_closes_at_utc,
            clock=self.clock,
        )
        if recovered is not None:
            return recovered
        initial_now = self.clock()
        persisted_obligation = repository.load_obligation(key)
        try:
            registry_bindings = self.artifact_registry.for_origin(obligation.origin)
            registry_error: Exception | None = None
        except Exception as error:  # noqa: BLE001 - champion lane fails closed below
            registry_bindings = ()
            registry_error = error
        if persisted_obligation is not None:
            champion = persisted_obligation.champion_binding
            if champion is None:
                registry_error = registry_error or ValueError(
                    "origin has no frozen champion selection"
                )
                selected_bindings: tuple[ArtifactBinding, ...] = ()
            else:
                selected_bindings = (champion,) + tuple(
                    item for item in registry_bindings if item.model_role == "challenger"
                )
        else:
            try:
                champion = self._resolve_champion(registry_bindings, obligation.origin)
                selected_bindings = registry_bindings
            except Exception as error:  # noqa: BLE001 - committed as required failure when due
                champion = None
                selected_bindings = ()
                registry_error = registry_error or error
        if context.mode == "live" and initial_now < obligation.window.window_opens_at_utc:
            if champion is not None:
                repository.register_obligation(key, obligation, context, champion)
            return None
        repository.register_obligation(key, obligation, context, champion)
        origin_run_id = sha256(f"{context.namespace}|{key}".encode()).hexdigest()
        prior_attempt = repository.latest_attempt(origin_run_id)
        if prior_attempt is not None and prior_attempt.status == "STARTED":
            prior_attempt = RunAttempt.model_validate(
                prior_attempt.model_copy(
                    update={
                        "ended_at_utc": initial_now,
                        "status": "FAILED",
                        "safe_error_category": "INTERRUPTED_ATTEMPT",
                    }
                ).model_dump()
            )
            repository.append_attempt(prior_attempt)
        attempt = RunAttempt(
            attempt_id=self.id_factory(),
            origin_run_id=origin_run_id,
            retry_of_attempt_id=(None if prior_attempt is None else prior_attempt.attempt_id),
            trigger=trigger,
            started_at_utc=initial_now,
            ended_at_utc=None,
            status="STARTED",
            safe_error_category=None,
        )
        repository.append_attempt(attempt)
        challengers: tuple[ArtifactBinding, ...] = ()
        if champion is not None:
            champion, challengers = self._select_artifacts(
                repository,
                obligation,
                attempt,
                selected_bindings,
                champion,
            )
        if self._live_after_close(obligation, context):
            return self._commit_terminal(
                repository,
                obligation,
                attempt,
                context,
                RunStatus.MISSED,
                ("WINDOW_CLOSED",),
                None,
            )
        try:
            if self._live_before_open(obligation, context):
                return None
            if self._live_after_close(obligation, context):
                return self._commit_terminal(
                    repository,
                    obligation,
                    attempt,
                    context,
                    RunStatus.MISSED,
                    ("WINDOW_CLOSED",),
                    None,
                )
            football = (
                self.required_capture.capture_live(obligation, attempt.attempt_id)
                if context.mode == "live"
                else self.required_capture.capture_replay(
                    obligation,
                    attempt.attempt_id,
                    self._replay_cutoff(context),
                )
            )
            if self._append_capture(repository, football, obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
            if registry_error is not None or champion is None:
                raise registry_error or ValueError("origin has no frozen champion selection")
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
            market, market_reason = self._capture_optional_market(
                repository, obligation, attempt, context
            )
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
            decision_at = context.as_of_utc if context.mode == "replay" else self.clock()
            if decision_at is None:
                raise ValueError("forecast decision cutoff is missing")
            snapshot = self._build_snapshot(
                obligation.event,
                obligation.origin,
                decision_at,
                mode=context.mode,
                facts=tuple(
                    record
                    for record in football.records
                    if isinstance(record, NormalizedFact)
                ),
            )
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
            self._validate_snapshot(
                snapshot,
                obligation,
                champion,
                decision_at,
                context,
                self._required_manifests(football, attempt, context),
            )
            champion_prediction = self.predictor.predict(
                snapshot,
                champion,
                origin_run_id=origin_run_id,
                obligation=obligation,
                created_at_utc=decision_at,
                context=context,
            )
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
            self._validate_prediction(
                champion_prediction,
                champion,
                snapshot,
                obligation,
                origin_run_id,
                context,
            )
            predictions = [champion_prediction]
            prediction_ids = {champion_prediction.prediction_id}
            for challenger in challengers:
                try:
                    prediction = self.predictor.predict(
                        snapshot,
                        challenger,
                        origin_run_id=origin_run_id,
                        obligation=obligation,
                        created_at_utc=decision_at,
                        context=context,
                    )
                    if self._live_after_close(obligation, context):
                        return self._late_terminal(repository, obligation, attempt, context)
                    self._validate_prediction(
                        prediction,
                        challenger,
                        snapshot,
                        obligation,
                        origin_run_id,
                        context,
                    )
                    if prediction.prediction_id in prediction_ids:
                        raise ValueError("challenger prediction ID duplicates champion/peer")
                    prediction_ids.add(prediction.prediction_id)
                    predictions.append(prediction)
                except Exception as error:  # noqa: BLE001 - challenger isolation
                    self._record_optional_failure(
                        repository, attempt, obligation, challenger.artifact_id, error
                    )
                    if self._live_after_close(obligation, context):
                        return self._late_terminal(repository, obligation, attempt, context)
            evaluation, evaluation_reason = self._evaluate_optional_market(
                champion_prediction,
                champion,
                market,
                obligation,
                decision_at,
                context,
            )
            market_reason = market_reason or evaluation_reason
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
        except Exception as error:  # noqa: BLE001 - required lane fails closed
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
            return self._commit_terminal(
                repository,
                obligation,
                attempt,
                context,
                RunStatus.FAILED,
                ("REQUIRED_FORECAST_FAILURE",),
                self._safe_error_category(error),
            )
        return self._commit_success(
            repository,
            obligation,
            attempt,
            context,
            snapshot,
            predictions,
            evaluation,
            market_reason,
            champion,
        )

    def _late_terminal(
        self,
        repository: ForecastRepository,
        obligation: OriginObligation,
        attempt: RunAttempt,
        context: ForecastExecutionContext,
    ) -> OriginRun:
        return self._commit_terminal(
            repository,
            obligation,
            attempt,
            context,
            RunStatus.MISSED,
            ("EXECUTION_CROSSED_WINDOW",),
            None,
        )

    def _capture_optional_market(
        self,
        repository: ForecastRepository,
        obligation: OriginObligation,
        attempt: RunAttempt,
        context: ForecastExecutionContext,
    ) -> tuple[CaptureBundle | None, str | None]:
        if context.mode == "replay":
            return None, "REPLAY_MARKET_DISABLED"
        if not self.market_capture.enabled:
            return None, "MARKET_CAPTURE_DISABLED"
        try:
            captured = self.market_capture.capture(obligation, attempt.attempt_id)
            self._validate_optional_capture(captured, obligation, attempt, context)
            if self._append_capture(repository, captured, obligation, context):
                return captured, "MARKET_CAPTURE_COMPLETED_OUTSIDE_WINDOW"
            return captured, None
        except BudgetExceeded:
            return None, "MARKET_BUDGET_EXCEEDED"
        except MarketCaptureDisabled:
            return None, "MARKET_CAPTURE_DISABLED"
        except MarketCaptureStale:
            return None, "MARKET_CAPTURE_STALE"
        except MarketSchemaDrift:
            return None, "MARKET_SCHEMA_DRIFT"
        except ValueError:
            return None, "MARKET_CAPTURE_LINEAGE_INVALID"
        except Exception as error:  # noqa: BLE001 - optional capture degrades
            return None, f"MARKET_CAPTURE_{self._safe_error_category(error)}"

    def _evaluate_optional_market(
        self,
        champion: Prediction,
        champion_binding: ArtifactBinding,
        market: CaptureBundle | None,
        obligation: OriginObligation,
        cutoff: datetime,
        context: ForecastExecutionContext,
    ) -> tuple[MarketEvaluation, str | None]:
        if market is None or context.mode == "replay":
            return MarketEvaluation(None, ()), None
        try:
            evaluation = self.market_layer.evaluate(champion, market.payload, cutoff)
            if evaluation.comparator is not None:
                self._validate_market_evaluation(
                    evaluation,
                    champion,
                    champion_binding,
                    obligation,
                    market,
                    cutoff,
                )
        except Exception as error:  # noqa: BLE001 - optional evaluation degrades
            category = (
                "LINEAGE_INVALID"
                if isinstance(error, ValueError)
                else self._safe_error_category(error)
            )
            return MarketEvaluation(None, ()), f"MARKET_EVALUATION_{category}"
        if evaluation.comparator is None:
            return MarketEvaluation(None, ()), "MARKET_NO_ELIGIBLE_COMPARATOR"
        return evaluation, None

    def _validate_optional_capture(
        self,
        capture: CaptureBundle,
        obligation: OriginObligation,
        attempt: RunAttempt,
        context: ForecastExecutionContext,
    ) -> None:
        manifests = tuple(row for row in capture.records if isinstance(row, CaptureManifest))
        quotes = tuple(row for row in capture.records if isinstance(row, MoneylineQuote))
        if (
            context.mode != "live"
            or not manifests
            or not quotes
            or len({row.capture_id for row in manifests}) != len(manifests)
            or any(row.run_id != attempt.attempt_id for row in manifests)
        ):
            raise ValueError("optional market capture lineage is invalid")
        manifest_by_id = {row.capture_id: row for row in manifests}
        resolved = self.lineage_repository.resolve_manifests([row.capture_id for row in manifests])
        if resolved != list(manifests):
            raise ValueError("resolved optional manifests differ from captured manifests")
        if len({row.quote_id for row in quotes}) != len(quotes):
            raise ValueError("optional market capture quote IDs are duplicated")
        for quote in quotes:
            manifest = manifest_by_id.get(quote.capture_id)
            if manifest is None or (
                quote.canonical_event_id != obligation.event.canonical_event_id
                or quote.source_event_id not in obligation.event.source_event_ids.values()
                or quote.response_received_at_utc != manifest.response_received_at_utc
                or quote.provider_last_update_at_utc > quote.response_received_at_utc
                or quote.provenance_grade is not ProvenanceGrade.A
            ):
                raise ValueError("optional quote does not exactly match capture manifest")

    def _append_capture(
        self,
        repository: ForecastRepository,
        capture: CaptureBundle,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
    ) -> bool:
        for record in capture.records:
            repository.append_capture_record(record)
            if self._live_after_close(obligation, context):
                return True
        return False

    @staticmethod
    def _replay_cutoff(context: ForecastExecutionContext) -> datetime:
        if context.as_of_utc is None:
            raise ValueError("replay context requires a cutoff")
        return context.as_of_utc

    def _build_snapshot(
        self,
        event: EventVersion,
        origin: Origin,
        cutoff: datetime,
        *,
        mode: str,
        facts: Sequence[NormalizedFact],
    ) -> FeatureSnapshot:
        parameters = inspect.signature(self.feature_builder.build).parameters.values()
        if not any(
            parameter.name == "facts" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        ):
            return self.feature_builder.build(event, origin, cutoff, mode)
        return self.feature_builder.build(event, origin, cutoff, mode, facts=facts)

    @staticmethod
    def _required_manifests(
        capture: CaptureBundle,
        attempt: RunAttempt,
        context: ForecastExecutionContext,
    ) -> tuple[CaptureManifest, ...]:
        manifests = [record for record in capture.records if isinstance(record, CaptureManifest)]
        if not manifests:
            raise ValueError("required capture must return its canonical manifests")
        identifiers = [manifest.capture_id for manifest in manifests]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("required capture returned duplicate manifest IDs")
        if any(manifest.run_id != attempt.attempt_id for manifest in manifests):
            lane = "live" if context.mode == "live" else "replay reconstruction"
            raise ValueError(f"required {lane} manifest does not belong to current attempt")
        return tuple(manifests)

    def _validate_snapshot(
        self,
        snapshot: FeatureSnapshot,
        obligation: OriginObligation,
        champion: ArtifactBinding,
        cutoff: datetime,
        context: ForecastExecutionContext,
        required_manifests: tuple[CaptureManifest, ...],
    ) -> None:
        required_manifest_ids = {item.capture_id for item in required_manifests}
        if (
            snapshot.canonical_event_id != obligation.event.canonical_event_id
            or snapshot.event_version != obligation.event.event_version
            or snapshot.origin is not obligation.origin
            or snapshot.decision_at_utc != cutoff
            or snapshot.feature_policy_version != champion.feature_policy_version
            or snapshot.feature_schema_version != champion.feature_schema_version
            or snapshot.provenance_grade is not context.provenance_grade
            or snapshot.status.value != "COMPLETE"
            or bool(snapshot.reason_codes)
            or set(snapshot.input_manifest_ids) != required_manifest_ids
            or len(snapshot.input_manifest_ids) != len(required_manifest_ids)
        ):
            raise ValueError("feature snapshot identity/policy does not match obligation")
        manifests = self.lineage_repository.resolve_manifests(snapshot.input_manifest_ids)
        if [item.capture_id for item in manifests] != snapshot.input_manifest_ids:
            raise ValueError("feature snapshot manifest lineage could not be resolved exactly")
        captured_by_id = {item.capture_id: item for item in required_manifests}
        if any(captured_by_id.get(item.capture_id) != item for item in manifests):
            raise ValueError("resolved feature manifest differs from captured manifest")
        manifest_ids = set(snapshot.input_manifest_ids)
        for manifest in manifests:
            if context.mode == "live":
                if manifest.response_received_at_utc > cutoff:
                    raise ValueError("live feature manifest was received after cutoff")
            elif not (
                manifest.response_received_at_utc <= cutoff
                or (
                    manifest.source_snapshot_at_utc is not None
                    and manifest.source_snapshot_at_utc <= cutoff
                )
            ):
                raise ValueError("replay manifest has no eligible historical source snapshot")
        facts = self.lineage_repository.resolve_facts(snapshot.input_fact_ids)
        if [fact.fact_id for fact in facts] != snapshot.input_fact_ids:
            raise ValueError("feature snapshot fact lineage could not be resolved exactly")
        for fact in facts:
            if fact.capture_id not in manifest_ids:
                raise ValueError("feature fact does not belong to exact manifest set")
            if not set(fact.lineage_capture_ids).issubset(manifest_ids):
                raise ValueError("feature fact lineage does not belong to exact manifest set")
            if fact.available_at_utc > cutoff:
                raise ValueError("feature fact was unavailable at cutoff")
            if context.mode == "live":
                if fact.captured_at_utc > cutoff or fact.provenance_grade is not ProvenanceGrade.A:
                    raise ValueError("live feature fact is not prospective Grade A evidence")
            elif not (
                (fact.provenance_grade is ProvenanceGrade.A and fact.captured_at_utc <= cutoff)
                or (
                    fact.provenance_grade in {ProvenanceGrade.B, ProvenanceGrade.C}
                    and fact.archive_published_at_utc is not None
                    and fact.archive_published_at_utc <= cutoff
                )
            ):
                raise ValueError("replay feature fact lacks eligible point-in-time lineage")

    @staticmethod
    def _validate_prediction(
        prediction: Prediction,
        binding: ArtifactBinding,
        snapshot: FeatureSnapshot,
        obligation: OriginObligation,
        origin_run_id: str,
        context: ForecastExecutionContext,
    ) -> None:
        expected_policies = dict(binding.policy_versions)
        expected_policies["forecast"] = obligation.policy_version
        if (
            prediction.origin_run_id != origin_run_id
            or prediction.canonical_event_id != obligation.event.canonical_event_id
            or prediction.event_version != obligation.event.event_version
            or prediction.origin is not obligation.origin
            or prediction.target_at_utc != obligation.window.target_at_utc
            or prediction.decision_at_utc != snapshot.decision_at_utc
            or prediction.feature_snapshot_id != snapshot.snapshot_id
            or prediction.model_artifact_id != binding.artifact_id
            or prediction.model_role != binding.model_role
            or prediction.model_lane != binding.model_lane
            or prediction.calibrator_artifact_id != binding.expected_calibrator_artifact_id
            or prediction.policy_versions != expected_policies
            or prediction.provenance_grade is not context.provenance_grade
            or prediction.status.value != "COMPLETE"
            or bool(prediction.reason_codes) != (context.mode == "replay")
        ):
            raise ValueError("predictor output does not match selected lineage/execution")
        if (
            context.mode == "replay"
            and context.reconstruction_reason not in prediction.reason_codes
        ):
            raise ValueError("replay prediction must carry reconstruction reason")

    @staticmethod
    def _validate_market_evaluation(
        evaluation: MarketEvaluation,
        champion: Prediction,
        champion_binding: ArtifactBinding,
        obligation: OriginObligation,
        capture: CaptureBundle,
        cutoff: datetime,
    ) -> None:
        comparator = evaluation.comparator
        if comparator is None or (
            comparator.origin_run_id != champion.origin_run_id
            or comparator.canonical_event_id != champion.canonical_event_id
            or comparator.origin is not champion.origin
            or comparator.decision_at_utc != cutoff
            or comparator.market_policy_version != champion_binding.market_policy_version
            or comparator.p_tie_shared_prior != champion.p_tie
            or not comparator.quote_ids
            or comparator.r_home_market != median_market_home(evaluation.quotes)
        ):
            raise ValueError("market comparator lineage does not match champion")
        captured_quotes = {
            item.quote_id: item for item in capture.records if isinstance(item, MoneylineQuote)
        }
        quotes = {item.quote_id: item for item in evaluation.quotes}
        if (
            len(quotes) != len(evaluation.quotes)
            or [item.quote_id for item in evaluation.quotes] != comparator.quote_ids
            or quotes != captured_quotes
            or len({item.book_key for item in evaluation.quotes}) != len(evaluation.quotes)
        ):
            raise ValueError("market quote set does not exactly match comparator")
        capture_ids = {
            record.capture_id for record in capture.records if isinstance(record, CaptureManifest)
        }
        if not capture_ids:
            raise ValueError("market evaluation has no current capture manifest")
        for quote in evaluation.quotes:
            if (
                quote.canonical_event_id != champion.canonical_event_id
                or quote.source_event_id not in obligation.event.source_event_ids.values()
                or quote.capture_id not in capture_ids
                or quote.response_received_at_utc > cutoff
                or quote.provider_last_update_at_utc > cutoff
                or quote.provider_last_update_at_utc > quote.response_received_at_utc
                or quote.provenance_grade is not ProvenanceGrade.A
                or {selection.side: selection.team for selection in quote.selections}
                != {"home": obligation.event.home_team, "away": obligation.event.away_team}
            ):
                raise ValueError("market quote lineage does not match comparator cutoff")
        candidates = {item.candidate_id: item for item in evaluation.candidates}
        if len(candidates) != len(evaluation.candidates):
            raise ValueError("market candidate IDs must be unique")
        decisions = {item.decision_id: item for item in evaluation.decisions}
        if len(decisions) != len(evaluation.decisions):
            raise ValueError("market decision IDs must be unique")
        linked_decisions = {
            item.candidate_id: item
            for item in evaluation.decisions
            if item.candidate_id is not None
        }
        if len(linked_decisions) != sum(
            item.candidate_id is not None for item in evaluation.decisions
        ) or set(linked_decisions) != set(candidates):
            raise ValueError("market candidates and accepted decisions must be bijective")
        for decision in evaluation.decisions:
            if (
                decision.prediction_id != champion.prediction_id
                or decision.canonical_event_id != champion.canonical_event_id
                or decision.origin is not champion.origin
                or decision.evaluated_at_utc != cutoff
                or decision.policy_version != champion_binding.candidate_policy_version
                or (decision.quote_id is not None and decision.quote_id not in quotes)
            ):
                raise ValueError("betting decision lineage does not match champion")
            if decision.candidate_id is not None:
                candidate = candidates.get(decision.candidate_id)
                if candidate is None:
                    raise ValueError("candidate lineage does not match comparator/champion")
                candidate_quote = quotes.get(decision.quote_id or "")
                matching_selections = (
                    []
                    if candidate_quote is None
                    else [
                        item for item in candidate_quote.selections if item.side == candidate.side
                    ]
                )
                if (
                    candidate_quote is None
                    or candidate.prediction_id != champion.prediction_id
                    or candidate.origin is not champion.origin
                    or candidate.decision_at_utc != cutoff
                    or candidate.quote_id != decision.quote_id
                    or candidate.side != decision.side
                    or candidate.decision_status != decision.status
                    or candidate.policy_version != decision.policy_version
                    or candidate.policy_version != champion_binding.candidate_policy_version
                    or candidate.reason_codes != decision.reason_codes
                    or decision.status != "candidate"
                    or len(matching_selections) != 1
                    or candidate.decimal_price != matching_selections[0].decimal_price
                    or candidate.raw_p_win
                    != (champion.p_home if candidate.side == "home" else champion.p_away)
                    or candidate.p_loss
                    != (champion.p_away if candidate.side == "home" else champion.p_home)
                    or candidate.p_push != champion.p_tie
                    or candidate.buffered_p_win + candidate.buffered_p_loss + candidate.p_push
                    != Decimal(1)
                    or candidate.displayed_ev_per_unit
                    != displayed_ev(
                        candidate.buffered_p_win,
                        candidate.buffered_p_loss,
                        candidate.decimal_price,
                    )
                    or candidate.quarter_kelly_fraction
                    != quarter_kelly(
                        candidate.buffered_p_win,
                        candidate.buffered_p_loss,
                        candidate.decimal_price,
                    )
                    or candidate.displayed_ev_per_unit <= 0
                ):
                    raise ValueError("candidate lineage does not match comparator/champion")
            elif decision.status != "rejected" or not decision.reason_codes:
                raise ValueError("unlinked market decision must be a typed rejection")

    def _select_artifacts(
        self,
        repository: ForecastRepository,
        obligation: OriginObligation,
        attempt: RunAttempt,
        bindings: tuple[ArtifactBinding, ...],
        champion: ArtifactBinding,
    ) -> tuple[ArtifactBinding, tuple[ArtifactBinding, ...]]:
        repository.append_artifact_selection(
            ArtifactSelection(attempt.attempt_id, attempt.origin_run_id, champion)
        )
        challenger_counts: dict[str, int] = {}
        for item in bindings:
            if item.model_role == "challenger":
                challenger_counts[item.artifact_id] = challenger_counts.get(item.artifact_id, 0) + 1
        challengers: list[ArtifactBinding] = []
        for item in sorted(
            (binding for binding in bindings if binding.model_role == "challenger"),
            key=lambda binding: binding.artifact_id,
        ):
            if (
                item.artifact_id == champion.artifact_id
                or challenger_counts[item.artifact_id] != 1
                or item.origin is not obligation.origin
                or not item.frozen
                or not item.verified
            ):
                self._record_optional_failure(
                    repository,
                    attempt,
                    obligation,
                    item.artifact_id,
                    ValueError("invalid challenger binding"),
                )
                continue
            repository.append_artifact_selection(
                ArtifactSelection(attempt.attempt_id, attempt.origin_run_id, item)
            )
            challengers.append(item)
        return champion, tuple(challengers)

    @staticmethod
    def _resolve_champion(bindings: tuple[ArtifactBinding, ...], origin: Origin) -> ArtifactBinding:
        all_champions = tuple(item for item in bindings if item.model_role == "champion")
        valid_champions = tuple(
            item
            for item in all_champions
            if item.origin is origin and item.frozen and item.verified
        )
        if len(all_champions) != 1 or len(valid_champions) != 1:
            raise ValueError("origin requires exactly one frozen verified champion")
        return valid_champions[0]

    def _record_optional_failure(
        self,
        repository: ForecastRepository,
        attempt: RunAttempt,
        obligation: OriginObligation,
        artifact_id: str,
        error: Exception,
    ) -> None:
        repository.append_capture_record(
            OptionalFailure(
                attempt.attempt_id,
                attempt.origin_run_id,
                obligation.origin,
                artifact_id,
                self._safe_error_category(error),
            )
        )

    def _commit_success(
        self,
        repository: ForecastRepository,
        obligation: OriginObligation,
        attempt: RunAttempt,
        context: ForecastExecutionContext,
        snapshot: FeatureSnapshot,
        predictions: list[Prediction],
        evaluation: MarketEvaluation,
        market_reason: str | None,
        champion_binding: ArtifactBinding,
    ) -> OriginRun:
        repository.append_feature_snapshot(snapshot)
        if self._live_after_close(obligation, context):
            return self._late_terminal(repository, obligation, attempt, context)
        for prediction in predictions:
            repository.append_prediction(prediction)
            if self._live_after_close(obligation, context):
                return self._late_terminal(repository, obligation, attempt, context)
        if evaluation.comparator is not None:
            repository.append_market_comparator(evaluation.comparator)
            repository.append_market_validation(
                MarketValidationBinding(
                    evaluation.comparator.market_comparator_id,
                    predictions[0].prediction_id,
                    champion_binding.market_policy_version,
                    champion_binding.candidate_policy_version,
                )
            )
        for decision in evaluation.decisions:
            repository.append_betting_decision(decision)
        for candidate in evaluation.candidates:
            repository.append_candidate(candidate)
        if self._live_after_close(obligation, context):
            return self._late_terminal(repository, obligation, attempt, context)
        status = RunStatus.FOOTBALL_ONLY if market_reason is not None else RunStatus.COMPLETE
        return self._commit_terminal(
            repository,
            obligation,
            attempt,
            context,
            status,
            () if market_reason is None else (market_reason,),
            None,
            decision_at=snapshot.decision_at_utc,
            prediction_ids=[item.prediction_id for item in predictions],
            comparator_id=(
                None
                if evaluation.comparator is None
                else evaluation.comparator.market_comparator_id
            ),
        )

    def _commit_terminal(
        self,
        repository: ForecastRepository,
        obligation: OriginObligation,
        attempt: RunAttempt,
        context: ForecastExecutionContext,
        status: RunStatus,
        reason_codes: tuple[str, ...],
        error_category: str | None,
        *,
        decision_at: datetime | None = None,
        prediction_ids: list[str] | None = None,
        comparator_id: str | None = None,
    ) -> OriginRun:
        ended_at = self.clock()
        if context.mode == "live" and ended_at > obligation.window.window_closes_at_utc:
            status = RunStatus.MISSED
            reason_codes = ("EXECUTION_CROSSED_WINDOW",)
            error_category = None
            decision_at = None
            prediction_ids = []
            comparator_id = None
        marker_time = self.clock()
        if context.mode == "live" and marker_time > obligation.window.window_closes_at_utc:
            status = RunStatus.MISSED
            reason_codes = ("EXECUTION_CROSSED_WINDOW",)
            error_category = "EXECUTION_CROSSED_WINDOW"
            decision_at = None
            prediction_ids = []
            comparator_id = None
        terminal_attempt = RunAttempt.model_validate(
            attempt.model_copy(
                update={
                    "ended_at_utc": marker_time if status is RunStatus.MISSED else ended_at,
                    "status": (
                        "FAILED" if status in {RunStatus.FAILED, RunStatus.MISSED} else "COMPLETE"
                    ),
                    "safe_error_category": (
                        error_category
                        if status is RunStatus.FAILED
                        else "EXECUTION_CROSSED_WINDOW"
                        if status is RunStatus.MISSED
                        else None
                    ),
                }
            ).model_dump()
        )
        run = OriginRun(
            origin_run_id=attempt.origin_run_id,
            canonical_event_id=obligation.event.canonical_event_id,
            event_version=obligation.event.event_version,
            origin=obligation.origin,
            forecast_policy_version=obligation.policy_version,
            target_at_utc=obligation.window.target_at_utc,
            window_opens_at_utc=obligation.window.window_opens_at_utc,
            window_closes_at_utc=obligation.window.window_closes_at_utc,
            run_started_at_utc=attempt.started_at_utc,
            decision_at_utc=decision_at,
            status=status,
            attempt_ids=[attempt.attempt_id],
            prediction_ids=prediction_ids or [],
            market_comparator_id=comparator_id,
            reason_codes=list(reason_codes),
            code_sha=self.code_sha,
            created_at_utc=marker_time,
        )
        return repository.commit_terminal(
            self._execution_key(obligation, context),
            run,
            terminal_attempt,
            deadline=obligation.window.window_closes_at_utc,
            context=context,
            clock=self.clock,
        )

    def _live_after_close(
        self, obligation: OriginObligation, context: ForecastExecutionContext
    ) -> bool:
        return context.mode == "live" and self.clock() > obligation.window.window_closes_at_utc

    def _live_before_open(
        self, obligation: OriginObligation, context: ForecastExecutionContext
    ) -> bool:
        return context.mode == "live" and self.clock() < obligation.window.window_opens_at_utc

    @staticmethod
    def _safe_error_category(error: Exception) -> str:
        name = re.sub(r"[^A-Za-z0-9]+", "_", type(error).__name__).upper().strip("_")
        return (name or "UNKNOWN_ERROR")[:64]


class DurableForecastRepository:
    """File-locked immutable forecast coordinator with marker-only visibility."""

    def __init__(
        self,
        root: Path,
        namespace: RepositoryNamespace,
        *,
        artifact_resolver: ArtifactRegistry | None = None,
    ) -> None:
        if namespace not in {"prospective", "replay"}:
            raise ValueError("durable forecast repository namespace is invalid")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.namespace = namespace
        self.artifact_resolver = artifact_resolver
        self.ledger = LedgerStore(root)
        self._terminal_namespace = f"forecast-terminal-{namespace}"
        self._terminal_receipt_namespace = f"forecast-terminal-receipt-{namespace}"
        self._terminal_prepared_namespace = f"forecast-terminal-prepared-{namespace}"
        self._attempt_namespace = f"forecast-attempt-{namespace}"
        self._obligation_namespace = f"forecast-obligation-{namespace}"

    @property
    def storage_identity(self) -> str:
        return str(self.root.resolve())

    @property
    def backing_identity(self) -> tuple[int, int]:
        stat = os.stat(self.root, follow_symlinks=True)
        return stat.st_dev, stat.st_ino

    def _record_namespace(self, kind: str) -> str:
        return f"forecast-{kind}-{self.namespace}"

    def transact(self, key: str, operation: Callable[[], OriginRun | None]) -> OriginRun | None:
        digest = sha256(key.encode()).hexdigest()
        lock_path = self.root / "locks" / self.namespace / f"{digest}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return operation()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def terminal_run(self, key: str) -> OriginRun | None:
        lock_path = self._publication_lock_path(key)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                run = self._read_receipted_terminal(key)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return run

    def _read_receipted_terminal(self, key: str) -> OriginRun | None:
        value = self.ledger.read(self._terminal_namespace, key)
        if value is None:
            return None
        try:
            run = OriginRun.model_validate(value)
        except (TypeError, ValueError) as error:
            raise DataIntegrityError("committed terminal run is invalid") from error
        digest = sha256(canonical_json_bytes(run)).hexdigest()
        receipt_key = self._terminal_receipt_key(key, digest)
        receipt_value = self.ledger.read(self._terminal_receipt_namespace, receipt_key)
        if receipt_value is None:
            return None
        receipt = self._terminal_receipt_from_json(receipt_value)
        if receipt.execution_key != key or receipt.run_content_sha256 != digest:
            raise DataIntegrityError("terminal receipt does not match durable candidate")
        receipt_published_at = self._terminal_receipt_publication_time(receipt_key)
        if (
            self.namespace == "prospective"
            and run.status in {RunStatus.COMPLETE, RunStatus.FOOTBALL_ONLY}
            and (
                receipt.candidate_durable_at_utc > run.window_closes_at_utc
                or receipt_published_at
                >= self._terminal_receipt_close_threshold(run.window_closes_at_utc)
            )
        ):
            return None
        return run

    @staticmethod
    def _terminal_receipt_key(key: str, run_digest: str) -> str:
        return f"{key}|{run_digest}"

    def _terminal_receipt_publication_time(self, receipt_key: str) -> datetime:
        marker = self.ledger.marker_path(self._terminal_receipt_namespace, receipt_key)
        try:
            metadata = marker.stat()
            publication_ns = metadata.st_ctime_ns
            seconds, nanoseconds = divmod(publication_ns, 1_000_000_000)
        except OSError as error:
            raise DataIntegrityError("terminal receipt marker metadata is unreadable") from error
        return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanoseconds // 1_000)

    @staticmethod
    def _terminal_receipt_close_threshold(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("terminal receipt close time must be UTC")
        return value.replace(microsecond=0)

    @staticmethod
    def _terminal_receipt_from_json(
        value: dict[str, object],
    ) -> TerminalPublicationReceipt:
        try:
            fields = {
                "execution_key",
                "run_content_sha256",
                "candidate_durable_at_utc",
                "state",
            }
            if set(value) != fields or any(not isinstance(value[name], str) for name in fields):
                raise TypeError("terminal receipt fields must be exact strings")
            return TerminalPublicationReceipt(
                execution_key=cast(str, value["execution_key"]),
                run_content_sha256=cast(str, value["run_content_sha256"]),
                candidate_durable_at_utc=datetime.fromisoformat(
                    cast(str, value["candidate_durable_at_utc"])
                ),
                state=value["state"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("terminal publication receipt is invalid") from error

    def validated_terminal_run(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
    ) -> OriginRun | None:
        if self.terminal_run(key) is None:
            return None
        graph = self.load_committed_graph(key)
        if graph.obligation != self._obligation_record(
            key, obligation, context, graph.obligation.champion_binding
        ):
            raise DataIntegrityError("terminal forecast does not match requested obligation")
        return graph.run

    def recover_prepared_terminal(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
        *,
        deadline: datetime,
        clock: Callable[[], datetime],
    ) -> OriginRun | None:
        committed = self.terminal_run(key)
        if committed is not None:
            return committed
        registered = self.load_obligation(key)
        if registered is None:
            return None
        if registered != self._obligation_record(
            key, obligation, context, registered.champion_binding
        ):
            raise DataIntegrityError("prepared forecast does not match requested obligation")
        origin_run_id = sha256(f"{context.namespace}|{key}".encode()).hexdigest()
        latest = self.latest_attempt(origin_run_id)
        if latest is None or latest.status == "STARTED":
            return None
        candidates: list[OriginRun] = []
        for logical_id, value in self._records(self._terminal_prepared_namespace):
            try:
                run = OriginRun.model_validate(value)
            except (TypeError, ValueError) as error:
                raise DataIntegrityError("prepared terminal run is invalid") from error
            digest = sha256(canonical_json_bytes(run)).hexdigest()
            if logical_id != f"{key}|{digest}":
                if logical_id.startswith(f"{key}|"):
                    raise DataIntegrityError("prepared terminal identity is substituted")
                continue
            if run.origin_run_id != origin_run_id or run.attempt_ids != [latest.attempt_id]:
                continue
            try:
                self._assemble_graph(key, run, registered)
            except DataIntegrityError:
                continue
            candidates.append(run)
        if not candidates:
            return None
        if len(candidates) != 1:
            raise DataIntegrityError("prepared terminal recovery is ambiguous")
        return self.commit_terminal(
            key,
            candidates[0],
            latest,
            deadline=deadline,
            context=context,
            clock=clock,
        )

    def _publication_lock_path(self, key: str) -> Path:
        digest = sha256(key.encode()).hexdigest()
        path = self.root / "publication-locks" / self.namespace / f"{digest}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def register_obligation(
        self,
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
        champion_binding: ArtifactBinding | None,
    ) -> None:
        record = self._obligation_record(key, obligation, context, champion_binding)
        self.ledger.append(self._obligation_namespace, key, self._json_value(record))

    @staticmethod
    def _obligation_record(
        key: str,
        obligation: OriginObligation,
        context: ForecastExecutionContext,
        champion_binding: ArtifactBinding | None,
    ) -> ForecastObligationRecord:
        return ForecastObligationRecord(
            execution_key=key,
            event=obligation.event,
            origin=obligation.origin,
            target_at_utc=obligation.window.target_at_utc,
            window_opens_at_utc=obligation.window.window_opens_at_utc,
            window_closes_at_utc=obligation.window.window_closes_at_utc,
            forecast_policy_version=obligation.policy_version,
            mode=context.mode,
            namespace=context.namespace,
            provenance_grade=context.provenance_grade,
            as_of_utc=None if context.mode == "live" else context.as_of_utc,
            reconstruction_reason=context.reconstruction_reason,
            champion_binding=champion_binding,
        )

    def load_obligation(self, key: str) -> ForecastObligationRecord | None:
        value = self.ledger.read(self._obligation_namespace, key)
        return None if value is None else self.decode_obligation(value)

    def latest_attempt(self, origin_run_id: str) -> RunAttempt | None:
        records = [
            attempt
            for logical_id, value in self._records(self._attempt_namespace)
            if (attempt := self._attempt_record(logical_id, value)).origin_run_id == origin_run_id
        ]
        if not records:
            return None
        _, leaf = self._collapse_attempt_chain(records)
        return leaf

    @staticmethod
    def _collapse_attempt_chain(
        records: list[RunAttempt] | tuple[RunAttempt, ...],
    ) -> tuple[tuple[RunAttempt, ...], RunAttempt]:
        grouped: dict[str, list[RunAttempt]] = {}
        for item in records:
            grouped.setdefault(item.attempt_id, []).append(item)
        selected: dict[str, RunAttempt] = {}
        for attempt_id, states in grouped.items():
            terminal = [item for item in states if item.status != "STARTED"]
            if terminal:
                terminal.sort(
                    key=lambda item: (
                        item.ended_at_utc or item.started_at_utc,
                        item.status == "FAILED",
                        item.safe_error_category or "",
                    )
                )
                if len(terminal) > 1 and (
                    terminal[-1].ended_at_utc == terminal[-2].ended_at_utc
                    and terminal[-1] != terminal[-2]
                ):
                    raise DataIntegrityError("attempt has tied conflicting terminal states")
                selected[attempt_id] = terminal[-1]
            else:
                if any(item != states[0] for item in states[1:]):
                    raise DataIntegrityError("attempt has conflicting started states")
                selected[attempt_id] = states[0]
        children: dict[str, list[str]] = {item: [] for item in selected}
        roots: list[str] = []
        for item in selected.values():
            parent_id = item.retry_of_attempt_id
            if parent_id is None:
                roots.append(item.attempt_id)
                continue
            parent = selected.get(parent_id)
            if (
                parent is None
                or parent.ended_at_utc is None
                or item.started_at_utc < parent.ended_at_utc
            ):
                raise DataIntegrityError("attempt retry chain is dangling or unordered")
            children[parent_id].append(item.attempt_id)
        if len(roots) != 1 or any(len(items) > 1 for items in children.values()):
            raise DataIntegrityError("attempt retry chain must have one root and one leaf")
        order: list[RunAttempt] = []
        current = roots[0]
        while current not in {item.attempt_id for item in order}:
            order.append(selected[current])
            next_items = children[current]
            if not next_items:
                break
            current = next_items[0]
        if len(order) != len(selected):
            raise DataIntegrityError("attempt retry chain is cyclic or disconnected")
        return tuple(order), order[-1]

    def append_attempt(self, attempt: RunAttempt) -> None:
        ended = "started" if attempt.ended_at_utc is None else attempt.ended_at_utc.isoformat()
        self.ledger.append(
            self._attempt_namespace,
            f"{attempt.origin_run_id}|{attempt.attempt_id}|{attempt.status}|{ended}",
            attempt,
        )

    @classmethod
    def _json_value(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if is_dataclass(value) and not isinstance(value, type):
            return cls._json_value(asdict(value))
        if isinstance(value, dict):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item) for item in value]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(f"unsupported durable forecast record: {type(value).__name__}")

    def _append_record(self, kind: str, logical_id: str, record: object) -> None:
        self.ledger.append(self._record_namespace(kind), logical_id, self._json_value(record))

    def append_capture_record(self, record: object) -> None:
        if isinstance(record, CaptureManifest):
            self._append_record("manifest", record.capture_id, record)
        elif isinstance(record, NormalizedFact):
            self._append_record("fact", record.fact_id, record)
        elif isinstance(record, MoneylineQuote):
            self._append_record("quote", record.quote_id, record)
        elif isinstance(record, OptionalFailure):
            self._append_record(
                "optional-failure",
                f"{record.attempt_id}|{record.artifact_id}",
                record,
            )
        else:
            raise TypeError(f"unsupported forecast capture record: {type(record).__name__}")

    def append_artifact_selection(self, selection: ArtifactSelection) -> None:
        self._append_record(
            "artifact-selection",
            (
                f"{selection.attempt_id}|{selection.binding.model_role}|"
                f"{selection.binding.artifact_id}"
            ),
            selection,
        )

    def append_feature_snapshot(self, snapshot: FeatureSnapshot) -> None:
        self._append_record("snapshot", snapshot.snapshot_id, snapshot)

    def append_prediction(self, prediction: Prediction) -> None:
        self._append_record("prediction", prediction.prediction_id, prediction)

    def append_market_comparator(self, comparator: MarketComparator) -> None:
        self._append_record("comparator", comparator.market_comparator_id, comparator)

    def append_market_validation(self, binding: MarketValidationBinding) -> None:
        self._append_record("market-validation", binding.market_comparator_id, binding)

    def append_betting_decision(self, decision: BettingDecision) -> None:
        self._append_record("decision", decision.decision_id, decision)

    def append_candidate(self, candidate: DisplayedQuoteCandidate) -> None:
        self._append_record("candidate", candidate.candidate_id, candidate)

    def _records(self, namespace: str) -> list[tuple[str, dict[str, object]]]:
        directory = self.root / "commits" / namespace
        if not directory.exists():
            return []
        rows: list[tuple[str, dict[str, object]]] = []
        seen: set[str] = set()
        for marker_path in sorted(directory.glob("*.json")):
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DataIntegrityError("forecast commit marker is unreadable") from error
            fields = {"namespace", "idempotency_key", "content_sha256", "object_path"}
            if (
                not isinstance(marker, dict)
                or set(marker) != fields
                or not all(isinstance(item, str) for item in marker.values())
            ):
                raise DataIntegrityError("forecast commit marker fields are invalid")
            key = marker["idempotency_key"]
            if (
                marker["namespace"] != namespace
                or self.ledger.marker_path(namespace, key) != marker_path
            ):
                raise DataIntegrityError("forecast commit marker identity is substituted")
            if key in seen:
                raise DataIntegrityError("duplicate forecast logical record")
            seen.add(key)
            value = self.ledger.read(namespace, key)
            if value is None:
                raise DataIntegrityError("enumerated forecast record is missing")
            rows.append((key, value))
        return rows

    def _read_required(self, kind: str, logical_id: str) -> dict[str, object]:
        value = self.ledger.read(self._record_namespace(kind), logical_id)
        if value is None:
            raise DataIntegrityError(f"committed forecast {kind} is missing")
        return value

    @staticmethod
    def _typed_record(
        kind: str,
        logical_id: str,
        value: dict[str, object],
        model: type[ModelT],
        id_field: str,
    ) -> ModelT:
        try:
            record = model.model_validate(value)
        except (TypeError, ValueError) as error:
            raise DataIntegrityError(f"committed forecast {kind} is invalid") from error
        if getattr(record, id_field) != logical_id:
            raise DataIntegrityError(f"committed forecast {kind} logical ID is substituted")
        return record

    @staticmethod
    def _attempt_record(logical_id: str, value: dict[str, object]) -> RunAttempt:
        try:
            attempt = RunAttempt.model_validate(value)
        except (TypeError, ValueError) as error:
            raise DataIntegrityError("committed forecast attempt is invalid") from error
        ended = "started" if attempt.ended_at_utc is None else attempt.ended_at_utc.isoformat()
        expected = f"{attempt.origin_run_id}|{attempt.attempt_id}|{attempt.status}|{ended}"
        if logical_id != expected:
            raise DataIntegrityError("committed forecast attempt logical ID is substituted")
        return attempt

    @staticmethod
    def _obligation_from_json(value: dict[str, object]) -> ForecastObligationRecord:
        try:
            fields = {
                "execution_key",
                "event",
                "origin",
                "target_at_utc",
                "window_opens_at_utc",
                "window_closes_at_utc",
                "forecast_policy_version",
                "mode",
                "namespace",
                "provenance_grade",
                "as_of_utc",
                "reconstruction_reason",
                "champion_binding",
            }
            if set(value) != fields:
                raise TypeError("forecast obligation fields are not exact")
            string_fields = (
                "execution_key",
                "origin",
                "target_at_utc",
                "window_opens_at_utc",
                "window_closes_at_utc",
                "forecast_policy_version",
                "mode",
                "namespace",
                "provenance_grade",
            )
            if any(not isinstance(value[name], str) for name in string_fields):
                raise TypeError("forecast obligation primitive types are invalid")
            if not isinstance(value["event"], dict):
                raise TypeError("forecast obligation event must be an object")
            if value["as_of_utc"] is not None and not isinstance(value["as_of_utc"], str):
                raise TypeError("forecast obligation as-of must be a string or null")
            if value["reconstruction_reason"] is not None and not isinstance(
                value["reconstruction_reason"], str
            ):
                raise TypeError("forecast obligation reason must be a string or null")
            champion_value = value["champion_binding"]
            if champion_value is not None and not isinstance(champion_value, dict):
                raise TypeError("forecast obligation champion must be an object or null")
            return ForecastObligationRecord(
                execution_key=cast(str, value["execution_key"]),
                event=EventVersion.model_validate(value["event"]),
                origin=Origin(cast(str, value["origin"])),
                target_at_utc=datetime.fromisoformat(cast(str, value["target_at_utc"])),
                window_opens_at_utc=datetime.fromisoformat(cast(str, value["window_opens_at_utc"])),
                window_closes_at_utc=datetime.fromisoformat(
                    cast(str, value["window_closes_at_utc"])
                ),
                forecast_policy_version=cast(str, value["forecast_policy_version"]),
                mode=value["mode"],  # type: ignore[arg-type]
                namespace=value["namespace"],  # type: ignore[arg-type]
                provenance_grade=ProvenanceGrade(cast(str, value["provenance_grade"])),
                as_of_utc=(
                    None
                    if value["as_of_utc"] is None
                    else datetime.fromisoformat(value["as_of_utc"])
                ),
                reconstruction_reason=value["reconstruction_reason"],
                champion_binding=(
                    None
                    if champion_value is None
                    else DurableForecastRepository._artifact_binding_from_json(champion_value)
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("forecast obligation record is invalid") from error

    @classmethod
    def encode_obligation(cls, obligation: ForecastObligationRecord) -> dict[str, object]:
        value = cls._json_value(obligation)
        if not isinstance(value, dict):
            raise TypeError("forecast obligation must encode as an object")
        return value

    @staticmethod
    def decode_obligation(value: dict[str, object]) -> ForecastObligationRecord:
        return validate_obligation_record(DurableForecastRepository._obligation_from_json(value))

    @staticmethod
    def _optional_failure_from_json(value: dict[str, object]) -> OptionalFailure:
        try:
            fields = {
                "attempt_id",
                "origin_run_id",
                "origin",
                "artifact_id",
                "safe_error_category",
            }
            if set(value) != fields or any(not isinstance(value[name], str) for name in fields):
                raise TypeError("optional failure fields are not exact strings")
            return OptionalFailure(
                attempt_id=cast(str, value["attempt_id"]),
                origin_run_id=cast(str, value["origin_run_id"]),
                origin=Origin(cast(str, value["origin"])),
                artifact_id=cast(str, value["artifact_id"]),
                safe_error_category=cast(str, value["safe_error_category"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("forecast optional failure record is invalid") from error

    @staticmethod
    def _artifact_binding_from_json(value: dict[str, object]) -> ArtifactBinding:
        try:
            fields = {
                "artifact_id",
                "origin",
                "model_role",
                "frozen",
                "verified",
                "calibrator_artifact_id",
                "model_lane",
                "feature_policy_version",
                "feature_schema_version",
                "policy_versions",
                "market_policy_version",
                "candidate_policy_version",
            }
            if set(value) != fields:
                raise TypeError("artifact binding fields are not exact")
            string_fields = fields - {
                "frozen",
                "verified",
                "calibrator_artifact_id",
                "policy_versions",
            }
            if any(not isinstance(value[name], str) for name in string_fields):
                raise TypeError("artifact binding primitive fields are invalid")
            if type(value["frozen"]) is not bool or type(value["verified"]) is not bool:
                raise TypeError("artifact binding trust flags must be booleans")
            calibrator = value["calibrator_artifact_id"]
            if calibrator is not None and not isinstance(calibrator, str):
                raise TypeError("artifact binding calibrator must be a string or null")
            policies_value = value["policy_versions"]
            if not isinstance(policies_value, list):
                raise TypeError("artifact policy lineage must be an array")
            policies: list[tuple[str, str]] = []
            for row in policies_value:
                if (
                    not isinstance(row, list)
                    or len(row) != 2
                    or not all(isinstance(item, str) for item in row)
                ):
                    raise TypeError("artifact policy lineage rows must be string pairs")
                policies.append((row[0], row[1]))
            return ArtifactBinding(
                artifact_id=cast(str, value["artifact_id"]),
                origin=Origin(cast(str, value["origin"])),
                model_role=value["model_role"],  # type: ignore[arg-type]
                frozen=value["frozen"],
                verified=value["verified"],
                calibrator_artifact_id=calibrator,
                model_lane=value["model_lane"],  # type: ignore[arg-type]
                feature_policy_version=cast(str, value["feature_policy_version"]),
                feature_schema_version=cast(str, value["feature_schema_version"]),
                policy_versions=tuple(policies),
                market_policy_version=cast(str, value["market_policy_version"]),
                candidate_policy_version=cast(str, value["candidate_policy_version"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("forecast artifact binding record is invalid") from error

    @staticmethod
    def _artifact_selection_from_json(value: dict[str, object]) -> ArtifactSelection:
        try:
            if set(value) != {"attempt_id", "origin_run_id", "binding"}:
                raise TypeError("artifact selection fields are not exact")
            if not isinstance(value["attempt_id"], str) or not isinstance(
                value["origin_run_id"], str
            ):
                raise TypeError("artifact selection identity fields are invalid")
            binding = value["binding"]
            if not isinstance(binding, dict):
                raise TypeError("artifact selection binding must be an object")
            return ArtifactSelection(
                value["attempt_id"],
                value["origin_run_id"],
                DurableForecastRepository._artifact_binding_from_json(binding),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("forecast artifact selection record is invalid") from error

    @staticmethod
    def _market_validation_from_json(
        value: dict[str, object],
    ) -> MarketValidationBinding:
        fields = {
            "market_comparator_id",
            "champion_prediction_id",
            "market_policy_version",
            "candidate_policy_version",
            "comparator_math_version",
            "candidate_math_version",
        }
        if set(value) != fields or any(not isinstance(value[name], str) for name in fields):
            raise DataIntegrityError("market validation binding fields are invalid")
        return MarketValidationBinding(**value)  # type: ignore[arg-type]

    def load_committed_graph(self, key: str) -> CommittedForecastGraph:
        run = self.terminal_run(key)
        obligation = self.load_obligation(key)
        if run is None or obligation is None:
            raise DataIntegrityError("forecast graph is not terminal and committed")
        return self._assemble_graph(key, run, obligation)

    def _assemble_graph(
        self,
        key: str,
        run: OriginRun,
        obligation: ForecastObligationRecord,
    ) -> CommittedForecastGraph:
        if obligation.execution_key != key:
            raise DataIntegrityError("forecast obligation logical ID is substituted")
        if (
            run.canonical_event_id != obligation.event.canonical_event_id
            or run.event_version != obligation.event.event_version
            or run.origin is not obligation.origin
            or run.forecast_policy_version != obligation.forecast_policy_version
        ):
            raise DataIntegrityError("terminal run does not match registered obligation")
        all_attempts = tuple(
            self._attempt_record(logical_id, value)
            for logical_id, value in self._records(self._attempt_namespace)
        )
        attempt_history = tuple(
            item for item in all_attempts if item.origin_run_id == run.origin_run_id
        )
        attempts, _ = self._collapse_attempt_chain(attempt_history)
        if not attempts or not set(run.attempt_ids).issubset(
            {item.attempt_id for item in attempts}
        ):
            raise DataIntegrityError("terminal run attempt lineage is incomplete")
        predictions = tuple(
            self._typed_record(
                "prediction",
                item,
                self._read_required("prediction", item),
                Prediction,
                "prediction_id",
            )
            for item in run.prediction_ids
        )
        if len({item.prediction_id for item in predictions}) != len(predictions) or any(
            item.origin_run_id != run.origin_run_id for item in predictions
        ):
            raise DataIntegrityError("terminal prediction lineage is invalid")
        snapshot_ids = {item.feature_snapshot_id for item in predictions}
        if len(snapshot_ids) > 1:
            raise DataIntegrityError("terminal predictions reference multiple feature snapshots")
        snapshot = (
            None
            if not snapshot_ids
            else self._typed_record(
                "snapshot",
                next(iter(snapshot_ids)),
                self._read_required("snapshot", next(iter(snapshot_ids))),
                FeatureSnapshot,
                "snapshot_id",
            )
        )
        manifest_rows = {
            logical_id: self._typed_record(
                "manifest", logical_id, value, CaptureManifest, "capture_id"
            )
            for logical_id, value in self._records(self._record_namespace("manifest"))
        }
        attempt_ids = set(run.attempt_ids)
        manifests = tuple(
            sorted(
                (item for item in manifest_rows.values() if item.run_id in attempt_ids),
                key=lambda item: item.capture_id,
            )
        )
        if snapshot is not None and not set(snapshot.input_manifest_ids).issubset(manifest_rows):
            raise DataIntegrityError("feature snapshot manifest lineage is incomplete")
        fact_rows = {
            logical_id: self._typed_record("fact", logical_id, value, NormalizedFact, "fact_id")
            for logical_id, value in self._records(self._record_namespace("fact"))
        }
        facts = tuple(
            fact_rows[item]
            for item in (() if snapshot is None else snapshot.input_fact_ids)
            if item in fact_rows
        )
        if snapshot is not None and len(facts) != len(snapshot.input_fact_ids):
            raise DataIntegrityError("feature snapshot fact lineage is incomplete")
        comparator = (
            None
            if run.market_comparator_id is None
            else self._typed_record(
                "comparator",
                run.market_comparator_id,
                self._read_required("comparator", run.market_comparator_id),
                MarketComparator,
                "market_comparator_id",
            )
        )
        market_validation = (
            None
            if comparator is None
            else self._market_validation_from_json(
                self._read_required("market-validation", comparator.market_comparator_id)
            )
        )
        quotes = tuple(
            self._typed_record(
                "quote",
                quote_id,
                self._read_required("quote", quote_id),
                MoneylineQuote,
                "quote_id",
            )
            for quote_id in (() if comparator is None else comparator.quote_ids)
        )
        prediction_ids = set(run.prediction_ids)
        all_decisions = tuple(
            self._typed_record("decision", logical_id, value, BettingDecision, "decision_id")
            for logical_id, value in self._records(self._record_namespace("decision"))
        )
        decisions = tuple(item for item in all_decisions if item.prediction_id in prediction_ids)
        all_candidates = tuple(
            self._typed_record(
                "candidate",
                logical_id,
                value,
                DisplayedQuoteCandidate,
                "candidate_id",
            )
            for logical_id, value in self._records(self._record_namespace("candidate"))
        )
        candidates = tuple(item for item in all_candidates if item.prediction_id in prediction_ids)
        optional_failure_values: list[OptionalFailure] = []
        for logical_id, value in self._records(self._record_namespace("optional-failure")):
            failure = self._optional_failure_from_json(value)
            if logical_id != f"{failure.attempt_id}|{failure.artifact_id}":
                raise DataIntegrityError("optional failure logical ID is substituted")
            if failure.origin_run_id == run.origin_run_id and failure.attempt_id in attempt_ids:
                optional_failure_values.append(failure)
        optional_failures = tuple(optional_failure_values)
        artifact_selection_values: list[ArtifactSelection] = []
        for logical_id, value in self._records(self._record_namespace("artifact-selection")):
            selection = self._artifact_selection_from_json(value)
            expected = (
                f"{selection.attempt_id}|{selection.binding.model_role}|"
                f"{selection.binding.artifact_id}"
            )
            if logical_id != expected:
                raise DataIntegrityError("artifact selection logical ID is substituted")
            if selection.origin_run_id == run.origin_run_id and selection.attempt_id in attempt_ids:
                artifact_selection_values.append(selection)
        artifact_selections = tuple(
            sorted(
                artifact_selection_values,
                key=lambda item: (item.binding.model_role, item.binding.artifact_id),
            )
        )
        graph = CommittedForecastGraph(
            obligation,
            run,
            attempts,
            manifests,
            facts,
            quotes,
            optional_failures,
            artifact_selections,
            snapshot,
            predictions,
            comparator,
            decisions,
            candidates,
            market_validation,
        )
        return validate_committed_graph(
            graph,
            artifact_resolver=self.artifact_resolver,
        )

    @classmethod
    def encode_committed_graph(cls, graph: CommittedForecastGraph) -> dict[str, object]:
        value = cls._json_value(graph)
        if not isinstance(value, dict):
            raise TypeError("committed forecast graph must encode as an object")
        return value

    @classmethod
    def decode_committed_graph(
        cls,
        value: dict[str, object],
        *,
        artifact_resolver: ArtifactRegistry | None = None,
    ) -> CommittedForecastGraph:
        try:
            fields = {
                "obligation",
                "run",
                "attempts",
                "manifests",
                "facts",
                "quotes",
                "optional_failures",
                "artifact_selections",
                "snapshot",
                "predictions",
                "comparator",
                "decisions",
                "candidates",
                "market_validation",
            }
            if set(value) != fields:
                raise TypeError("forecast graph fields are not exact")

            def sequence(name: str) -> list[object]:
                raw = value[name]
                if not isinstance(raw, list):
                    raise TypeError(f"forecast graph {name} must be an array")
                return raw

            def objects(name: str) -> list[dict[str, object]]:
                result: list[dict[str, object]] = []
                for item in sequence(name):
                    if not isinstance(item, dict):
                        raise TypeError(f"forecast graph {name} rows must be objects")
                    result.append(item)
                return result

            obligation_value = value["obligation"]
            snapshot_value = value["snapshot"]
            comparator_value = value["comparator"]
            market_validation_value = value["market_validation"]
            if not isinstance(obligation_value, dict):
                raise TypeError("forecast graph obligation must be an object")
            if market_validation_value is not None and not isinstance(
                market_validation_value, dict
            ):
                raise TypeError("forecast graph market validation must be an object or null")
            graph = CommittedForecastGraph(
                cls.decode_obligation(obligation_value),
                OriginRun.model_validate(value["run"]),
                tuple(RunAttempt.model_validate(item) for item in sequence("attempts")),
                tuple(CaptureManifest.model_validate(item) for item in sequence("manifests")),
                tuple(NormalizedFact.model_validate(item) for item in sequence("facts")),
                tuple(MoneylineQuote.model_validate(item) for item in sequence("quotes")),
                tuple(
                    cls._optional_failure_from_json(item) for item in objects("optional_failures")
                ),
                tuple(
                    cls._artifact_selection_from_json(item)
                    for item in objects("artifact_selections")
                ),
                None if snapshot_value is None else FeatureSnapshot.model_validate(snapshot_value),
                tuple(Prediction.model_validate(item) for item in sequence("predictions")),
                None
                if comparator_value is None
                else MarketComparator.model_validate(comparator_value),
                tuple(BettingDecision.model_validate(item) for item in sequence("decisions")),
                tuple(
                    DisplayedQuoteCandidate.model_validate(item) for item in sequence("candidates")
                ),
                (
                    None
                    if market_validation_value is None
                    else cls._market_validation_from_json(market_validation_value)
                ),
            )
            return validate_committed_graph(
                graph,
                artifact_resolver=artifact_resolver,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("committed forecast graph bundle is invalid") from error

    def commit_terminal(
        self,
        key: str,
        run: OriginRun,
        attempt: RunAttempt,
        *,
        deadline: datetime,
        context: ForecastExecutionContext,
        clock: Callable[[], datetime],
    ) -> OriginRun:
        def missed(at: datetime) -> tuple[OriginRun, RunAttempt]:
            return (
                OriginRun.model_validate(
                    run.model_copy(
                        update={
                            "decision_at_utc": None,
                            "status": RunStatus.MISSED,
                            "prediction_ids": [],
                            "market_comparator_id": None,
                            "reason_codes": ["EXECUTION_CROSSED_WINDOW"],
                            "created_at_utc": at,
                        }
                    ).model_dump()
                ),
                RunAttempt.model_validate(
                    attempt.model_copy(
                        update={
                            "ended_at_utc": at,
                            "status": "FAILED",
                            "safe_error_category": "EXECUTION_CROSSED_WINDOW",
                        }
                    ).model_dump()
                ),
            )

        committed_at = clock()
        if context.mode == "live" and committed_at > deadline:
            run, attempt = missed(committed_at)
        self.append_attempt(attempt)
        after_attempt = clock()
        if (
            context.mode == "live"
            and after_attempt > deadline
            and (run.status is not RunStatus.MISSED or attempt.ended_at_utc != after_attempt)
        ):
            run, attempt = missed(after_attempt)
            self.append_attempt(attempt)
        while True:
            obligation = self.load_obligation(key)
            if obligation is None:
                raise DataIntegrityError("terminal forecast obligation is missing")
            self._assemble_graph(key, run, obligation)
            digest = self._prepare_terminal(key, run)
            visibility_at = clock()
            if (
                context.mode == "live"
                and visibility_at > deadline
                and run.status is not RunStatus.MISSED
            ):
                run, attempt = missed(visibility_at)
                self.append_attempt(attempt)
                continue
            crossed_at = self._publish_terminal(
                key,
                run,
                digest,
                deadline=deadline,
                context=context,
                clock=clock,
            )
            if crossed_at is not None:
                run, attempt = missed(crossed_at)
                self.append_attempt(attempt)
                continue
            return run

    def _prepare_terminal(self, key: str, run: OriginRun) -> str:
        payload = canonical_json_bytes(run)
        digest = sha256(payload).hexdigest()
        self.ledger.append(self._terminal_prepared_namespace, f"{key}|{digest}", run)
        return digest

    def _publish_terminal_receipt(
        self,
        key: str,
        receipt: TerminalPublicationReceipt,
    ) -> datetime:
        receipt_key = self._terminal_receipt_key(key, receipt.run_content_sha256)
        existing = self.ledger.read(self._terminal_receipt_namespace, receipt_key)
        if existing is not None:
            if self._terminal_receipt_from_json(existing) != receipt:
                raise IdempotencyConflict(receipt_key)
            return self._terminal_receipt_publication_time(receipt_key)

        payload = canonical_json_bytes(self._json_value(receipt))
        content_digest = sha256(payload).hexdigest()
        object_path = self.ledger.object_path(content_digest)
        LedgerStore._mkdir_durable(object_path.parent)
        object_created = LedgerStore._atomic_create(object_path, payload)
        if not object_created and object_path.read_bytes() != payload:
            raise DataIntegrityError("terminal receipt object does not match its content hash")

        marker = self.ledger.marker_path(self._terminal_receipt_namespace, receipt_key)
        marker_payload = canonical_json_bytes(
            {
                "namespace": self._terminal_receipt_namespace,
                "idempotency_key": receipt_key,
                "content_sha256": content_digest,
                "object_path": object_path.relative_to(self.root).as_posix(),
            }
        )
        marker_object_digest = sha256(marker_payload).hexdigest()
        marker_object_path = self.ledger.object_path(marker_object_digest)
        LedgerStore._mkdir_durable(marker_object_path.parent)
        marker_object_created = LedgerStore._atomic_create(marker_object_path, marker_payload)
        if not marker_object_created and marker_object_path.read_bytes() != marker_payload:
            raise DataIntegrityError(
                "terminal receipt marker object does not match its content hash"
            )
        LedgerStore._mkdir_durable(marker.parent)
        try:
            os.link(marker_object_path, marker)
        except FileExistsError:
            existing = self.ledger.read(self._terminal_receipt_namespace, receipt_key)
            if existing is None or self._terminal_receipt_from_json(existing) != receipt:
                raise IdempotencyConflict(receipt_key) from None
            return self._terminal_receipt_publication_time(receipt_key)
        published_at = self._terminal_receipt_publication_time(receipt_key)
        LedgerStore._fsync_directory(marker.parent)
        return published_at

    def _publish_terminal(
        self,
        key: str,
        run: OriginRun,
        digest: str,
        *,
        deadline: datetime,
        context: ForecastExecutionContext,
        clock: Callable[[], datetime],
    ) -> datetime | None:
        committed = self.terminal_run(key)
        if committed is not None:
            if committed != run:
                raise IdempotencyConflict(key)
            return None
        object_path = self.ledger.object_path(digest)
        if not object_path.exists():
            raise DataIntegrityError("prepared terminal object is missing")
        marker_path = self.ledger.marker_path(self._terminal_namespace, key)
        marker_payload = canonical_json_bytes(
            {
                "namespace": self._terminal_namespace,
                "idempotency_key": key,
                "content_sha256": digest,
                "object_path": object_path.relative_to(self.root).as_posix(),
            }
        )
        LedgerStore._mkdir_durable(marker_path.parent)
        with tempfile.NamedTemporaryFile(dir=marker_path.parent, delete=False) as handle:
            handle.write(marker_payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            publication_lock = self._publication_lock_path(key)
            with publication_lock.open("a+b") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    committed = self._read_receipted_terminal(key)
                    if committed is not None:
                        if committed != run:
                            raise IdempotencyConflict(key)
                        return None
                    candidate = self.ledger.read(self._terminal_namespace, key)
                    if candidate is not None:
                        try:
                            candidate_run = OriginRun.model_validate(candidate)
                        except (TypeError, ValueError) as error:
                            raise DataIntegrityError("terminal candidate is invalid") from error
                        if candidate_run != run:
                            marker_path.unlink()
                            LedgerStore._fsync_directory(marker_path.parent)
                            candidate = None
                    decisive_at = clock()
                    if (
                        context.mode == "live"
                        and decisive_at > deadline
                        and run.status is not RunStatus.MISSED
                    ):
                        return decisive_at
                    if candidate is None:
                        os.link(temporary, marker_path)
                    LedgerStore._fsync_directory(marker_path.parent)
                    durable_at = clock()
                    if (
                        context.mode == "live"
                        and durable_at > deadline
                        and run.status is not RunStatus.MISSED
                    ):
                        marker_path.unlink()
                        LedgerStore._fsync_directory(marker_path.parent)
                        return durable_at
                    receipt = TerminalPublicationReceipt(
                        execution_key=key,
                        run_content_sha256=digest,
                        candidate_durable_at_utc=durable_at,
                    )
                    receipt_published_at = self._publish_terminal_receipt(
                        key,
                        receipt,
                    )
                    if (
                        context.mode == "live"
                        and receipt_published_at >= self._terminal_receipt_close_threshold(deadline)
                        and run.status is not RunStatus.MISSED
                    ):
                        return receipt_published_at
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            temporary.unlink(missing_ok=True)
        return None
