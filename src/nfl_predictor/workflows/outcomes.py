from __future__ import annotations

import fcntl
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Literal, Protocol, cast

from nfl_predictor.betting.settlement import (
    OperatorSettlementEvidence,
    settle_displayed_candidate,
    settle_profit,
)
from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import OriginRun, Prediction
from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.contracts.markets import (
    ActualTicket,
    DisplayedQuoteCandidate,
    MoneylineQuote,
)
from nfl_predictor.contracts.outcomes import Outcome, Settlement
from nfl_predictor.evaluation.scorecards import (
    ForecastObligationInput,
    MarketComparatorInput,
    PredictionScoreInput,
    ReturnObligationInput,
    ReturnScoreInput,
)
from nfl_predictor.sources.outcomes import OutcomeAdapter, OutcomeCapture, OutcomeObservation
from nfl_predictor.storage.ledger import DataIntegrityError, LedgerStore
from nfl_predictor.workflows.forecast import (
    ArtifactRegistry,
    CommittedForecastGraph,
    DurableForecastRepository,
    ForecastObligationRecord,
    validate_committed_graph,
    validate_obligation_artifact_trust,
    validate_obligation_record,
)


@dataclass(frozen=True)
class SettlementEnvelope:
    outcome_id: str
    outcome_version: int
    operator_evidence_id: str
    operator_evidence_frozen_at_utc: datetime
    capture_id: str
    raw_payload_sha256: str
    code_sha: str
    dependency_lock_sha256: str
    source: str
    settlement: Settlement


@dataclass(frozen=True)
class OutcomeRun:
    outcome: Outcome
    capture_manifest: CaptureManifest
    settlements: tuple[SettlementEnvelope, ...]
    report_only_ticket_ids: tuple[str, ...]
    observation_time_basis: Literal["source_snapshot", "retrieval_fallback"]


@dataclass(frozen=True)
class ObservationWatermark:
    canonical_event_id: str
    source: str
    source_observed_at_utc: datetime
    response_received_at_utc: datetime
    raw_payload_sha256: str


@dataclass(frozen=True)
class ForecastObligationBundle:
    obligation: ForecastObligationRecord
    run: OriginRun | None


class OutcomeRepository(Protocol):
    def transact(self, key: str, operation: Callable[[], OutcomeRun]) -> OutcomeRun: ...
    def latest_outcome(self, event_id: str, source: str) -> Outcome | None: ...
    def append_outcome(self, outcome: Outcome) -> None: ...
    def append_outcome_capture(self, manifest: CaptureManifest) -> None: ...
    def displayed_candidates(self, event_id: str) -> tuple[DisplayedQuoteCandidate, ...]: ...
    def quote_for(self, quote_id: str) -> MoneylineQuote: ...
    def tickets_for_event(self, event_id: str) -> tuple[ActualTicket, ...]: ...
    def operator_evidence(
        self, quote: MoneylineQuote, outcome: Outcome
    ) -> OperatorSettlementEvidence: ...
    def latest_settlement(
        self, candidate_id: str | None, ticket_id: str | None
    ) -> Settlement | None: ...

    def latest_settlement_envelope(
        self, candidate_id: str | None, ticket_id: str | None
    ) -> SettlementEnvelope | None: ...
    def append_settlement(self, envelope: SettlementEnvelope) -> None: ...
    def mark_ticket_report_only(self, ticket_id: str) -> None: ...
    def append_quarantine(self, reason: str) -> None: ...

    def latest_observation_watermark(
        self, event_id: str, source: str
    ) -> ObservationWatermark | None: ...

    def append_observation_watermark(self, watermark: ObservationWatermark) -> None: ...


