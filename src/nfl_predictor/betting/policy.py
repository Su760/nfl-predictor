from __future__ import annotations

import json
import math
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nfl_predictor.betting.math import displayed_ev, quarter_kelly
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin, PredictionStatus, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.contracts.markets import (
    DisplayedQuoteCandidate,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.identity.teams import CANONICAL_TEAM_ALIASES

NonBlank = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
OfficialStatus = Literal["pregame", "cancelled", "postponed", "suspended", "started"]
DecisionKey = tuple[str, str, str]


class BookSettlementContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    book_key: NonBlank
    market_semantics_version: NonBlank
    settlement_policy_url: Annotated[str, Field(pattern=r"^https://")]
    settlement_policy_version: NonBlank
    overtime_included: Literal[True]
    tie_handling: Literal["push"]
    verified_on: NonBlank


class ProviderTeamName(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: NonBlank
    canonical_team: NonBlank


@dataclass(frozen=True)
class QuoteEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class EventMatch:
    event: EventVersion | None
    source_event_id: str | None
    reason_code: str | None


@dataclass(frozen=True)
class TrustedEventSourceMatch:
    canonical_event_id: str
    event_version: int
    provider_source_key: Literal["the_odds_api"]
    source_event_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_event_id, str) or not self.canonical_event_id.strip():
            raise ValueError("trusted source match event ID must be non-blank")
        if isinstance(self.event_version, bool) or not isinstance(self.event_version, int):
            raise TypeError("trusted source match event version must be an integer")
        if self.event_version < 1:
            raise ValueError("trusted source match event version must be positive")
        if self.provider_source_key != "the_odds_api":
            raise ValueError("trusted source match provider key is invalid")
        if not isinstance(self.source_event_id, str) or not self.source_event_id.strip():
            raise ValueError("trusted source match source event ID must be non-blank")


@dataclass(frozen=True)
class CaptureSelection:
    capture_id: str | None
    quotes: tuple[MoneylineQuote, ...]
    reason_code: str | None


@dataclass(frozen=True)
class OfficialEventState:
    event: EventVersion
    status: OfficialStatus

    def __post_init__(self) -> None:
        if not isinstance(self.event, EventVersion):
            raise TypeError("official event state requires an EventVersion")
        if self.status not in {"pregame", "cancelled", "postponed", "suspended", "started"}:
            raise ValueError("unsupported official event status")

    def is_pregame_at(self, decision_at: datetime) -> bool:
        return self.status == "pregame" and decision_at < self.event.kickoff_at_utc


@dataclass(frozen=True)
class CandidateContext:
    expected_champion_artifact_id: str
    required_prediction_policy_versions: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.expected_champion_artifact_id.strip():
            raise ValueError("expected champion artifact ID must be non-blank")
        copied = dict(self.required_prediction_policy_versions)
        if not copied or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in copied.items()
        ):
            raise ValueError("required prediction policy versions must be nonempty and non-blank")
        object.__setattr__(self, "required_prediction_policy_versions", MappingProxyType(copied))


@dataclass(frozen=True)
class CalibrationBinEvidence:
    one_sided_upper_absolute_error: Decimal
    sample_count: int
    confidence: float
    cutoff_at_utc: datetime
    origin: Origin
    binning_method: Literal["equal_count"]
    out_of_sample: bool
    frozen: bool
    evidence_id: str


class OddsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: NonBlank = "odds-v1"
    candidate_policy_version: NonBlank = "candidate-v1"
    provider_source_key: Literal["the_odds_api"] = "the_odds_api"
    capture_enabled: bool = False
    real_staking_enabled: bool = False
    provider_plan_limit_observed: int = Field(default=500, strict=True, ge=1)
    provider_rules_observed_on: NonBlank = "2026-08-28"
    monthly_hard_stop: int = Field(default=400, strict=True, ge=1, le=400)
    maximum_books: int = Field(default=10, strict=True, ge=1, le=10)
    markets: tuple[Literal["h2h"], ...] = ("h2h",)
    odds_format: Literal["decimal"] = "decimal"
    t72_max_provider_age_seconds: int = Field(default=21600, strict=True, ge=0)
    t60_max_provider_age_seconds: int = Field(default=900, strict=True, ge=0)
    close_target_minutes: int = Field(default=5, strict=True, ge=1)
    close_window_opens_minutes: int = Field(default=10, strict=True, ge=1)
    close_window_closes_minutes: int = Field(default=1, strict=True, ge=1)
    minimum_calibration_bin_games: int = Field(default=50, strict=True, ge=1)
    calibration_buffer_one_sided_confidence: float = Field(default=0.95, gt=0, lt=1)
    displayed_fixed_stake_units: int = Field(default=1, strict=True, ge=1, le=1)
    kelly_fraction: float = Field(default=0.25, gt=0, le=1)
    provider_team_names: tuple[ProviderTeamName, ...] = ()
    book_allowlist: tuple[NonBlank, ...] = ()
    book_settlement_contracts: tuple[BookSettlementContract, ...] = ()
    worst_case_monthly_credits: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def require_fail_closed_capture_configuration(self) -> OddsPolicy:
        if self.real_staking_enabled:
            raise ValueError("real staking is not enabled by the evidence-only policy")
        provider_names = [item.provider_name for item in self.provider_team_names]
        canonical_teams = [item.canonical_team for item in self.provider_team_names]
        canonical_nfl_teams = set(CANONICAL_TEAM_ALIASES.values())
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("provider team names must be unique")
        if len(canonical_teams) != len(set(canonical_teams)):
            raise ValueError("provider team canonical mappings must be unique")
        if not set(canonical_teams) <= canonical_nfl_teams:
            raise ValueError("provider team mapping contains an unknown canonical team")
        if len(set(self.book_allowlist)) != len(self.book_allowlist):
            raise ValueError("book allowlist must be unique")
        contract_keys = [item.book_key for item in self.book_settlement_contracts]
        if len(set(contract_keys)) != len(contract_keys):
            raise ValueError("book settlement contracts must be unique")
        if not set(contract_keys) <= set(self.book_allowlist):
            raise ValueError("settlement contracts must belong to allowlisted books")
        if self.capture_enabled:
            if set(canonical_teams) != canonical_nfl_teams:
                raise ValueError("capture requires complete provider team coverage")
            if self.markets != ("h2h",):
                raise ValueError("capture requires the frozen full-game h2h market")
            if not 1 <= len(self.book_allowlist) <= self.maximum_books:
                raise ValueError("capture requires one to ten frozen allowlisted books")
            if set(contract_keys) != set(self.book_allowlist):
                raise ValueError("every allowlisted book requires one verified settlement contract")
            if self.worst_case_monthly_credits is None:
                raise ValueError("capture requires a frozen worst-case monthly projection")
            if self.worst_case_monthly_credits > self.monthly_hard_stop:
                raise ValueError("worst-case monthly projection exceeds the hard stop")
        return self

    def canonical_provider_team(self, provider_name: object) -> str | None:
        if not isinstance(provider_name, str):
            return None
        matches = [
            item.canonical_team
            for item in self.provider_team_names
            if item.provider_name == provider_name
        ]
        return matches[0] if len(matches) == 1 else None

    def contract_for(self, book_key: str) -> BookSettlementContract | None:
        matches = [item for item in self.book_settlement_contracts if item.book_key == book_key]
        return matches[0] if len(matches) == 1 else None

    def decode_json(self, raw_payload: bytes) -> list[Mapping[str, Any]]:
        try:
            decoded = json.loads(raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("odds payload is not valid JSON") from error
        if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
            raise ValueError("odds JSON must be a list of event objects")
        return decoded

    def match_event_result(
        self, provider_event: Mapping[str, Any], active_events: Iterable[EventVersion]
    ) -> EventMatch:
        events = tuple(active_events)
        source_event_id = provider_event.get("id")
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            return EventMatch(None, None, "PROVIDER_EVENT_ID_INVALID")
        if "commence_time" not in provider_event:
            return EventMatch(None, None, "PROVIDER_KICKOFF_MISSING")
        try:
            provider_kickoff = _parse_utc(provider_event.get("commence_time"), require_utc=True)
        except (TypeError, ValueError):
            return EventMatch(None, None, "PROVIDER_KICKOFF_INVALID")
        home_team = self.canonical_provider_team(provider_event.get("home_team"))
        away_team = self.canonical_provider_team(provider_event.get("away_team"))
        if home_team is None or away_team is None:
            return EventMatch(None, None, "PROVIDER_TEAM_UNKNOWN")
        source_id_owners = [
            event
            for event in events
            if event.source_event_ids.get(self.provider_source_key) == source_event_id
        ]
        if len(source_id_owners) > 1:
            return EventMatch(None, None, "PROVIDER_EVENT_ID_MULTIPLE_OWNERS")
        if any(
            event.home_team == away_team
            and event.away_team == home_team
            and event.kickoff_at_utc == provider_kickoff
            for event in events
        ):
            return EventMatch(None, None, "PROVIDER_TEAM_INVERSION")
        if source_id_owners:
            owner = source_id_owners[0]
            if owner.home_team != home_team or owner.away_team != away_team:
                return EventMatch(None, None, "PROVIDER_EVENT_ID_OWNERSHIP_CONFLICT")
            if owner.kickoff_at_utc != provider_kickoff:
                return EventMatch(None, None, "PROVIDER_KICKOFF_MISMATCH")
        scheduled_matches = [
            event
            for event in events
            if event.home_team == home_team
            and event.away_team == away_team
            and event.kickoff_at_utc == provider_kickoff
        ]
        established = [
            event
            for event in scheduled_matches
            if self.provider_source_key in event.source_event_ids
        ]
        if established:
            exact_alias = [
                event
                for event in established
                if event.source_event_ids[self.provider_source_key] == source_event_id
            ]
            if len(exact_alias) == 1:
                return EventMatch(exact_alias[0], source_event_id, None)
            if not exact_alias:
                return EventMatch(None, None, "PROVIDER_EVENT_ID_MISMATCH")
            return EventMatch(None, None, "EVENT_NOT_EXACTLY_MATCHED")
        bootstrap = [
            event
            for event in scheduled_matches
            if self.provider_source_key not in event.source_event_ids
        ]
        if len(bootstrap) == 1:
            return EventMatch(bootstrap[0], source_event_id, None)
        if len(bootstrap) > 1:
            return EventMatch(None, None, "EVENT_BOOTSTRAP_AMBIGUOUS")
        if any(event.home_team == home_team and event.away_team == away_team for event in events):
            return EventMatch(None, None, "PROVIDER_KICKOFF_MISMATCH")
        return EventMatch(None, None, "EVENT_NOT_EXACTLY_MATCHED")

    def match_event(
        self, provider_event: Mapping[str, Any], active_events: Iterable[EventVersion]
    ) -> EventVersion | None:
        return self.match_event_result(provider_event, active_events).event

    def trusted_source_match(self, event_match: EventMatch) -> TrustedEventSourceMatch:
        if not isinstance(event_match, EventMatch):
            raise TypeError("trusted source context requires an EventMatch")
        if (
            event_match.event is None
            or event_match.source_event_id is None
            or event_match.reason_code is not None
        ):
            raise ValueError("only an exact successful event match can become trusted")
        established_source_id = event_match.event.source_event_ids.get(self.provider_source_key)
        if (
            established_source_id is not None
            and established_source_id != event_match.source_event_id
        ):
            raise ValueError("event match conflicts with the established provider alias")
        return TrustedEventSourceMatch(
            canonical_event_id=event_match.event.canonical_event_id,
            event_version=event_match.event.event_version,
            provider_source_key=self.provider_source_key,
            source_event_id=event_match.source_event_id,
        )

    def source_match_reason(
        self,
        source_match: TrustedEventSourceMatch,
        event: EventVersion,
        quotes: Iterable[MoneylineQuote],
    ) -> str | None:
        if not isinstance(source_match, TrustedEventSourceMatch):
            raise TypeError("source match must be a TrustedEventSourceMatch")
        if (
            source_match.provider_source_key != self.provider_source_key
            or source_match.canonical_event_id != event.canonical_event_id
            or source_match.event_version != event.event_version
        ):
            return "EVENT_SOURCE_MATCH_MISMATCH"
        established_source_id = event.source_event_ids.get(self.provider_source_key)
        if (
            established_source_id is not None
            and established_source_id != source_match.source_event_id
        ):
            return "PROVIDER_EVENT_ID_MISMATCH"
        checked_quotes = tuple(quotes)
        if any(quote.canonical_event_id != event.canonical_event_id for quote in checked_quotes):
            return "EVENT_SOURCE_MATCH_MISMATCH"
        source_ids = {quote.source_event_id for quote in checked_quotes}
        if len(source_ids) > 1:
            return "COMPETING_PROVIDER_EVENT_IDS"
        if source_ids and source_ids != {source_match.source_event_id}:
            return "PROVIDER_EVENT_ID_MISMATCH"
        return None

    def parse_verified_full_game_h2h(
        self,
        *,
        bookmaker: Mapping[str, Any],
        event: EventVersion,
        source_event_id: str,
        manifest: CaptureManifest,
        raw_pointer: str,
    ) -> MoneylineQuote | Any:
        from nfl_predictor.markets.normalizer import QuoteRejection

        book_key = bookmaker.get("key")
        if not isinstance(book_key, str) or book_key not in self.book_allowlist:
            return QuoteRejection(raw_pointer, "BOOK_NOT_ALLOWLISTED")
        contract = self.contract_for(book_key)
        if contract is None:
            return QuoteRejection(raw_pointer, "SETTLEMENT_CONTRACT_NOT_VERIFIED")
        if manifest.response_received_at_utc < manifest.request_started_at_utc:
            return QuoteRejection(raw_pointer, "RESPONSE_BEFORE_REQUEST")
        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            return QuoteRejection(raw_pointer, "H2H_MARKET_MISSING")
        h2h = [
            (index, item)
            for index, item in enumerate(markets)
            if isinstance(item, dict) and item.get("key") == "h2h"
        ]
        if len(h2h) != 1:
            return QuoteRejection(raw_pointer, "SIDES_NOT_FROM_ONE_BOOK_SNAPSHOT")
        market_index, market = h2h[0]
        outcomes = market.get("outcomes")
        if not isinstance(outcomes, list):
            return QuoteRejection(raw_pointer, "SIDES_NOT_FROM_ONE_BOOK_SNAPSHOT")
        indexed_outcomes = [
            (index, item) for index, item in enumerate(outcomes) if isinstance(item, dict)
        ]
        expected = {event.home_team, event.away_team}
        normalized_outcomes: list[tuple[int, Mapping[str, Any], str]] = []
        for outcome_index, outcome in indexed_outcomes:
            raw_name = outcome.get("name")
            if raw_name == "Draw":
                return QuoteRejection(raw_pointer, "DRAW_OUTCOME_NOT_SUPPORTED")
            canonical_team = self.canonical_provider_team(raw_name)
            if canonical_team is None:
                return QuoteRejection(raw_pointer, "PROVIDER_TEAM_UNKNOWN")
            normalized_outcomes.append((outcome_index, outcome, canonical_team))
        names = [canonical_team for _, _, canonical_team in normalized_outcomes]
        if any(name not in expected for name in names):
            return QuoteRejection(raw_pointer, "SIDES_NOT_FROM_ONE_BOOK_SNAPSHOT")
        if len(outcomes) != 2 or set(names) != expected or len(names) != len(set(names)):
            return QuoteRejection(raw_pointer, "SIDES_NOT_FROM_ONE_BOOK_SNAPSHOT")
        try:
            provider_update = _parse_utc(bookmaker.get("last_update"))
        except (TypeError, ValueError):
            return QuoteRejection(raw_pointer, "PROVIDER_UPDATE_INVALID")
        if provider_update > manifest.response_received_at_utc:
            return QuoteRejection(raw_pointer, "PROVIDER_UPDATE_AFTER_RESPONSE")
        by_team = {
            canonical_team: (outcome_index, outcome)
            for outcome_index, outcome, canonical_team in normalized_outcomes
        }
        expected_sides: tuple[tuple[Literal["home", "away"], str], ...] = (
            ("home", event.home_team),
            ("away", event.away_team),
        )
        selections: list[MoneylineSelection] = []
        for side, team in expected_sides:
            outcome_index, chosen_outcome = by_team[team]
            try:
                price = Decimal(str(chosen_outcome.get("price")))
            except (InvalidOperation, ValueError):
                return QuoteRejection(raw_pointer, "INVALID_DECIMAL_PRICE")
            if not price.is_finite() or price <= Decimal(1):
                return QuoteRejection(raw_pointer, "INVALID_DECIMAL_PRICE")
            source_id = chosen_outcome.get("id")
            selections.append(
                MoneylineSelection(
                    side=side,
                    team=team,
                    decimal_price=price,
                    source_outcome_id=str(source_id) if source_id is not None else None,
                    raw_pointer=(
                        f"{raw_pointer}.markets[{market_index}].outcomes[{outcome_index}]"
                    ),
                )
            )
        identity = (
            f"{manifest.capture_id}|{event.canonical_event_id}|"
            f"{book_key}|{provider_update.isoformat()}"
        )
        return MoneylineQuote(
            quote_id=sha256(identity.encode()).hexdigest(),
            capture_id=manifest.capture_id,
            canonical_event_id=event.canonical_event_id,
            source_event_id=source_event_id,
            book_key=book_key,
            book_name=str(bookmaker.get("title") or book_key),
            market_key="h2h",
            period="full_game",
            provider_last_update_at_utc=provider_update,
            response_received_at_utc=manifest.response_received_at_utc,
            capture_lag_seconds=int(
                (
                    manifest.response_received_at_utc - manifest.request_started_at_utc
                ).total_seconds()
            ),
            provider_update_lag_seconds=int(
                (manifest.response_received_at_utc - provider_update).total_seconds()
            ),
            selections=(selections[0], selections[1]),
            overtime_included=contract.overtime_included,
            tie_handling=contract.tie_handling,
            market_semantics_version=contract.market_semantics_version,
            settlement_policy_url=contract.settlement_policy_url,
            settlement_policy_version=contract.settlement_policy_version,
            provenance_grade=ProvenanceGrade.A,
        )

    def eligibility(
        self,
        quote: MoneylineQuote,
        *,
        origin: Origin | str,
        decision_at: datetime,
        target_at: datetime | None = None,
    ) -> QuoteEligibility:
        chosen_origin = Origin(origin)
        reasons: list[str] = []
        if quote.provenance_grade is not ProvenanceGrade.A:
            reasons.append("QUOTE_NOT_VERIFIED_GRADE_A")
        if quote.response_received_at_utc > decision_at:
            reasons.append("QUOTE_CAPTURED_AFTER_DECISION")
        if target_at is not None and not (
            target_at - timedelta(minutes=10)
            <= quote.response_received_at_utc
            <= target_at + timedelta(minutes=10)
        ):
            reasons.append("QUOTE_OUTSIDE_ORIGIN_WINDOW")
        if quote.provider_last_update_at_utc > decision_at:
            reasons.append("PROVIDER_UPDATE_AFTER_DECISION")
        maximum_age = (
            self.t72_max_provider_age_seconds
            if chosen_origin is Origin.T72
            else self.t60_max_provider_age_seconds
        )
        if (decision_at - quote.provider_last_update_at_utc).total_seconds() > maximum_age:
            reasons.append("PROVIDER_UPDATE_TOO_OLD")
        contract = self.contract_for(quote.book_key)
        if quote.book_key not in self.book_allowlist or contract is None:
            reasons.append("BOOK_OR_SETTLEMENT_CONTRACT_NOT_VERIFIED")
        elif not _quote_matches_contract(quote, contract):
            reasons.append("SETTLEMENT_CONTRACT_MISMATCH")
        return QuoteEligibility(not reasons, tuple(reasons))

    def select_frozen_capture(
        self, quotes: Iterable[MoneylineQuote], prediction: Prediction
    ) -> CaptureSelection:
        eligible = tuple(
            quote
            for quote in quotes
            if quote.canonical_event_id == prediction.canonical_event_id
            and self.eligibility(
                quote,
                origin=prediction.origin,
                decision_at=prediction.decision_at_utc,
                target_at=prediction.target_at_utc,
            ).eligible
        )
        if not eligible:
            return CaptureSelection(None, (), "NO_ELIGIBLE_DISPLAYED_QUOTE")
        newest = max(quote.response_received_at_utc for quote in eligible)
        newest_capture_ids = {
            quote.capture_id for quote in eligible if quote.response_received_at_utc == newest
        }
        if len(newest_capture_ids) != 1:
            return CaptureSelection(None, (), "AMBIGUOUS_LATEST_CAPTURE")
        capture_id = next(iter(newest_capture_ids))
        selected = tuple(quote for quote in eligible if quote.capture_id == capture_id)
        if any(quote.response_received_at_utc != newest for quote in selected):
            return CaptureSelection(None, (), "INCONSISTENT_CAPTURE_TIMESTAMP")
        book_keys = [quote.book_key for quote in selected]
        if len(book_keys) != len(set(book_keys)):
            return CaptureSelection(None, (), "DUPLICATE_BOOK_IN_CAPTURE")
        return CaptureSelection(capture_id, selected, None)

    def eligible_quotes(
        self,
        quotes: Iterable[MoneylineQuote],
        *,
        prediction: Prediction,
        event_state: OfficialEventState,
        source_match: TrustedEventSourceMatch,
        side: str,
    ) -> tuple[MoneylineQuote, ...]:
        event = event_state.event
        expected_team = event.home_team if side == "home" else event.away_team
        eligible: list[MoneylineQuote] = []
        for quote in quotes:
            selections = [item for item in quote.selections if item.side == side]
            if (
                quote.canonical_event_id == prediction.canonical_event_id
                and quote.canonical_event_id == event.canonical_event_id
                and quote.source_event_id == source_match.source_event_id
                and len(selections) == 1
                and selections[0].team == expected_team
                and self.eligibility(
                    quote,
                    origin=prediction.origin,
                    decision_at=prediction.decision_at_utc,
                    target_at=prediction.target_at_utc,
                ).eligible
            ):
                eligible.append(quote)
        return tuple(eligible)

    @staticmethod
    def selection_price(quote: MoneylineQuote, side: str) -> Decimal:
        matches = [item.decimal_price for item in quote.selections if item.side == side]
        if len(matches) != 1:
            raise ValueError(f"quote must contain exactly one {side} selection")
        return matches[0]


def _parse_utc(value: object, *, require_utc: bool = False) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if require_utc and parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return parsed.astimezone(UTC)


def _quote_matches_contract(quote: MoneylineQuote, contract: BookSettlementContract) -> bool:
    return (
        quote.market_key == "h2h"
        and quote.period == "full_game"
        and quote.overtime_included is contract.overtime_included
        and quote.tie_handling == contract.tie_handling
        and quote.market_semantics_version == contract.market_semantics_version
        and quote.settlement_policy_url == contract.settlement_policy_url
        and quote.settlement_policy_version == contract.settlement_policy_version
    )


def load_odds_policy(path: Path) -> OddsPolicy:
    with path.open("rb") as handle:
        return OddsPolicy.model_validate(tomllib.load(handle))


@dataclass(frozen=True)
class CandidateEvaluation:
    decision: BettingDecision
    candidate: DisplayedQuoteCandidate | None


class CandidateIds(Protocol):
    def candidate(self, **values: Any) -> DisplayedQuoteCandidate: ...

    def decision(self, **values: Any) -> BettingDecision: ...


class CalibrationBins(Protocol):
    def lookup(self, **values: Any) -> CalibrationBinEvidence | None: ...


class DecisionRepository(Protocol):
    def get_or_create(
        self, key: DecisionKey, factory: Callable[[], CandidateEvaluation]
    ) -> CandidateEvaluation: ...


class InMemoryDecisionRepository:
    def __init__(self) -> None:
        self._values: dict[DecisionKey, CandidateEvaluation] = {}
        self._lock = Lock()

    def get_or_create(
        self, key: DecisionKey, factory: Callable[[], CandidateEvaluation]
    ) -> CandidateEvaluation:
        with self._lock:
            prior = self._values.get(key)
            if prior is not None:
                return prior
            created = factory()
            self._values[key] = created
            return created


class CandidatePolicy:
    def __init__(
        self,
        config: OddsPolicy,
        quote_policy: OddsPolicy,
        ids: CandidateIds,
        context: CandidateContext,
        repository: DecisionRepository,
    ) -> None:
        self.config = config
        self.quote_policy = quote_policy
        self.ids = ids
        self.context = context
        self.repository = repository

    def evaluate(
        self,
        prediction: Prediction,
        event_state: OfficialEventState,
        quotes: Iterable[MoneylineQuote],
        calibration_bins: CalibrationBins,
        source_match: TrustedEventSourceMatch,
    ) -> tuple[CandidateEvaluation, ...]:
        if not isinstance(source_match, TrustedEventSourceMatch):
            raise TypeError("candidate evaluation requires a TrustedEventSourceMatch")
        selection = self.quote_policy.select_frozen_capture(quotes, prediction)
        evaluations: list[CandidateEvaluation] = []
        sides: tuple[tuple[Literal["home", "away"], Decimal, Decimal], ...] = (
            ("home", prediction.p_home, prediction.p_away),
            ("away", prediction.p_away, prediction.p_home),
        )
        for side, raw_p_win, raw_p_loss in sides:
            key = (prediction.prediction_id, side, self.config.candidate_policy_version)

            def create_evaluation(
                chosen_side: Literal["home", "away"] = side,
                chosen_p_win: Decimal = raw_p_win,
                chosen_p_loss: Decimal = raw_p_loss,
            ) -> CandidateEvaluation:
                return self._evaluate_side(
                    prediction,
                    event_state,
                    selection,
                    calibration_bins,
                    chosen_side,
                    chosen_p_win,
                    chosen_p_loss,
                    source_match,
                )

            evaluations.append(self.repository.get_or_create(key, create_evaluation))
        return tuple(evaluations)

    def _evaluate_side(
        self,
        prediction: Prediction,
        event_state: OfficialEventState,
        selection: CaptureSelection,
        calibration_bins: CalibrationBins,
        side: Literal["home", "away"],
        raw_p_win: Decimal,
        raw_p_loss: Decimal,
        source_match: TrustedEventSourceMatch,
    ) -> CandidateEvaluation:
        event = event_state.event
        reasons: list[str] = []
        if prediction.provenance_grade is not ProvenanceGrade.A:
            reasons.append("PREDICTION_NOT_PROSPECTIVE_GRADE_A")
        if prediction.status is not PredictionStatus.COMPLETE:
            reasons.append("PREDICTION_NOT_COMPLETE")
        if prediction.model_role != "champion":
            reasons.append("BETTING_REQUIRES_FROZEN_CHAMPION")
        if prediction.model_artifact_id != self.context.expected_champion_artifact_id:
            reasons.append("CHAMPION_ARTIFACT_MISMATCH")
        if any(
            prediction.policy_versions.get(key) != value
            for key, value in self.context.required_prediction_policy_versions.items()
        ):
            reasons.append("PREDICTION_POLICY_VERSION_MISMATCH")
        if not event_state.is_pregame_at(prediction.decision_at_utc):
            reasons.append("EVENT_NOT_OFFICIAL_PREGAME")
        if event.canonical_event_id != prediction.canonical_event_id:
            reasons.append("EVENT_ID_MISMATCH")
        if event.event_version != prediction.event_version:
            reasons.append("EVENT_VERSION_MISMATCH")
        if selection.reason_code is not None:
            reasons.append(selection.reason_code)
        source_reason = self.quote_policy.source_match_reason(source_match, event, selection.quotes)
        if source_reason is not None:
            reasons.append(source_reason)
        eligible = self.quote_policy.eligible_quotes(
            selection.quotes,
            prediction=prediction,
            event_state=event_state,
            source_match=source_match,
            side=side,
        )
        best_quote = max(
            eligible,
            key=lambda quote: self.quote_policy.selection_price(quote, side),
            default=None,
        )
        if best_quote is None and "NO_ELIGIBLE_DISPLAYED_QUOTE" not in reasons:
            reasons.append("NO_ELIGIBLE_DISPLAYED_QUOTE")
        calibration_bin = calibration_bins.lookup(
            origin=prediction.origin,
            probability=raw_p_win,
            strictly_before_utc=prediction.decision_at_utc,
            minimum_games=self.config.minimum_calibration_bin_games,
            confidence=self.config.calibration_buffer_one_sided_confidence,
        )
        buffer = _validated_calibration_buffer(calibration_bin, prediction, self.config)
        if calibration_bin is None:
            reasons.append("NO_QUALIFYING_CALIBRATION_BIN")
        elif buffer is None:
            reasons.append("INVALID_CALIBRATION_EVIDENCE")
        candidate = None
        if not reasons and best_quote is not None and buffer is not None:
            buffered_p_win = max(Decimal(0), raw_p_win - buffer)
            buffered_p_loss = raw_p_loss + (raw_p_win - buffered_p_win)
            price = self.quote_policy.selection_price(best_quote, side)
            ev = displayed_ev(buffered_p_win, buffered_p_loss, price)
            if ev <= 0:
                reasons.append("BUFFERED_EV_NOT_POSITIVE")
            else:
                candidate = self.ids.candidate(
                    prediction=prediction,
                    quote=best_quote,
                    side=side,
                    price=price,
                    raw_p_win=raw_p_win,
                    buffered_p_win=buffered_p_win,
                    raw_p_loss=raw_p_loss,
                    buffered_p_loss=buffered_p_loss,
                    p_push=prediction.p_tie,
                    ev=ev,
                    kelly=quarter_kelly(buffered_p_win, buffered_p_loss, price),
                    policy_version=self.config.candidate_policy_version,
                )
        decision = self.ids.decision(
            prediction=prediction,
            side=side,
            quote_id=None if best_quote is None else best_quote.quote_id,
            candidate_id=None if candidate is None else candidate.candidate_id,
            status="candidate" if candidate is not None else "rejected",
            reason_codes=reasons,
            policy_version=self.config.candidate_policy_version,
        )
        return CandidateEvaluation(decision, candidate)


def _validated_calibration_buffer(
    calibration_bin: CalibrationBinEvidence | None,
    prediction: Prediction,
    config: OddsPolicy,
) -> Decimal | None:
    if calibration_bin is None:
        return None
    try:
        buffer = calibration_bin.one_sided_upper_absolute_error
        sample_count = calibration_bin.sample_count
        confidence = calibration_bin.confidence
        cutoff = calibration_bin.cutoff_at_utc
        origin = calibration_bin.origin
        binning_method = calibration_bin.binning_method
        out_of_sample = calibration_bin.out_of_sample
        frozen = calibration_bin.frozen
        evidence_id = calibration_bin.evidence_id
    except AttributeError:
        return None
    if (
        not isinstance(buffer, Decimal)
        or not buffer.is_finite()
        or not Decimal(0) <= buffer <= Decimal(1)
    ):
        return None
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < config.minimum_calibration_bin_games
    ):
        return None
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or float(confidence) != config.calibration_buffer_one_sided_confidence
    ):
        return None
    if (
        not isinstance(cutoff, datetime)
        or cutoff.tzinfo is None
        or cutoff.utcoffset() != timedelta(0)
        or cutoff >= prediction.decision_at_utc
    ):
        return None
    if origin is not prediction.origin or binning_method != "equal_count":
        return None
    if out_of_sample is not True or frozen is not True:
        return None
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        return None
    return buffer