class OutcomeWorkflow:
    def __init__(self, adapter: OutcomeAdapter, repository: OutcomeRepository) -> None:
        self.adapter = adapter
        self.repository = repository

    def run(self, event: EventVersion, *, request: dict[str, object]) -> OutcomeRun:
        source_event_id = event.source_event_ids.get("nflverse")
        if source_event_id is None:
            raise ValueError("canonical event has no nflverse source event alias")
        key = f"{event.canonical_event_id}:nflverse"
        run_id = sha256(f"outcome|{key}".encode()).hexdigest()
        return self.repository.transact(
            key,
            lambda: self._capture_and_reconcile(event, source_event_id, request, run_id),
        )

    def _capture_and_reconcile(
        self,
        event: EventVersion,
        source_event_id: str,
        request: dict[str, object],
        run_id: str,
    ) -> OutcomeRun:
        capture = self.adapter.capture(request, run_id)
        self._validate_capture_lineage(capture)
        self._validate_observation(event, source_event_id, capture.observation)
        self.repository.append_outcome_capture(capture.manifest)
        return self._commit_observation(event, capture)

    @staticmethod
    def _validate_capture_lineage(capture: OutcomeCapture) -> None:
        manifest = capture.manifest
        observation = capture.observation
        expected_observed = manifest.source_snapshot_at_utc or manifest.response_received_at_utc
        expected_basis = (
            "source_snapshot"
            if manifest.source_snapshot_at_utc is not None
            else "retrieval_fallback"
        )
        if (
            observation.capture_id != manifest.capture_id
            or observation.raw_payload_sha256 != manifest.raw_payload_sha256
            or observation.source != manifest.source
            or observation.retrieved_at_utc != manifest.response_received_at_utc
            or observation.source_observed_at_utc != expected_observed
            or observation.observation_time_basis != expected_basis
            or observation.code_sha != manifest.code_sha
            or observation.dependency_lock_sha256 != manifest.dependency_lock_sha256
        ):
            raise ValueError("outcome observation does not exactly match capture manifest")

    def _validate_observation(
        self,
        event: EventVersion,
        source_event_id: str,
        observation: OutcomeObservation,
    ) -> None:
        errors: list[str] = []
        if observation.source != "nflverse":
            errors.append("source is not nflverse")
        if observation.source_event_id != source_event_id:
            errors.append("source event does not match canonical nflverse alias")
        if observation.home_team is not None and observation.home_team != event.home_team:
            errors.append("home team does not match canonical event")
        if observation.away_team is not None and observation.away_team != event.away_team:
            errors.append("away team does not match canonical event")
        if errors:
            reason = "; ".join(errors)
            self.repository.append_quarantine(reason)
            raise ValueError(reason)

    def _commit_observation(self, event: EventVersion, capture: OutcomeCapture) -> OutcomeRun:
        observation = capture.observation
        watermark = self.repository.latest_observation_watermark(
            event.canonical_event_id, observation.source
        )
        if watermark is not None:
            if observation.source_observed_at_utc < watermark.source_observed_at_utc:
                reason = "older outcome observation cannot supersede the stream watermark"
                self.repository.append_quarantine(reason)
                raise ValueError(reason)
            if (
                observation.source_observed_at_utc == watermark.source_observed_at_utc
                and observation.raw_payload_sha256 != watermark.raw_payload_sha256
            ):
                reason = "equal-time conflicting outcome observation is ambiguous"
                self.repository.append_quarantine(reason)
                raise ValueError(reason)
        next_watermark = ObservationWatermark(
            event.canonical_event_id,
            observation.source,
            observation.source_observed_at_utc,
            observation.retrieved_at_utc,
            observation.raw_payload_sha256,
        )
        if watermark != next_watermark:
            self.repository.append_observation_watermark(next_watermark)
        latest = self.repository.latest_outcome(event.canonical_event_id, observation.source)
        if latest is not None:
            if observation.source_observed_at_utc < latest.source_version_or_retrieved_at_utc:
                reason = "older outcome observation cannot supersede the latest source observation"
                self.repository.append_quarantine(reason)
                raise ValueError(reason)
            if (
                observation.source_observed_at_utc == latest.source_version_or_retrieved_at_utc
                and observation.raw_payload_sha256 != latest.raw_payload_sha256
            ):
                reason = "ambiguous outcome observations share a source timestamp"
                self.repository.append_quarantine(reason)
                raise ValueError(reason)
        if latest is not None and latest.raw_payload_sha256 == observation.raw_payload_sha256:
            outcome = latest
        else:
            outcome = self._outcome(event, observation, latest)
            self.repository.append_outcome(outcome)
        settlements, report_only = self._reconcile_settlements(event, outcome, capture.manifest)
        return OutcomeRun(
            outcome=outcome,
            capture_manifest=capture.manifest,
            settlements=tuple(settlements),
            report_only_ticket_ids=tuple(report_only),
            observation_time_basis=observation.observation_time_basis,
        )

    def _reconcile_settlements(
        self,
        event: EventVersion,
        outcome: Outcome,
        manifest: CaptureManifest,
    ) -> tuple[list[SettlementEnvelope], list[str]]:
        candidates = self.repository.displayed_candidates(event.canonical_event_id)
        if len({item.candidate_id for item in candidates}) != len(candidates):
            raise ValueError("displayed candidate IDs must be unique per event")
        candidate_by_id = {item.candidate_id: item for item in candidates}
        envelopes: list[SettlementEnvelope] = []
        for candidate in sorted(candidates, key=lambda item: item.candidate_id):
            quote = self.repository.quote_for(candidate.quote_id)
            self._validate_quote(event, candidate, quote)
            evidence = self.repository.operator_evidence(quote, outcome)
            settlement = settle_displayed_candidate(candidate, quote, outcome, evidence)
            envelope = self._versioned_envelope(settlement, outcome, evidence, manifest)
            if envelope is not None:
                self.repository.append_settlement(envelope)
                envelopes.append(envelope)

        report_only: list[str] = []
        for ticket in sorted(
            self.repository.tickets_for_event(event.canonical_event_id),
            key=lambda item: item.ticket_id,
        ):
            linked_candidate = (
                None if ticket.candidate_id is None else candidate_by_id.get(ticket.candidate_id)
            )
            if not ticket.model_attributed or linked_candidate is None:
                self.repository.mark_ticket_report_only(ticket.ticket_id)
                report_only.append(ticket.ticket_id)
                continue
            quote = self.repository.quote_for(linked_candidate.quote_id)
            self._validate_quote(event, linked_candidate, quote)
            evidence = self.repository.operator_evidence(quote, outcome)
            settlement = self._settle_ticket(ticket, linked_candidate, quote, outcome, evidence)
            envelope = self._versioned_envelope(settlement, outcome, evidence, manifest)
            if envelope is not None:
                self.repository.append_settlement(envelope)
                envelopes.append(envelope)
        return envelopes, report_only

    @staticmethod
    def _validate_quote(
        event: EventVersion,
        candidate: DisplayedQuoteCandidate,
        quote: MoneylineQuote,
    ) -> None:
        if quote.quote_id != candidate.quote_id:
            raise ValueError("candidate quote lineage mismatch")
        if quote.canonical_event_id != event.canonical_event_id:
            raise ValueError("candidate quote event lineage mismatch")
        if quote.source_event_id not in event.source_event_ids.values():
            raise ValueError("candidate quote source event lineage mismatch")
        if not any(selection.side == candidate.side for selection in quote.selections):
            raise ValueError("candidate side is absent from exact quote")

    @staticmethod
    def _outcome(
        event: EventVersion,
        observation: OutcomeObservation,
        latest: Outcome | None,
    ) -> Outcome:
        outcome_id = sha256(f"{event.canonical_event_id}|{observation.source}".encode()).hexdigest()
        version = 1 if latest is None else latest.outcome_version + 1
        result: Literal["home", "away", "tie", "unresolved"]
        if observation.game_status == "final":
            if observation.home_score is None or observation.away_score is None:
                raise ValueError("final observation is missing scores")
            result = (
                "home"
                if observation.home_score > observation.away_score
                else "away"
                if observation.away_score > observation.home_score
                else "tie"
            )
        else:
            result = "unresolved"
        return Outcome(
            outcome_id=outcome_id,
            outcome_version=version,
            canonical_event_id=event.canonical_event_id,
            event_version_at_final=event.event_version,
            game_status=observation.game_status,
            home_score=observation.home_score,
            away_score=observation.away_score,
            result=result,
            finalized_at_utc=observation.finalized_at_utc,
            source=observation.source,
            source_version_or_retrieved_at_utc=observation.source_observed_at_utc,
            raw_payload_sha256=observation.raw_payload_sha256,
        )

    def _versioned_envelope(
        self,
        settlement: Settlement,
        outcome: Outcome,
        evidence: OperatorSettlementEvidence,
        manifest: CaptureManifest,
    ) -> SettlementEnvelope | None:
        prior_envelope = self.repository.latest_settlement_envelope(
            settlement.candidate_id, settlement.ticket_id
        )
        prior = None if prior_envelope is None else prior_envelope.settlement
        if prior_envelope is not None:
            if (
                outcome.outcome_id != prior_envelope.outcome_id
                or outcome.outcome_version < prior_envelope.outcome_version
            ):
                raise ValueError("obsolete outcome cannot supersede newer settlement lineage")
            if evidence.frozen_at_utc < prior_envelope.operator_evidence_frozen_at_utc:
                raise ValueError("stale operator evidence cannot supersede newer evidence")
            if (
                evidence.frozen_at_utc == prior_envelope.operator_evidence_frozen_at_utc
                and evidence.evidence_id != prior_envelope.operator_evidence_id
            ):
                raise ValueError("equal-time conflicting operator evidence cannot supersede")
        if (
            prior_envelope is not None
            and prior_envelope.operator_evidence_id == evidence.evidence_id
            and prior_envelope.outcome_id == outcome.outcome_id
            and prior_envelope.outcome_version == outcome.outcome_version
        ):
            return None
        if prior is not None:
            version = prior.settlement_version + 1
            identity = (
                f"{settlement.candidate_id}|{settlement.ticket_id}|{outcome.outcome_id}|"
                f"{outcome.outcome_version}|{evidence.evidence_id}|{version}|"
                f"{prior.settlement_id}"
            )
            settlement = Settlement.model_validate(
                settlement.model_copy(
                    update={
                        "settlement_id": sha256(identity.encode()).hexdigest(),
                        "settlement_version": version,
                        "supersedes_settlement_id": prior.settlement_id,
                    }
                ).model_dump()
            )
        return SettlementEnvelope(
            outcome_id=outcome.outcome_id,
            outcome_version=outcome.outcome_version,
            operator_evidence_id=evidence.evidence_id,
            operator_evidence_frozen_at_utc=evidence.frozen_at_utc,
            capture_id=manifest.capture_id,
            raw_payload_sha256=manifest.raw_payload_sha256,
            code_sha=manifest.code_sha,
            dependency_lock_sha256=manifest.dependency_lock_sha256,
            source=manifest.source,
            settlement=settlement,
        )

    @staticmethod
    def _settle_ticket(
        ticket: ActualTicket,
        candidate: DisplayedQuoteCandidate,
        quote: MoneylineQuote,
        outcome: Outcome,
        evidence: OperatorSettlementEvidence,
    ) -> Settlement:
        if ticket.candidate_id != candidate.candidate_id or ticket.operator != quote.book_key:
            raise ValueError("ticket attribution does not match immutable candidate/operator")
        displayed = settle_displayed_candidate(candidate, quote, outcome, evidence)
        if displayed.result == "unresolved":
            profit_amount: Decimal | None = None
        elif displayed.result == "void":
            profit_amount = Decimal(0)
        else:
            _, units = settle_profit(candidate.side, outcome.result, ticket.accepted_decimal_price)
            profit_amount = None if units is None else ticket.stake * units
        settlement_id = sha256(
            (
                f"{ticket.ticket_id}|{outcome.outcome_id}|{outcome.outcome_version}|"
                f"{evidence.evidence_id}"
            ).encode()
        ).hexdigest()
        return Settlement(
            settlement_id=settlement_id,
            settlement_version=1,
            canonical_event_id=outcome.canonical_event_id,
            candidate_id=None,
            ticket_id=ticket.ticket_id,
            operator=ticket.operator,
            market_semantics_version=quote.market_semantics_version,
            settlement_policy_url=quote.settlement_policy_url,
            settlement_policy_version=quote.settlement_policy_version,
            result=displayed.result,
            profit_units=None,
            profit_amount=profit_amount,
            currency=ticket.currency,
            settled_at_utc=displayed.settled_at_utc,
            manual_review_required=displayed.manual_review_required,
        )


class DurableOutcomeReportRepository:
    """Immutable local outcome/settlement store that also exposes report cohorts."""

    _TICKETS = "workflow-tickets"
    _OUTCOMES = "workflow-outcomes"
    _OUTCOME_CAPTURES = "workflow-outcome-captures"
    _SETTLEMENTS = "workflow-settlements"
    _WATERMARKS = "workflow-watermarks"
    _OPERATOR_EVIDENCE = "workflow-operator-evidence"
    _REPORT_ONLY = "workflow-report-only"
    _QUARANTINE = "workflow-quarantine"
    _FORECAST_GRAPHS = "workflow-forecast-graphs"
    _FORECAST_OBLIGATIONS = "workflow-forecast-obligations"

    def __init__(
        self,
        root: Path,
        artifact_resolver: ArtifactRegistry,
    ) -> None:
        if artifact_resolver is None:
            raise TypeError("outcome repository requires an artifact resolver")
        self.root = root
        self.ledger = LedgerStore(root)
        self.artifact_resolver = artifact_resolver

    def transact(self, key: str, operation: Callable[[], OutcomeRun]) -> OutcomeRun:
        lock_path = self.root / "locks" / f"{sha256(key.encode()).hexdigest()}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return operation()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def import_forecast_graph(
        self,
        source: DurableForecastRepository,
        execution_key: str,
    ) -> None:
        if type(source) is not DurableForecastRepository:
            raise TypeError("forecast import requires a concrete durable source repository")
        graph = source.load_committed_graph(execution_key)
        validate_committed_graph(
            graph,
            artifact_resolver=self.artifact_resolver,
        )
        self.ledger.append(
            self._FORECAST_GRAPHS,
            graph.run.origin_run_id,
            DurableForecastRepository.encode_committed_graph(graph),
        )

    def import_forecast_obligation(
        self,
        source: DurableForecastRepository,
        execution_key: str,
    ) -> None:
        if type(source) is not DurableForecastRepository:
            raise TypeError("forecast obligation import requires a concrete durable source")
        obligation = source.load_obligation(execution_key)
        if obligation is None:
            raise KeyError(execution_key)
        validate_obligation_record(obligation)
        validate_obligation_artifact_trust(obligation, self.artifact_resolver)
        self.ledger.append(
            self._FORECAST_OBLIGATIONS,
            obligation.execution_key,
            {
                "obligation": DurableForecastRepository.encode_obligation(obligation),
                "run": None,
            },
        )

    def forecast_obligation_bundles(self) -> tuple[ForecastObligationBundle, ...]:
        values: dict[str, ForecastObligationBundle] = {}
        for logical_id, value in self._record_rows(self._FORECAST_OBLIGATIONS):
            if set(value) != {"obligation", "run"}:
                raise DataIntegrityError("forecast obligation bundle fields are invalid")
            obligation_value = value["obligation"]
            if not isinstance(obligation_value, dict):
                raise DataIntegrityError("forecast obligation bundle is invalid")
            obligation = DurableForecastRepository.decode_obligation(obligation_value)
            validate_obligation_artifact_trust(obligation, self.artifact_resolver)
            if obligation.execution_key != logical_id:
                raise DataIntegrityError("forecast obligation logical ID is substituted")
            run_value = value["run"]
            try:
                run = None if run_value is None else OriginRun.model_validate(run_value)
            except (TypeError, ValueError) as error:
                raise DataIntegrityError("forecast obligation run is invalid") from error
            values[obligation.execution_key] = ForecastObligationBundle(obligation, run)
        for graph in self._forecast_graphs():
            key = graph.obligation.execution_key
            prior = values.get(key)
            if prior is not None:
                if prior.obligation != graph.obligation:
                    raise DataIntegrityError("forecast obligation lifecycle identity changed")
                if prior.run is not None and prior.run != graph.run:
                    raise DataIntegrityError("conflicting terminal forecast graph import")
            values[key] = ForecastObligationBundle(graph.obligation, graph.run)
        return tuple(values[key] for key in sorted(values))

    def load_forecast_graph(self, origin_run_id: str) -> CommittedForecastGraph:
        value = self.ledger.read(self._FORECAST_GRAPHS, origin_run_id)
        if value is None:
            raise KeyError(origin_run_id)
        graph = DurableForecastRepository.decode_committed_graph(
            value,
            artifact_resolver=self.artifact_resolver,
        )
        if graph.run.origin_run_id != origin_run_id:
            raise DataIntegrityError("forecast graph bundle identity mismatch")
        return graph

    def _forecast_graphs(self) -> tuple[CommittedForecastGraph, ...]:
        values: list[CommittedForecastGraph] = []
        for logical_id, value in self._record_rows(self._FORECAST_GRAPHS):
            graph = DurableForecastRepository.decode_committed_graph(
                value,
                artifact_resolver=self.artifact_resolver,
            )
            if graph.run.origin_run_id != logical_id:
                raise DataIntegrityError("forecast graph bundle logical ID is substituted")
            values.append(graph)
        return tuple(values)

    def register_ticket(
        self, ticket: ActualTicket, *, canonical_event_id: str | None = None
    ) -> None:
        if ticket.model_attributed and ticket.candidate_id is None:
            raise ValueError("model-attributed ticket requires a persisted candidate")
        if ticket.candidate_id is not None:
            try:
                candidate = self.read_candidate(ticket.candidate_id)
            except KeyError as error:
                raise ValueError(
                    "model-attributed ticket requires a persisted candidate"
                ) from error
            linked_quote = self.quote_for(candidate.quote_id)
            linked_event_id = linked_quote.canonical_event_id
            if canonical_event_id is not None and canonical_event_id != linked_event_id:
                raise ValueError("ticket event does not match its persisted candidate")
            linked_event = self._event(linked_event_id)
            if ticket.operator != linked_quote.book_key:
                raise ValueError("ticket operator does not match immutable candidate quote")
            if (
                ticket.accepted_at_utc < candidate.decision_at_utc
                or ticket.accepted_at_utc >= linked_event.kickoff_at_utc
            ):
                raise ValueError("ticket accepted time must follow decision and precede kickoff")
            canonical_event_id = linked_event_id
        if canonical_event_id is None:
            raise ValueError("report-only ticket requires a persisted canonical event")
        try:
            self._event(canonical_event_id)
        except KeyError as error:
            raise ValueError("report-only ticket requires a persisted canonical event") from error
        self.ledger.append(
            self._TICKETS,
            ticket.ticket_id,
            {
                "ticket": ticket.model_dump(mode="json"),
                "canonical_event_id": canonical_event_id,
            },
        )

    def latest_outcome(self, event_id: str, source: str) -> Outcome | None:
        values = [
            outcome
            for outcome in self._typed_outcomes()
            if outcome.canonical_event_id == event_id and outcome.source == source
        ]
        return max(values, key=lambda item: item.outcome_version) if values else None

    def read_outcome(self, outcome_id: str, outcome_version: int) -> Outcome:
        value = self.ledger.read(self._OUTCOMES, f"{outcome_id}|{outcome_version}")
        if value is None:
            raise KeyError((outcome_id, outcome_version))
        try:
            outcome = Outcome.model_validate(value)
        except (TypeError, ValueError) as error:
            raise DataIntegrityError("persisted outcome is invalid") from error
        if outcome.outcome_id != outcome_id or outcome.outcome_version != outcome_version:
            raise DataIntegrityError("persisted outcome logical ID is substituted")
        return outcome

    def append_outcome(self, outcome: Outcome) -> None:
        expected_id = sha256(f"{outcome.canonical_event_id}|{outcome.source}".encode()).hexdigest()
        try:
            canonical_event = self._event(outcome.canonical_event_id)
        except KeyError as error:
            raise DataIntegrityError("outcome has no imported canonical forecast event") from error
        if (
            outcome.outcome_id != expected_id
            or outcome.event_version_at_final != canonical_event.event_version
        ):
            raise DataIntegrityError("outcome ID is not the stable event/source identity")
        if not self._captures_for_outcome(outcome):
            raise DataIntegrityError("outcome append lacks an exact persisted capture manifest")
        watermark = self.latest_observation_watermark(outcome.canonical_event_id, outcome.source)
        if watermark is None or (
            watermark.source_observed_at_utc != outcome.source_version_or_retrieved_at_utc
            or watermark.raw_payload_sha256 != outcome.raw_payload_sha256
        ):
            raise DataIntegrityError("outcome append lacks its exact observation watermark")
        prior = self.latest_outcome(outcome.canonical_event_id, outcome.source)
        expected_version = 1 if prior is None else prior.outcome_version + 1
        if outcome.outcome_version != expected_version or (
            prior is not None
            and outcome.source_version_or_retrieved_at_utc
            < prior.source_version_or_retrieved_at_utc
        ):
            raise DataIntegrityError("outcome append does not extend the monotone stream")
        self.ledger.append(
            self._OUTCOMES,
            f"{outcome.outcome_id}|{outcome.outcome_version}",
            outcome,
        )

    def append_outcome_capture(self, manifest: CaptureManifest) -> None:
        self.ledger.append(self._OUTCOME_CAPTURES, manifest.capture_id, manifest)

    def _outcome_capture(self, capture_id: str) -> CaptureManifest:
        value = self.ledger.read(self._OUTCOME_CAPTURES, capture_id)
        if value is None:
            raise DataIntegrityError("settlement outcome capture reference is dangling")
        try:
            manifest = CaptureManifest.model_validate(value)
        except (TypeError, ValueError) as error:
            raise DataIntegrityError("persisted outcome capture is invalid") from error
        if manifest.capture_id != capture_id:
            raise DataIntegrityError("persisted outcome capture logical ID is substituted")
        return manifest

    def _typed_outcome_captures(self) -> tuple[CaptureManifest, ...]:
        values: list[CaptureManifest] = []
        for logical_id, value in self._record_rows(self._OUTCOME_CAPTURES):
            try:
                manifest = CaptureManifest.model_validate(value)
            except (TypeError, ValueError) as error:
                raise DataIntegrityError("persisted outcome capture is invalid") from error
            if manifest.capture_id != logical_id:
                raise DataIntegrityError("persisted outcome capture logical ID is substituted")
            values.append(manifest)
        return tuple(values)

    def _captures_for_outcome(self, outcome: Outcome) -> tuple[CaptureManifest, ...]:
        return tuple(
            manifest
            for manifest in self._typed_outcome_captures()
            if manifest.source == outcome.source
            and manifest.raw_payload_sha256 == outcome.raw_payload_sha256
            and (manifest.source_snapshot_at_utc or manifest.response_received_at_utc)
            == outcome.source_version_or_retrieved_at_utc
        )

    def displayed_candidates(self, event_id: str) -> tuple[DisplayedQuoteCandidate, ...]:
        candidates = [
            candidate
            for graph in self._forecast_graphs()
            if graph.run.canonical_event_id == event_id
            for candidate in graph.candidates
        ]
        if len({item.candidate_id for item in candidates}) != len(candidates):
            raise DataIntegrityError("duplicate durable forecast candidate")
        return tuple(sorted(candidates, key=lambda item: item.candidate_id))

    def quote_for(self, quote_id: str) -> MoneylineQuote:
        values = [
            quote
            for graph in self._forecast_graphs()
            for quote in graph.quotes
            if quote.quote_id == quote_id
        ]
        if len(values) != 1:
            raise KeyError(quote_id)
        return values[0]

    def read_prediction(self, prediction_id: str) -> Prediction:
        values = [
            prediction
            for graph in self._forecast_graphs()
            for prediction in graph.predictions
            if prediction.prediction_id == prediction_id
        ]
        if len(values) != 1:
            raise KeyError(prediction_id)
        return values[0]

    def read_candidate(self, candidate_id: str) -> DisplayedQuoteCandidate:
        values = [
            candidate
            for graph in self._forecast_graphs()
            for candidate in graph.candidates
            if candidate.candidate_id == candidate_id
        ]
        if len(values) != 1:
            raise KeyError(candidate_id)
        return values[0]

    def tickets_for_event(self, event_id: str) -> tuple[ActualTicket, ...]:
        return tuple(
            sorted(
                (
                    ticket
                    for ticket, linked_event in self._typed_registered_tickets()
                    if linked_event == event_id
                ),
                key=lambda item: item.ticket_id,
            )
        )

    def read_ticket(self, ticket_id: str) -> ActualTicket:
        values = [
            ticket
            for ticket, _ in self._typed_registered_tickets()
            if ticket.ticket_id == ticket_id
        ]
        if len(values) != 1:
            raise KeyError(ticket_id)
        return values[0]

    def report_only_ticket_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        for logical_id, value in self._record_rows(self._REPORT_ONLY):
            if set(value) != {"ticket_id"} or not isinstance(value["ticket_id"], str):
                raise DataIntegrityError("report-only ticket fields are invalid")
            if value["ticket_id"] != logical_id:
                raise DataIntegrityError("report-only ticket logical ID is substituted")
            values.append(logical_id)
        return tuple(sorted(values))

    def settlement_envelopes(self) -> tuple[SettlementEnvelope, ...]:
        return self._validated_settlement_envelopes()

    def operator_evidence(
        self, quote: MoneylineQuote, outcome: Outcome
    ) -> OperatorSettlementEvidence:
        values = [
            item
            for item in self._typed_evidence()
            if item.quote_id == quote.quote_id
            and item.outcome_id == outcome.outcome_id
            and item.outcome_version == outcome.outcome_version
        ]
        if values:
            latest_frozen = max(item.frozen_at_utc for item in values)
            latest = [item for item in values if item.frozen_at_utc == latest_frozen]
            if len({item.evidence_id for item in latest}) != 1:
                raise ValueError("equal-time conflicting operator evidence cannot supersede")
            selected = latest[0]
            self._validate_operator_evidence(selected, quote, outcome)
            return selected
        observed = outcome.source_version_or_retrieved_at_utc
        frozen = max(observed, outcome.finalized_at_utc or observed)
        identity = (
            f"missing|{quote.quote_id}|{outcome.outcome_id}|{outcome.outcome_version}|"
            f"{quote.settlement_policy_version}"
        )
        missing = OperatorSettlementEvidence(
            sha256(identity.encode()).hexdigest(),
            quote.quote_id,
            quote.canonical_event_id,
            outcome.outcome_id,
            outcome.outcome_version,
            quote.book_key,
            quote.settlement_policy_url,
            quote.settlement_policy_version,
            outcome.game_status,
            "unresolved",
            observed,
            frozen,
        )
        self.register_operator_evidence(missing)
        return missing

    def register_operator_evidence(self, evidence: OperatorSettlementEvidence) -> None:
        quote = self.quote_for(evidence.quote_id)
        outcome = self.read_outcome(evidence.outcome_id, evidence.outcome_version)
        self._validate_operator_evidence(evidence, quote, outcome)
        lock_path = (
            self.root
            / "locks"
            / f"operator-evidence-{sha256(evidence.evidence_id.encode()).hexdigest()}.lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                collisions = [
                    item
                    for item in self._typed_evidence()
                    if item.evidence_id == evidence.evidence_id
                ]
                if collisions:
                    if collisions == [evidence]:
                        return
                    raise ValueError("operator evidence ID must be globally unique")
                self.ledger.append(
                    self._OPERATOR_EVIDENCE,
                    (
                        f"{evidence.quote_id}|{evidence.outcome_id}|"
                        f"{evidence.outcome_version}|{evidence.evidence_id}"
                    ),
                    {
                        "evidence_id": evidence.evidence_id,
                        "quote_id": evidence.quote_id,
                        "canonical_event_id": evidence.canonical_event_id,
                        "outcome_id": evidence.outcome_id,
                        "outcome_version": evidence.outcome_version,
                        "operator": evidence.operator,
                        "settlement_policy_url": evidence.settlement_policy_url,
                        "settlement_policy_version": evidence.settlement_policy_version,
                        "operator_event_status": evidence.operator_event_status,
                        "resolution": evidence.resolution,
                        "observed_at_utc": evidence.observed_at_utc.isoformat(),
                        "frozen_at_utc": evidence.frozen_at_utc.isoformat(),
                    },
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _validate_operator_evidence(
        evidence: OperatorSettlementEvidence,
        quote: MoneylineQuote,
        outcome: Outcome,
    ) -> None:
        if (
            evidence.quote_id != quote.quote_id
            or evidence.canonical_event_id != quote.canonical_event_id
            or evidence.outcome_id != outcome.outcome_id
            or evidence.outcome_version != outcome.outcome_version
            or evidence.operator != quote.book_key
            or evidence.settlement_policy_url != quote.settlement_policy_url
            or evidence.settlement_policy_version != quote.settlement_policy_version
            or evidence.observed_at_utc < outcome.source_version_or_retrieved_at_utc
        ):
            raise ValueError("operator evidence does not match persisted quote/outcome/policy")

    def latest_settlement(
        self, candidate_id: str | None, ticket_id: str | None
    ) -> Settlement | None:
        envelope = self.latest_settlement_envelope(candidate_id, ticket_id)
        return None if envelope is None else envelope.settlement

    def latest_settlement_envelope(
        self, candidate_id: str | None, ticket_id: str | None
    ) -> SettlementEnvelope | None:
        all_envelopes = self._validated_settlement_envelopes()
        values = [
            envelope
            for envelope in all_envelopes
            if envelope.settlement.candidate_id == candidate_id
            and envelope.settlement.ticket_id == ticket_id
        ]
        return values[-1] if values else None

    def _validated_settlement_envelopes(self) -> tuple[SettlementEnvelope, ...]:
        envelopes: list[SettlementEnvelope] = []
        for logical_id, value in self._record_rows(self._SETTLEMENTS):
            envelope = self._envelope(value)
            settlement = envelope.settlement
            if logical_id != f"{settlement.settlement_id}|{settlement.settlement_version}":
                raise DataIntegrityError("settlement logical ID is substituted")
            try:
                outcome = self.read_outcome(envelope.outcome_id, envelope.outcome_version)
            except KeyError as error:
                raise DataIntegrityError("settlement outcome reference is dangling") from error
            if settlement.canonical_event_id != outcome.canonical_event_id:
                raise DataIntegrityError("settlement outcome event lineage is invalid")
            evidence_values = [
                item
                for item in self._typed_evidence()
                if item.evidence_id == envelope.operator_evidence_id
            ]
            if len(evidence_values) != 1:
                raise DataIntegrityError("settlement operator evidence reference is dangling")
            evidence = evidence_values[0]
            if (
                evidence.outcome_id != envelope.outcome_id
                or evidence.outcome_version != envelope.outcome_version
                or evidence.frozen_at_utc != envelope.operator_evidence_frozen_at_utc
            ):
                raise DataIntegrityError("settlement operator evidence lineage is invalid")
            envelopes.append(envelope)
        grouped: dict[tuple[str | None, str | None], list[SettlementEnvelope]] = {}
        for envelope in envelopes:
            settlement = envelope.settlement
            grouped.setdefault((settlement.candidate_id, settlement.ticket_id), []).append(envelope)
        ordered: list[SettlementEnvelope] = []
        for key in sorted(grouped, key=str):
            chain = sorted(
                grouped[key],
                key=lambda item: (
                    item.settlement.settlement_version,
                    item.settlement.settlement_id,
                ),
            )
            versions = [item.settlement.settlement_version for item in chain]
            if versions != list(range(1, len(chain) + 1)):
                raise DataIntegrityError("settlement versions are not continuous and unique")
            for index, envelope in enumerate(chain):
                prior_id = None if index == 0 else chain[index - 1].settlement.settlement_id
                if envelope.settlement.supersedes_settlement_id != prior_id:
                    raise DataIntegrityError("settlement supersession chain is invalid")
                if index > 0 and (
                    envelope.outcome_id != chain[index - 1].outcome_id
                    or envelope.outcome_version < chain[index - 1].outcome_version
                ):
                    raise DataIntegrityError("settlement outcome lineage regressed")
                self._validate_settlement_truth(envelope, prior_id)
            ordered.extend(chain)
        return tuple(ordered)

    def _validate_settlement_truth(
        self, envelope: SettlementEnvelope, prior_id: str | None
    ) -> None:
        settlement = envelope.settlement
        outcome = self.read_outcome(envelope.outcome_id, envelope.outcome_version)
        evidence_values = [
            item
            for item in self._typed_evidence()
            if item.evidence_id == envelope.operator_evidence_id
        ]
        if len(evidence_values) != 1:
            raise DataIntegrityError("settlement operator evidence reference is dangling")
        evidence = evidence_values[0]
        try:
            if settlement.candidate_id is not None:
                candidate = self.read_candidate(settlement.candidate_id)
                quote = self.quote_for(candidate.quote_id)
                expected = settle_displayed_candidate(candidate, quote, outcome, evidence)
            else:
                if settlement.ticket_id is None:
                    raise DataIntegrityError("settlement position lineage is missing")
                ticket = self.read_ticket(settlement.ticket_id)
                if ticket.candidate_id is None:
                    raise DataIntegrityError("ticket settlement has no model candidate")
                candidate = self.read_candidate(ticket.candidate_id)
                quote = self.quote_for(candidate.quote_id)
                expected = OutcomeWorkflow._settle_ticket(
                    ticket, candidate, quote, outcome, evidence
                )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("settlement position/economic lineage is invalid") from error
        version = settlement.settlement_version
        expected_id = expected.settlement_id
        if version > 1:
            if prior_id is None:
                raise DataIntegrityError("corrected settlement has no prior version")
            identity = (
                f"{settlement.candidate_id}|{settlement.ticket_id}|{outcome.outcome_id}|"
                f"{outcome.outcome_version}|{evidence.evidence_id}|{version}|{prior_id}"
            )
            expected_id = sha256(identity.encode()).hexdigest()
        expected = Settlement.model_validate(
            expected.model_copy(
                update={
                    "settlement_id": expected_id,
                    "settlement_version": version,
                    "supersedes_settlement_id": prior_id,
                }
            ).model_dump()
        )
        if (
            settlement != expected
            or envelope.raw_payload_sha256 != outcome.raw_payload_sha256
            or envelope.source != outcome.source
        ):
            raise DataIntegrityError("settlement does not equal recomputed immutable truth")
        manifest = self._outcome_capture(envelope.capture_id)
        if (
            manifest.raw_payload_sha256 != envelope.raw_payload_sha256
            or manifest.code_sha != envelope.code_sha
            or manifest.dependency_lock_sha256 != envelope.dependency_lock_sha256
            or manifest.source != envelope.source
        ):
            raise DataIntegrityError("settlement capture lineage is invalid")

    def append_settlement(self, envelope: SettlementEnvelope) -> None:
        settlement = envelope.settlement
        try:
            outcome = self.read_outcome(envelope.outcome_id, envelope.outcome_version)
        except KeyError as error:
            raise DataIntegrityError("settlement outcome reference is dangling") from error
        evidence = [
            item
            for item in self._typed_evidence()
            if item.evidence_id == envelope.operator_evidence_id
        ]
        if (
            settlement.canonical_event_id != outcome.canonical_event_id
            or len(evidence) != 1
            or evidence[0].outcome_id != envelope.outcome_id
            or evidence[0].outcome_version != envelope.outcome_version
            or evidence[0].frozen_at_utc != envelope.operator_evidence_frozen_at_utc
        ):
            raise DataIntegrityError("settlement outcome/operator lineage is invalid")
        prior = self.latest_settlement_envelope(settlement.candidate_id, settlement.ticket_id)
        expected_version = 1 if prior is None else prior.settlement.settlement_version + 1
        expected_supersedes = None if prior is None else prior.settlement.settlement_id
        if prior is not None and (
            envelope.outcome_id != prior.outcome_id
            or envelope.outcome_version < prior.outcome_version
        ):
            raise DataIntegrityError("settlement append regresses outcome correction lineage")
        if (
            settlement.settlement_version != expected_version
            or settlement.supersedes_settlement_id != expected_supersedes
        ):
            raise DataIntegrityError("settlement append does not extend the exact version chain")
        self._validate_settlement_truth(envelope, expected_supersedes)
        value = {
            "outcome_id": envelope.outcome_id,
            "outcome_version": envelope.outcome_version,
            "operator_evidence_id": envelope.operator_evidence_id,
            "operator_evidence_frozen_at_utc": envelope.operator_evidence_frozen_at_utc.isoformat(),
            "capture_id": envelope.capture_id,
            "raw_payload_sha256": envelope.raw_payload_sha256,
            "code_sha": envelope.code_sha,
            "dependency_lock_sha256": envelope.dependency_lock_sha256,
            "source": envelope.source,
            "settlement": envelope.settlement.model_dump(mode="json"),
        }
        self.ledger.append(
            self._SETTLEMENTS,
            f"{envelope.settlement.settlement_id}|{envelope.settlement.settlement_version}",
            value,
        )

    def mark_ticket_report_only(self, ticket_id: str) -> None:
        self.ledger.append(self._REPORT_ONLY, ticket_id, {"ticket_id": ticket_id})

    def append_quarantine(self, reason: str) -> None:
        self.ledger.append(
            self._QUARANTINE, sha256(reason.encode()).hexdigest(), {"reason": reason}
        )

    def latest_observation_watermark(
        self, event_id: str, source: str
    ) -> ObservationWatermark | None:
        values = [
            value
            for value in self._typed_watermarks()
            if value.canonical_event_id == event_id and value.source == source
        ]
        if not values:
            return None
        value = max(
            values,
            key=lambda item: (
                item.source_observed_at_utc,
                item.response_received_at_utc,
                item.raw_payload_sha256,
            ),
        )
        return value

    def append_observation_watermark(self, watermark: ObservationWatermark) -> None:
        value = {
            "canonical_event_id": watermark.canonical_event_id,
            "source": watermark.source,
            "source_observed_at_utc": watermark.source_observed_at_utc.isoformat(),
            "response_received_at_utc": watermark.response_received_at_utc.isoformat(),
            "raw_payload_sha256": watermark.raw_payload_sha256,
        }
        key = f"{watermark.canonical_event_id}|{watermark.source}"
        version_key = (
            f"{key}|{watermark.source_observed_at_utc.isoformat()}|"
            f"{watermark.response_received_at_utc.isoformat()}|{watermark.raw_payload_sha256}"
        )
        self.ledger.append(f"{self._WATERMARKS}-history", version_key, value)

    def prediction_score_inputs(
        self, season: int, through_week: int
    ) -> tuple[PredictionScoreInput, ...]:
        comparators = {
            graph.comparator.origin_run_id: graph.comparator
            for graph in self._forecast_graphs()
            if graph.comparator is not None
        }
        rows: list[PredictionScoreInput] = []
        for prediction in self._all_predictions():
            event = self._event(prediction.canonical_event_id)
            if event.season != season or event.week > through_week:
                continue
            outcome = self.latest_outcome(prediction.canonical_event_id, "nflverse")
            comparator = comparators.get(prediction.origin_run_id)
            market_input = None
            if comparator is not None:
                quotes = tuple(self.quote_for(item) for item in comparator.quote_ids)
                market_input = MarketComparatorInput(
                    comparator.market_comparator_id,
                    comparator.canonical_event_id,
                    comparator.origin,
                    comparator.decision_at_utc,
                    comparator.decision_at_utc,
                    tuple(item.quote_id for item in quotes),
                    tuple(item.capture_id for item in quotes),
                    comparator.r_home_market,
                )
            rows.append(PredictionScoreInput(season, event.week, prediction, outcome, market_input))
        return tuple(sorted(rows, key=lambda item: item.prediction.prediction_id))

    def forecast_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ForecastObligationInput, ...]:
        return tuple(self._forecast_obligations(season, through_week))

    def market_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ForecastObligationInput, ...]:
        return tuple(self._forecast_obligations(season, through_week))

    def displayed_return_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnScoreInput, ...]:
        return self._return_inputs(season, through_week, tickets=False)

    def actual_ticket_return_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnScoreInput, ...]:
        return self._return_inputs(season, through_week, tickets=True)

    def displayed_return_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnObligationInput, ...]:
        return self._return_obligations(season, through_week, tickets=False)

    def actual_ticket_return_obligation_inputs(
        self, season: int, through_week: int
    ) -> tuple[ReturnObligationInput, ...]:
        return self._return_obligations(season, through_week, tickets=True)

    def _forecast_obligations(
        self, season: int, through_week: int
    ) -> list[ForecastObligationInput]:
        result: list[ForecastObligationInput] = []
        prediction_event_ids: set[tuple[str, Origin, str]] = set()
        for prediction in self._all_predictions():
            event = self._event(prediction.canonical_event_id)
            if event.season == season and event.week <= through_week:
                prediction_event_ids.add(
                    (
                        prediction.canonical_event_id,
                        prediction.origin,
                        prediction.model_artifact_id,
                    )
                )
                result.append(
                    ForecastObligationInput(
                        prediction.canonical_event_id,
                        prediction.origin,
                        prediction.model_role,
                        prediction.model_artifact_id,
                        prediction.calibrator_artifact_id,
                        tuple(sorted(prediction.policy_versions.items())),
                        prediction.provenance_grade,
                    )
                )
        graph_prediction_runs = {
            graph.run.origin_run_id for graph in self._forecast_graphs() if graph.predictions
        }
        for bundle in self.forecast_obligation_bundles():
            obligation = bundle.obligation
            event = obligation.event
            if (
                event.season != season
                or event.week > through_week
                or (bundle.run is not None and bundle.run.origin_run_id in graph_prediction_runs)
            ):
                continue
            champion = obligation.champion_binding
            if champion is None:
                continue
            policies = dict(champion.policy_versions)
            policies["forecast"] = obligation.forecast_policy_version
            result.append(
                ForecastObligationInput(
                    event.canonical_event_id,
                    obligation.origin,
                    "champion",
                    champion.artifact_id,
                    champion.expected_calibrator_artifact_id,
                    tuple(sorted(policies.items())),
                    obligation.provenance_grade,
                )
            )
        return result

    def _return_inputs(
        self, season: int, through_week: int, *, tickets: bool
    ) -> tuple[ReturnScoreInput, ...]:
        rows: list[ReturnScoreInput] = []
        latest: dict[str, SettlementEnvelope] = {}
        for envelope in self._validated_settlement_envelopes():
            settlement = envelope.settlement
            if tickets != (settlement.ticket_id is not None):
                continue
            position_id = settlement.ticket_id or settlement.candidate_id
            if position_id is None:
                raise DataIntegrityError("settlement position lineage is missing")
            prior = latest.get(position_id)
            if prior is None or (
                settlement.settlement_version > prior.settlement.settlement_version
            ):
                latest[position_id] = envelope
        for position_id in sorted(latest):
            envelope = latest[position_id]
            settlement = envelope.settlement
            if settlement.ticket_id is not None:
                ticket = self.read_ticket(settlement.ticket_id)
                candidate_id = ticket.candidate_id
            else:
                ticket = None
                candidate_id = settlement.candidate_id
            if candidate_id is None:
                raise DataIntegrityError("settlement candidate lineage is missing")
            candidate = self.read_candidate(candidate_id)
            prediction = self.read_prediction(candidate.prediction_id)
            quote = self.quote_for(candidate.quote_id)
            event = self._event(prediction.canonical_event_id)
            if event.season != season or event.week > through_week:
                continue
            profit = settlement.profit_amount if tickets else settlement.profit_units
            rows.append(
                ReturnScoreInput(
                    season,
                    event.week,
                    event.canonical_event_id,
                    envelope.outcome_id,
                    envelope.outcome_version,
                    prediction.origin,
                    prediction.model_role,
                    prediction.model_artifact_id,
                    prediction.calibrator_artifact_id,
                    self._return_policies(prediction, candidate, quote),
                    profit,
                    ticket.stake if ticket is not None else Decimal(1),
                    prediction.provenance_grade,
                    candidate_id=None if tickets else candidate.candidate_id,
                    ticket_id=settlement.ticket_id,
                    settlement_id=settlement.settlement_id,
                    settlement_version=settlement.settlement_version,
                    supersedes_settlement_id=settlement.supersedes_settlement_id,
                    operator_evidence_id=envelope.operator_evidence_id,
                    settlement_result=settlement.result,
                )
            )
        return tuple(sorted(rows, key=lambda item: item.position_id))

    def _return_obligations(
        self, season: int, through_week: int, *, tickets: bool
    ) -> tuple[ReturnObligationInput, ...]:
        result: list[ReturnObligationInput] = []
        values: tuple[ActualTicket | DisplayedQuoteCandidate, ...]
        if tickets:
            values = tuple(self._typed_tickets())
        else:
            values = self._all_candidates()
        for value in values:
            if tickets:
                if not isinstance(value, ActualTicket):
                    raise DataIntegrityError("ticket obligation has candidate payload")
                ticket = value
                if not ticket.model_attributed or ticket.candidate_id is None:
                    continue
                candidate = self.read_candidate(ticket.candidate_id)
                position_id = ticket.ticket_id
            else:
                ticket = None
                if not isinstance(value, DisplayedQuoteCandidate):
                    raise DataIntegrityError("candidate obligation has ticket payload")
                candidate = value
                position_id = candidate.candidate_id
            prediction = self.read_prediction(candidate.prediction_id)
            quote = self.quote_for(candidate.quote_id)
            event = self._event(prediction.canonical_event_id)
            if event.season != season or event.week > through_week:
                continue
            result.append(
                ReturnObligationInput(
                    season,
                    event.week,
                    position_id,
                    event.canonical_event_id,
                    prediction.origin,
                    prediction.model_role,
                    prediction.model_artifact_id,
                    prediction.calibrator_artifact_id,
                    self._return_policies(prediction, candidate, quote),
                    prediction.provenance_grade,
                    candidate_id=None if tickets else candidate.candidate_id,
                    ticket_id=position_id if tickets else None,
                )
            )
        return tuple(sorted(result, key=lambda item: item.position_id))

    def _event(self, event_id: str) -> EventVersion:
        values = [
            bundle.obligation.event
            for bundle in self.forecast_obligation_bundles()
            if bundle.obligation.event.canonical_event_id == event_id
        ]
        if not values:
            raise KeyError(event_id)
        by_version: dict[int, EventVersion] = {}
        for value in values:
            prior = by_version.get(value.event_version)
            if prior is not None and prior != value:
                raise DataIntegrityError("conflicting canonical schedule event version")
            by_version[value.event_version] = value
        return by_version[max(by_version)]

    def _all_predictions(self) -> tuple[Prediction, ...]:
        values = [
            prediction for graph in self._forecast_graphs() for prediction in graph.predictions
        ]
        if len({item.prediction_id for item in values}) != len(values):
            raise DataIntegrityError("duplicate durable forecast prediction")
        return tuple(sorted(values, key=lambda item: item.prediction_id))

    def _all_candidates(self) -> tuple[DisplayedQuoteCandidate, ...]:
        values = [candidate for graph in self._forecast_graphs() for candidate in graph.candidates]
        if len({item.candidate_id for item in values}) != len(values):
            raise DataIntegrityError("duplicate durable forecast candidate")
        return tuple(sorted(values, key=lambda item: item.candidate_id))

    @staticmethod
    def _return_policies(
        prediction: Prediction,
        candidate: DisplayedQuoteCandidate,
        quote: MoneylineQuote,
    ) -> tuple[tuple[str, str], ...]:
        values = dict(prediction.policy_versions)
        values.update(
            {
                "candidate": candidate.policy_version,
                "feature_snapshot": prediction.feature_snapshot_id,
                "model_lane": prediction.model_lane,
                "market_semantics": quote.market_semantics_version,
                "operator": quote.settlement_policy_version,
                "settlement": quote.settlement_policy_version,
            }
        )
        return tuple(sorted(values.items()))

    def _typed_outcomes(self) -> tuple[Outcome, ...]:
        values: list[Outcome] = []
        for logical_id, value in self._record_rows(self._OUTCOMES):
            try:
                outcome = Outcome.model_validate(value)
            except (TypeError, ValueError) as error:
                raise DataIntegrityError("persisted outcome is invalid") from error
            if logical_id != f"{outcome.outcome_id}|{outcome.outcome_version}":
                raise DataIntegrityError("persisted outcome logical ID is substituted")
            values.append(outcome)
        grouped: dict[tuple[str, str], list[Outcome]] = {}
        watermarks = self._typed_watermarks()
        for outcome in values:
            expected_id = sha256(
                f"{outcome.canonical_event_id}|{outcome.source}".encode()
            ).hexdigest()
            if outcome.outcome_id != expected_id:
                raise DataIntegrityError("persisted outcome ID is not stable event/source identity")
            try:
                canonical_event = self._event(outcome.canonical_event_id)
            except KeyError as error:
                raise DataIntegrityError("persisted outcome has no canonical event") from error
            if outcome.event_version_at_final != canonical_event.event_version:
                raise DataIntegrityError("persisted outcome event version is invalid")
            if not self._captures_for_outcome(outcome):
                raise DataIntegrityError("persisted outcome has no exact capture manifest")
            if not any(
                item.canonical_event_id == outcome.canonical_event_id
                and item.source == outcome.source
                and item.source_observed_at_utc == outcome.source_version_or_retrieved_at_utc
                and item.raw_payload_sha256 == outcome.raw_payload_sha256
                for item in watermarks
            ):
                raise DataIntegrityError("persisted outcome has no exact observation watermark")
            grouped.setdefault((outcome.canonical_event_id, outcome.source), []).append(outcome)
        for stream in grouped.values():
            ordered = sorted(stream, key=lambda item: item.outcome_version)
            if [item.outcome_version for item in ordered] != list(
                range(1, len(ordered) + 1)
            ) or any(
                current.source_version_or_retrieved_at_utc
                < prior.source_version_or_retrieved_at_utc
                for prior, current in pairwise(ordered)
            ):
                raise DataIntegrityError("persisted outcome stream is non-contiguous or stale")
        return tuple(values)

    def _typed_registered_tickets(self) -> tuple[tuple[ActualTicket, str], ...]:
        if (self.root / "commits" / "workflow-ticket-events").exists():
            raise DataIntegrityError("legacy split ticket storage is not admissible")
        values: list[tuple[ActualTicket, str]] = []
        for logical_id, value in self._record_rows(self._TICKETS):
            try:
                if set(value) != {"ticket", "canonical_event_id"}:
                    raise TypeError("registered ticket fields are not exact")
                ticket_value = value["ticket"]
                event_id = value["canonical_event_id"]
                if not isinstance(ticket_value, dict) or not isinstance(event_id, str):
                    raise TypeError("registered ticket primitive fields are invalid")
                ticket = ActualTicket.model_validate(ticket_value)
            except (TypeError, ValueError) as error:
                raise DataIntegrityError("persisted ticket is invalid") from error
            if ticket.ticket_id != logical_id:
                raise DataIntegrityError("persisted ticket logical ID is substituted")
            values.append((ticket, event_id))
        return tuple(values)

    def _typed_tickets(self) -> tuple[ActualTicket, ...]:
        return tuple(ticket for ticket, _ in self._typed_registered_tickets())

    def _typed_evidence(self) -> tuple[OperatorSettlementEvidence, ...]:
        values: list[OperatorSettlementEvidence] = []
        for logical_id, value in self._record_rows(self._OPERATOR_EVIDENCE):
            evidence = self._evidence(value)
            expected = (
                f"{evidence.quote_id}|{evidence.outcome_id}|"
                f"{evidence.outcome_version}|{evidence.evidence_id}"
            )
            if logical_id != expected:
                raise DataIntegrityError("operator evidence logical ID is substituted")
            values.append(evidence)
        evidence_ids = [item.evidence_id for item in values]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise DataIntegrityError("operator evidence IDs are not globally unique")
        return tuple(values)

    def _typed_watermarks(self) -> tuple[ObservationWatermark, ...]:
        values: list[ObservationWatermark] = []
        namespace = f"{self._WATERMARKS}-history"
        for logical_id, value in self._record_rows(namespace):
            try:
                fields = {
                    "canonical_event_id",
                    "source",
                    "source_observed_at_utc",
                    "response_received_at_utc",
                    "raw_payload_sha256",
                }
                if set(value) != fields or any(not isinstance(value[name], str) for name in fields):
                    raise TypeError("watermark fields must be exact strings")
                watermark = ObservationWatermark(
                    cast(str, value["canonical_event_id"]),
                    cast(str, value["source"]),
                    datetime.fromisoformat(cast(str, value["source_observed_at_utc"])),
                    datetime.fromisoformat(cast(str, value["response_received_at_utc"])),
                    cast(str, value["raw_payload_sha256"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise DataIntegrityError("observation watermark is invalid") from error
            expected = (
                f"{watermark.canonical_event_id}|{watermark.source}|"
                f"{watermark.source_observed_at_utc.isoformat()}|"
                f"{watermark.response_received_at_utc.isoformat()}|"
                f"{watermark.raw_payload_sha256}"
            )
            if logical_id != expected:
                raise DataIntegrityError("observation watermark logical ID is substituted")
            values.append(watermark)
        return tuple(values)

    def _record_rows(self, namespace: str) -> tuple[tuple[str, dict[str, object]], ...]:
        directory = self.root / "commits" / namespace
        values: list[tuple[str, dict[str, object]]] = []
        if not directory.exists():
            return ()
        seen: set[str] = set()
        for marker_path in sorted(directory.glob("*.json")):
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DataIntegrityError("workflow commit marker is unreadable") from error
            fields = {"namespace", "idempotency_key", "content_sha256", "object_path"}
            if (
                not isinstance(marker, dict)
                or set(marker) != fields
                or not all(isinstance(item, str) for item in marker.values())
            ):
                raise DataIntegrityError("workflow commit marker fields are invalid")
            key = marker["idempotency_key"]
            if (
                marker["namespace"] != namespace
                or self.ledger.marker_path(namespace, key) != marker_path
            ):
                raise DataIntegrityError("workflow commit marker identity is substituted")
            if key in seen:
                raise DataIntegrityError("duplicate workflow logical record")
            seen.add(key)
            value = self.ledger.read(namespace, key)
            if value is None:
                raise DataIntegrityError("enumerated workflow record is missing")
            values.append((key, value))
        return tuple(values)

    def _records(self, namespace: str) -> tuple[dict[str, object], ...]:
        return tuple(value for _, value in self._record_rows(namespace))

    @staticmethod
    def _evidence(value: dict[str, object]) -> OperatorSettlementEvidence:
        try:
            fields = {
                "evidence_id",
                "quote_id",
                "canonical_event_id",
                "outcome_id",
                "outcome_version",
                "operator",
                "settlement_policy_url",
                "settlement_policy_version",
                "operator_event_status",
                "resolution",
                "observed_at_utc",
                "frozen_at_utc",
            }
            if set(value) != fields:
                raise TypeError("operator evidence fields are not exact")
            outcome_version = value["outcome_version"]
            if isinstance(outcome_version, bool) or not isinstance(outcome_version, int):
                raise TypeError("operator evidence outcome version is invalid")
            string_fields = fields - {"outcome_version"}
            if any(not isinstance(value[name], str) for name in string_fields):
                raise TypeError("operator evidence primitive fields are invalid")
            return OperatorSettlementEvidence(
                evidence_id=cast(str, value["evidence_id"]),
                quote_id=cast(str, value["quote_id"]),
                canonical_event_id=cast(str, value["canonical_event_id"]),
                outcome_id=cast(str, value["outcome_id"]),
                outcome_version=outcome_version,
                operator=cast(str, value["operator"]),
                settlement_policy_url=cast(str, value["settlement_policy_url"]),
                settlement_policy_version=cast(str, value["settlement_policy_version"]),
                operator_event_status=value["operator_event_status"],  # type: ignore[arg-type]
                resolution=value["resolution"],  # type: ignore[arg-type]
                observed_at_utc=datetime.fromisoformat(cast(str, value["observed_at_utc"])),
                frozen_at_utc=datetime.fromisoformat(cast(str, value["frozen_at_utc"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("operator evidence record is invalid") from error

    @staticmethod
    def _envelope(value: dict[str, object]) -> SettlementEnvelope:
        try:
            fields = {
                "outcome_id",
                "outcome_version",
                "operator_evidence_id",
                "operator_evidence_frozen_at_utc",
                "capture_id",
                "raw_payload_sha256",
                "code_sha",
                "dependency_lock_sha256",
                "source",
                "settlement",
            }
            if set(value) != fields:
                raise TypeError("settlement envelope fields are not exact")
            settlement_value = value["settlement"]
            outcome_version = value["outcome_version"]
            if not isinstance(settlement_value, dict):
                raise TypeError("settlement payload must be an object")
            if not isinstance(outcome_version, int) or isinstance(outcome_version, bool):
                raise TypeError("settlement outcome version must be an integer")
            string_fields = fields - {"outcome_version", "settlement"}
            if any(not isinstance(value[name], str) for name in string_fields):
                raise TypeError("settlement envelope primitive fields are invalid")
            return SettlementEnvelope(
                cast(str, value["outcome_id"]),
                outcome_version,
                cast(str, value["operator_evidence_id"]),
                datetime.fromisoformat(cast(str, value["operator_evidence_frozen_at_utc"])),
                cast(str, value["capture_id"]),
                cast(str, value["raw_payload_sha256"]),
                cast(str, value["code_sha"]),
                cast(str, value["dependency_lock_sha256"]),
                cast(str, value["source"]),
                Settlement.model_validate(settlement_value),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DataIntegrityError("settlement envelope is invalid") from error
