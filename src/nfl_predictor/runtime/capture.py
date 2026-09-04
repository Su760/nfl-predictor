"""Offline, raw-first production capture adapters."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, time
from io import BytesIO
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import polars as pl

from nfl_predictor.betting.policy import OddsPolicy
from nfl_predictor.capture.service import CaptureService
from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import CaptureManifest, NormalizedFact
from nfl_predictor.features.policy import FeaturePolicy
from nfl_predictor.identity.events import ScheduleFact
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.identity.teams import canonicalize_team
from nfl_predictor.markets.budget import OddsBudget
from nfl_predictor.markets.normalizer import normalize_h2h
from nfl_predictor.ratings.base import CompletedGame, TeamGameEpa
from nfl_predictor.ratings.colley import colley_ratings
from nfl_predictor.ratings.elo import EloRater
from nfl_predictor.ratings.epa import fit_opponent_adjusted_epa
from nfl_predictor.ratings.massey import massey_ratings
from nfl_predictor.runtime.lineage import DurableLineageRepository
from nfl_predictor.sources.base import SourceAdapter
from nfl_predictor.sources.nflverse import NflverseAdapter
from nfl_predictor.sources.outcomes import (
    OutcomeAdapter,
    OutcomeCapture,
    OutcomeObservation,
    OutcomeStatus,
)
from nfl_predictor.storage.ledger import canonical_json_bytes
from nfl_predictor.workflows.forecast import CaptureBundle, MarketCaptureDisabled

_SCHEDULE_COLUMNS = frozenset(
    {
        "game_id",
        "season",
        "game_type",
        "week",
        "gameday",
        "gametime",
        "away_team",
        "home_team",
        "away_score",
        "home_score",
        "location",
        "div_game",
        "stadium_id",
    }
)
_PBP_COLUMNS = frozenset(
    {
        "game_id",
        "play_id",
        "posteam",
        "defteam",
        "passer_player_id",
        "pass_attempt",
        "rush_attempt",
        "epa",
        "qb_epa",
        "cpoe",
    }
)
_FACT_SCHEMA_VERSION = "nflverse-football-facts-v1"
_EVENT_SCHEMA_VERSION = "nflverse-events-v1"


class _OddsAdapterFactory(Protocol):
    def __call__(self, api_key: str) -> SourceAdapter: ...


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamps must be aware UTC")
    return value


def _json_time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _integer(value: object, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer at least {minimum}")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-blank")
    return value


def _team(value: object, field: str) -> str:
    return canonicalize_team(_string(value, field))


def _kickoff(row: Mapping[str, object]) -> datetime:
    day = _string(row["gameday"], "gameday")
    game_time = _string(row["gametime"], "gametime")
    try:
        parsed_day = datetime.fromisoformat(day).date()
        parsed_time = time.fromisoformat(game_time)
    except ValueError as error:
        raise ValueError("schedule gameday/gametime must be ISO values") from error
    if parsed_time.tzinfo is not None:
        raise ValueError("schedule gametime must not include an offset")
    wall_time = datetime.combine(parsed_day, parsed_time)
    eastern = ZoneInfo("America/New_York")
    candidates: set[datetime] = set()
    for fold in (0, 1):
        localized = wall_time.replace(tzinfo=eastern, fold=fold)
        candidate = localized.astimezone(UTC)
        round_trip = candidate.astimezone(eastern)
        if round_trip.replace(tzinfo=None) == wall_time and round_trip.fold == fold:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError("schedule gameday/gametime is ambiguous or nonexistent in Eastern time")
    return candidates.pop()


def _score(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


class NflverseFootballNormalizer:
    """Schema-closed Arrow normalization for schedules and play-by-play evidence."""

    def __init__(self, feature_policy: FeaturePolicy) -> None:
        self.feature_policy = feature_policy

    def normalize_events(self, payload: bytes, manifest: CaptureManifest) -> tuple[EventVersion, ...]:
        rows = self._rows("schedules", payload)
        _, events = self._prepare_schedules(rows, manifest)
        return tuple(events[_string(row["game_id"], "game_id")] for row in rows)

    def normalize_facts(
        self,
        dataset: str,
        payload: bytes,
        manifest: CaptureManifest,
        target_event: EventVersion,
        *,
        schedules_payload: bytes | None = None,
    ) -> tuple[NormalizedFact, ...]:
        if dataset == "schedules":
            rows = self._rows(dataset, payload)
            self._reject_target_score(rows, target_event)
            _, events = self._prepare_schedules(rows, manifest)
            return self._schedule_facts(rows, events, manifest, target_event)
        if dataset == "pbp":
            if schedules_payload is None:
                raise ValueError("pbp normalization requires paired schedules")
            schedule_rows = self._rows("schedules", schedules_payload)
            schedules, events = self._prepare_schedules(schedule_rows, manifest)
            rows = self._rows(dataset, payload)
            return self._pbp_facts(rows, manifest, target_event, schedules, events)
        raise ValueError(f"unsupported football dataset: {dataset}")

    def normalize_capture(
        self,
        schedules_payload: bytes,
        schedules_manifest: CaptureManifest,
        pbp_payload: bytes,
        pbp_manifest: CaptureManifest,
        target_event: EventVersion,
    ) -> tuple[NormalizedFact, ...]:
        schedule_rows = self._rows("schedules", schedules_payload)
        pbp_rows = self._rows("pbp", pbp_payload)
        self._reject_target_score(schedule_rows, target_event)
        schedules, events = self._prepare_schedules(schedule_rows, schedules_manifest)
        schedule_facts = self._schedule_facts(
            schedule_rows, events, schedules_manifest, target_event
        )
        pbp_facts = self._pbp_facts(pbp_rows, pbp_manifest, target_event, schedules, events)
        priors = self._prior_facts(
            schedule_rows,
            pbp_rows,
            schedules_manifest,
            pbp_manifest,
            target_event,
            schedules,
            events,
        )
        return (*schedule_facts, *pbp_facts, *priors)

    @staticmethod
    def _reject_target_score(
        rows: Sequence[Mapping[str, object]], target_event: EventVersion
    ) -> None:
        target_source = target_event.source_event_ids.get("nflverse")
        for row in rows:
            if row["game_id"] == target_source and (
                row["home_score"] is not None or row["away_score"] is not None
            ):
                raise ValueError("target game score must not be normalized")

    def _prepare_schedules(
        self,
        rows: Sequence[Mapping[str, object]],
        manifest: CaptureManifest,
    ) -> tuple[dict[str, Mapping[str, object]], dict[str, EventVersion]]:
        schedules: dict[str, Mapping[str, object]] = {}
        events: dict[str, EventVersion] = {}
        for row in rows:
            game_id = _string(row["game_id"], "game_id")
            event = self._schedule_event(row, manifest)
            home_score = _score(row["home_score"], "home_score")
            away_score = _score(row["away_score"], "away_score")
            if (home_score is None) != (away_score is None):
                raise ValueError("schedule scores must be both present or both absent")
            if home_score is not None and event.kickoff_at_utc > manifest.response_received_at_utc:
                raise ValueError("finalized schedule row timestamp is after capture receipt")
            schedules[game_id] = row
            events[game_id] = event
        return schedules, events

    def _schedule_event(
        self, row: Mapping[str, object], manifest: CaptureManifest
    ) -> EventVersion:
        home_team = _team(row["home_team"], "home_team")
        away_team = _team(row["away_team"], "away_team")
        if home_team == away_team:
            raise ValueError("schedule home and away teams must differ")
        return ScheduleFact(
            source="nflverse",
            source_event_id=_string(row["game_id"], "game_id"),
            season=_integer(row["season"], "season", 1),
            season_type=_string(row["game_type"], "game_type"),
            week=_integer(row["week"], "week", 1),
            home_team=home_team,
            away_team=away_team,
            kickoff_at_utc=_kickoff(row),
            venue_id=(
                None if row["stadium_id"] is None else _string(row["stadium_id"], "stadium_id")
            ),
            neutral_site=_string(row["location"], "location").lower() == "neutral",
            observed_at_utc=manifest.response_received_at_utc,
            available_at_utc=manifest.response_received_at_utc,
            captured_at_utc=manifest.response_received_at_utc,
            raw_payload_sha256=manifest.raw_payload_sha256,
        ).to_event_version()

    def _rows(self, dataset: str, payload: bytes) -> list[dict[str, object]]:
        required = _SCHEDULE_COLUMNS if dataset == "schedules" else _PBP_COLUMNS if dataset == "pbp" else None
        if required is None:
            raise ValueError(f"unsupported football dataset: {dataset}")
        try:
            frame = pl.read_ipc(BytesIO(payload))
        except Exception as error:
            raise ValueError(f"{dataset} IPC payload is unreadable") from error
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{dataset} missing required columns: {', '.join(missing)}")
        rows = cast(list[dict[str, object]], frame.select(sorted(required)).to_dicts())
        identities: set[tuple[object, ...]] = set()
        for row in rows:
            game_id = _string(row["game_id"], "game_id")
            identity = (
                game_id,
                _integer(row["play_id"], "play_id"),
            ) if dataset == "pbp" else (game_id,)
            if identity in identities:
                raise ValueError(f"{dataset} contains duplicate provider row identities")
            identities.add(identity)
        return rows

    def _schedule_facts(
        self,
        rows: Sequence[Mapping[str, object]],
        events: Mapping[str, EventVersion],
        manifest: CaptureManifest,
        target_event: EventVersion,
    ) -> tuple[NormalizedFact, ...]:
        facts: list[NormalizedFact] = []
        target_source = target_event.source_event_ids.get("nflverse")
        target_seen = False
        for row in rows:
            game_id = _string(row["game_id"], "game_id")
            home_score = _score(row["home_score"], "home_score")
            away_score = _score(row["away_score"], "away_score")
            event = events[game_id]
            kickoff = event.kickoff_at_utc
            if game_id == target_source:
                target_seen = True
                if home_score is not None or away_score is not None:
                    raise ValueError("target game score must not be normalized")
                facts.append(
                    self._fact(
                        manifest=manifest,
                        input_manifests=(manifest,),
                        fact_type="division_alignment",
                        entity_keys={
                            "canonical_event_id": target_event.canonical_event_id,
                            "season": str(target_event.season),
                            "home_team": target_event.home_team,
                            "away_team": target_event.away_team,
                        },
                        payload={"same_division": self._bool(row["div_game"], "div_game")},
                        provider_record_id=game_id,
                        raw_pointer=f"{manifest.raw_path}#game_id={game_id}",
                        observed_at=None,
                    )
                )
                continue
            if (home_score is None) != (away_score is None):
                raise ValueError("schedule scores must be both present or both absent")
            if home_score is None:
                continue
            home_team = event.home_team
            away_team = event.away_team
            content = {
                "season": event.season,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "neutral_site": event.neutral_site,
                "kickoff_at_utc": kickoff.isoformat(),
            }
            for team in (home_team, away_team):
                facts.append(
                    self._fact(
                        manifest=manifest,
                        input_manifests=(manifest,),
                        fact_type="completed_game",
                        entity_keys={"canonical_event_id": event.canonical_event_id, "team": team},
                        payload=content,
                        provider_record_id=game_id,
                        raw_pointer=f"{manifest.raw_path}#game_id={game_id}",
                        observed_at=kickoff,
                    )
                )
        if not target_seen:
            raise ValueError("target event is missing from schedules")
        return tuple(facts)

    def _pbp_facts(
        self,
        rows: Sequence[Mapping[str, object]],
        manifest: CaptureManifest,
        target_event: EventVersion,
        schedules: Mapping[str, Mapping[str, object]],
        events: Mapping[str, EventVersion],
    ) -> tuple[NormalizedFact, ...]:
        target_source = target_event.source_event_ids.get("nflverse")
        aggregates: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
        quarterbacks: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
        for row in rows:
            game_id = _string(row["game_id"], "game_id")
            if game_id == target_source:
                continue
            schedule = schedules.get(game_id)
            if schedule is None:
                raise ValueError("play-by-play game is missing from schedule context")
            if _kickoff(schedule) > manifest.response_received_at_utc:
                raise ValueError("play-by-play row timestamp is after capture receipt")
            if not self._final_schedule(schedule, manifest):
                continue
            offense = _team(row["posteam"], "posteam")
            defense = _team(row["defteam"], "defteam")
            if offense == defense:
                raise ValueError("play-by-play offense and defense teams must differ")
            for metric in ("epa", "qb_epa", "cpoe"):
                _finite(row[metric], metric)
            pass_attempt = _integer(row["pass_attempt"], "pass_attempt")
            rush_attempt = _integer(row["rush_attempt"], "rush_attempt")
            if pass_attempt not in {0, 1} or rush_attempt not in {0, 1}:
                raise ValueError("play-by-play attempts must be binary")
            aggregates[(game_id, offense, defense)].append(row)
            if pass_attempt:
                passer = _string(row["passer_player_id"], "passer_player_id")
                quarterbacks[(game_id, offense, passer)].append(row)
        self._require_directional_closure(aggregates, schedules, target_source, manifest)
        facts: list[NormalizedFact] = []
        for (game_id, offense, defense), plays in sorted(aggregates.items()):
            schedule = schedules[game_id]
            observed = _kickoff(schedule)
            facts.append(
                self._fact(
                    manifest=manifest,
                    input_manifests=(manifest,),
                    fact_type="team_game_epa",
                    entity_keys={
                        "canonical_event_id": events[game_id].canonical_event_id,
                        "team": offense,
                        "offense_team": offense,
                        "defense_team": defense,
                    },
                    payload=self._epa_payload(plays),
                    provider_record_id=f"{game_id}:{offense}:{defense}",
                    raw_pointer=f"{manifest.raw_path}#game_id={game_id},posteam={offense}",
                    observed_at=observed,
                )
            )
        for (game_id, team, passer), plays in sorted(quarterbacks.items()):
            schedule = schedules[game_id]
            attempts = len(plays)
            facts.append(
                self._fact(
                    manifest=manifest,
                    input_manifests=(manifest,),
                    fact_type="qb_trailing",
                    entity_keys={
                        "canonical_event_id": events[game_id].canonical_event_id,
                        "team": team,
                        "player_id": passer,
                    },
                    payload={
                        "attempts": attempts,
                        "epa_per_play": sum(_finite(play["qb_epa"], "qb_epa") for play in plays)
                        / attempts,
                        "cpoe": sum(_finite(play["cpoe"], "cpoe") for play in plays) / attempts,
                    },
                    provider_record_id=f"{game_id}:{team}:{passer}",
                    raw_pointer=f"{manifest.raw_path}#game_id={game_id},passer={passer}",
                    observed_at=_kickoff(schedule),
                )
            )
        return tuple(facts)

    @staticmethod
    def _require_directional_closure(
        aggregates: Mapping[tuple[str, str, str], Sequence[Mapping[str, object]]],
        schedules: Mapping[str, Mapping[str, object]],
        target_source: str | None,
        manifest: CaptureManifest,
    ) -> None:
        directions: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for game_id, offense, defense in aggregates:
            directions[game_id].add((offense, defense))
        for game_id, schedule in schedules.items():
            if game_id == target_source or not NflverseFootballNormalizer._final_schedule(
                schedule, manifest
            ):
                continue
            home_team = _team(schedule["home_team"], "home_team")
            away_team = _team(schedule["away_team"], "away_team")
            expected = {(home_team, away_team), (away_team, home_team)}
            if directions.get(game_id, set()) != expected:
                raise ValueError(
                    f"play-by-play directional closure requires home and away offenses for {game_id}"
                )

    def _prior_facts(
        self,
        schedules: Sequence[Mapping[str, object]],
        pbp_rows: Sequence[Mapping[str, object]],
        schedules_manifest: CaptureManifest,
        pbp_manifest: CaptureManifest,
        target_event: EventVersion,
        schedule_context: Mapping[str, Mapping[str, object]],
        events: Mapping[str, EventVersion],
    ) -> tuple[NormalizedFact, ...]:
        previous_season = target_event.season - 1
        completed = [
            self._completed_game(row, events[_string(row["game_id"], "game_id")])
            for row in schedules
            if _integer(row["season"], "season", 1) == previous_season
            and self._final_schedule(row, schedules_manifest)
        ]
        epa_rows = self._epa_rows(
            pbp_rows, previous_season, pbp_manifest, schedule_context, events
        )
        teams = {team for game in completed for team in (game.home_team, game.away_team)}
        teams.update(row.offense_team for row in epa_rows)
        available_at = max(
            schedules_manifest.response_received_at_utc,
            pbp_manifest.response_received_at_utc,
        )
        input_manifests = (schedules_manifest, pbp_manifest)
        ratings = self._ratings(completed, epa_rows, available_at)
        facts: list[NormalizedFact] = [
            self._fact(
                manifest=pbp_manifest,
                input_manifests=input_manifests,
                fact_type="league_strength_prior",
                entity_keys={
                    "canonical_event_id": target_event.canonical_event_id,
                    "season": str(target_event.season),
                },
                payload=self._league_strength(ratings),
                provider_record_id=f"league:{previous_season}",
                raw_pointer=f"{pbp_manifest.raw_path}#season={previous_season}",
                observed_at=None,
            )
        ]
        passing: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for row in pbp_rows:
            schedule = schedule_context.get(_string(row["game_id"], "game_id"))
            if schedule is None or _integer(schedule["season"], "season", 1) != previous_season:
                continue
            if self._final_schedule(schedule, pbp_manifest) and _integer(
                row["pass_attempt"], "pass_attempt"
            ):
                passing[_team(row["posteam"], "posteam")].append(row)
        for team in sorted(teams):
            facts.append(
                self._fact(
                    manifest=pbp_manifest,
                    input_manifests=input_manifests,
                    fact_type="team_strength_prior",
                    entity_keys={"team": team, "season": str(target_event.season)},
                    payload={key: float(ratings[key].get(team, 0.0)) for key in ratings},
                    provider_record_id=f"strength:{previous_season}:{team}",
                    raw_pointer=f"{pbp_manifest.raw_path}#season={previous_season},team={team}",
                    observed_at=None,
                )
            )
            attempts = passing.get(team, [])
            if attempts:
                epa = sum(_finite(row["qb_epa"], "qb_epa") for row in attempts) / len(attempts)
                cpoe = sum(_finite(row["cpoe"], "cpoe") for row in attempts) / len(attempts)
            else:
                epa, cpoe = 0.0, 0.0
            facts.append(
                self._fact(
                    manifest=pbp_manifest,
                    input_manifests=input_manifests,
                    fact_type="team_passing_prior",
                    entity_keys={"team": team, "season": str(target_event.season)},
                    payload={"epa_per_play": epa, "cpoe": cpoe},
                    provider_record_id=f"passing:{previous_season}:{team}",
                    raw_pointer=f"{pbp_manifest.raw_path}#season={previous_season},team={team}",
                    observed_at=None,
                )
            )
        return tuple(facts)

    def _completed_game(
        self, row: Mapping[str, object], event: EventVersion
    ) -> CompletedGame:
        return CompletedGame(
            canonical_event_id=event.canonical_event_id,
            season=event.season,
            home_team=event.home_team,
            away_team=event.away_team,
            home_score=cast(int, _score(row["home_score"], "home_score")),
            away_score=cast(int, _score(row["away_score"], "away_score")),
            neutral_site=event.neutral_site,
            finalized_at_utc=event.kickoff_at_utc,
        )

    def _epa_rows(
        self,
        rows: Sequence[Mapping[str, object]],
        season: int,
        manifest: CaptureManifest,
        schedules: Mapping[str, Mapping[str, object]],
        events: Mapping[str, EventVersion],
    ) -> list[TeamGameEpa]:
        groups: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
        for row in rows:
            game_id = _string(row["game_id"], "game_id")
            schedule = schedules.get(game_id)
            if schedule is None or _integer(schedule["season"], "season", 1) != season:
                continue
            if not self._final_schedule(schedule, manifest):
                continue
            groups[(game_id, _team(row["posteam"], "posteam"), _team(row["defteam"], "defteam"))].append(row)
        values: list[TeamGameEpa] = []
        for (game_id, offense, defense), plays in sorted(groups.items()):
            payload = self._epa_payload(plays)
            values.append(
                TeamGameEpa(
                    canonical_event_id=events[game_id].canonical_event_id,
                    offense_team=offense,
                    defense_team=defense,
                    offense_epa_per_play=cast(float, payload["offense_epa_per_play"]),
                    pass_epa_per_play=cast(float, payload["pass_epa_per_play"]),
                    rush_epa_per_play=cast(float, payload["rush_epa_per_play"]),
                    plays=cast(int, payload["plays"]),
                    finalized_at_utc=events[game_id].kickoff_at_utc,
                )
            )
        return values

    def _ratings(
        self, games: Sequence[CompletedGame], rows: Sequence[TeamGameEpa], cutoff: datetime
    ) -> dict[str, Mapping[str, float]]:
        policy = self.feature_policy
        elo = EloRater(
            policy.elo_initial,
            policy.elo_home_field_points,
            policy.elo_k_factor,
            policy.elo_offseason_retention,
            policy.elo_logistic_scale,
            policy.elo_mov_denominator,
            policy.elo_mov_rating_scale,
        ).snapshot(list(games), cutoff).values
        adjusted = fit_opponent_adjusted_epa(rows, cutoff, "offense_epa_per_play", policy.epa_ridge_alpha)
        pass_adjusted = fit_opponent_adjusted_epa(rows, cutoff, "pass_epa_per_play", policy.epa_ridge_alpha)
        rush_adjusted = fit_opponent_adjusted_epa(rows, cutoff, "rush_epa_per_play", policy.epa_ridge_alpha)
        return {
            "elo": elo,
            "colley": colley_ratings(games, cutoff),
            "massey": massey_ratings(games, policy.massey_home_field_points, cutoff),
            "off_epa": adjusted.offense,
            "def_epa": adjusted.defense,
            "pass_epa": pass_adjusted.offense,
            "rush_epa": rush_adjusted.offense,
        }

    @staticmethod
    def _league_strength(ratings: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
        return {
            key: (sum(values.values()) / len(values) if values else 0.0)
            for key, values in ratings.items()
        }

    @staticmethod
    def _epa_payload(plays: Sequence[Mapping[str, object]]) -> dict[str, float | int]:
        count = len(plays)
        if count == 0:
            raise ValueError("EPA aggregation requires plays")
        passes = [play for play in plays if _integer(play["pass_attempt"], "pass_attempt")]
        rushes = [play for play in plays if _integer(play["rush_attempt"], "rush_attempt")]

        def mean(items: Sequence[Mapping[str, object]]) -> float:
            return sum(_finite(item["epa"], "epa") for item in items) / len(items) if items else 0.0

        return {
            "offense_epa_per_play": mean(plays),
            "pass_epa_per_play": mean(passes),
            "rush_epa_per_play": mean(rushes),
            "plays": count,
        }

    @staticmethod
    def _bool(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise TypeError(f"{field} must be boolean")
        return value

    @staticmethod
    def _final_schedule(row: Mapping[str, object], manifest: CaptureManifest) -> bool:
        home_score = _score(row["home_score"], "home_score")
        away_score = _score(row["away_score"], "away_score")
        if (home_score is None) != (away_score is None):
            raise ValueError("schedule scores must be both present or both absent")
        return home_score is not None and _kickoff(row) <= manifest.response_received_at_utc

    @staticmethod
    def _fact(
        *,
        manifest: CaptureManifest,
        input_manifests: tuple[CaptureManifest, ...],
        fact_type: str,
        entity_keys: dict[str, str],
        payload: dict[str, Any],
        provider_record_id: str,
        raw_pointer: str,
        observed_at: datetime | None,
    ) -> NormalizedFact:
        observed_at = None if observed_at is None else _utc(observed_at)
        input_capture_ids = tuple(item.capture_id for item in input_manifests)
        if len(set(input_capture_ids)) != len(input_capture_ids):
            raise ValueError("fact input capture IDs must be unique")
        if manifest.capture_id not in input_capture_ids:
            raise ValueError("primary fact capture must be an input capture")
        available_at = max(item.response_received_at_utc for item in input_manifests)
        captured_at = available_at
        identity = {
            "fact_type": fact_type,
            "entity_keys": entity_keys,
            "payload": payload,
            "provider_record_id": provider_record_id,
            "observed_at_utc": _json_time(observed_at),
            "available_at_utc": available_at.isoformat(),
            "captured_at_utc": captured_at.isoformat(),
            "capture_id": manifest.capture_id,
            "input_capture_ids": list(input_capture_ids),
            "normalization_schema_version": _FACT_SCHEMA_VERSION,
        }
        content_hash = _digest(identity)
        return NormalizedFact(
            fact_id=content_hash,
            capture_id=manifest.capture_id,
            input_capture_ids=input_capture_ids,
            fact_type=fact_type,
            entity_keys=entity_keys,
            payload=payload,
            provider_record_id=provider_record_id,
            raw_pointer=raw_pointer,
            observed_at_utc=observed_at,
            available_at_utc=available_at,
            captured_at_utc=captured_at,
            provenance_grade=ProvenanceGrade.A,
            normalization_schema_version=_FACT_SCHEMA_VERSION,
            fact_content_sha256=content_hash,
        )


class RequiredFootballCapture:
    DATASETS = ("schedules", "pbp")

    def __init__(
        self,
        *,
        capture_service: CaptureService,
        adapter: NflverseAdapter,
        normalizer: NflverseFootballNormalizer,
        lineage: DurableLineageRepository,
    ) -> None:
        self.capture_service = capture_service
        self.adapter = adapter
        self.normalizer = normalizer
        self.lineage = lineage

    def capture_live(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        manifests = self._capture_datasets(obligation.event.season, attempt_id)
        payloads = {dataset: self._verify_capture(manifest) for dataset, manifest in manifests.items()}
        facts = self.normalizer.normalize_capture(
            payloads["schedules"],
            manifests["schedules"],
            payloads["pbp"],
            manifests["pbp"],
            obligation.event,
        )
        manifest_items = tuple(manifests[dataset] for dataset in self.DATASETS)
        self.lineage.publish_capture_batch(obligation, attempt_id, manifest_items, facts)
        return CaptureBundle((*manifest_items, *facts), {"fact_ids": [fact.fact_id for fact in facts]})

    def capture_replay(
        self, obligation: OriginObligation, attempt_id: str, cutoff: datetime
    ) -> CaptureBundle:
        return self.lineage.replay_capture_batch(obligation, attempt_id, cutoff)

    def _capture_datasets(self, season: int, attempt_id: str) -> dict[str, CaptureManifest]:
        seasons = [season - 1, season]
        return {
            dataset: self.capture_service.capture(
                self.adapter, {"dataset": dataset, "seasons": seasons}, attempt_id
            )
            for dataset in self.DATASETS
        }

    def _verify_capture(self, manifest: CaptureManifest) -> bytes:
        raw_path = self.capture_service.data_root / manifest.raw_path
        manifest_path = self.capture_service.data_root / "manifests" / "captures" / f"{manifest.capture_id}.json"
        try:
            raw = raw_path.read_bytes()
            stored = CaptureManifest.model_validate_json(manifest_path.read_bytes())
        except (OSError, ValueError) as error:
            raise ValueError("captured raw file or manifest is unavailable") from error
        if hashlib.sha256(raw).hexdigest() != manifest.raw_payload_sha256:
            raise ValueError("captured raw file hash does not match manifest")
        if stored != manifest:
            raise ValueError("captured manifest does not match capture receipt")
        return raw


class OptionalOddsCapture:
    def __init__(
        self,
        *,
        policy: OddsPolicy,
        api_key: str | None,
        budget: OddsBudget,
        capture_service: CaptureService,
        adapter_factory: _OddsAdapterFactory,
        active_events: Callable[[], Iterable[EventVersion]],
        month: Callable[[OriginObligation], str],
        lineage: DurableLineageRepository | None = None,
    ) -> None:
        self.policy = policy
        self.api_key = api_key or ""
        self.budget = budget
        self.capture_service = capture_service
        self.adapter_factory = adapter_factory
        self.active_events = active_events
        self.month = month
        self.lineage = lineage

    @property
    def enabled(self) -> bool:
        return self.policy.capture_enabled and bool(self.api_key.strip())

    def capture(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        if not self.enabled:
            raise MarketCaptureDisabled("market capture is disabled")
        month = self.month(obligation)
        request_id = f"{obligation.idempotency_key}:{attempt_id}"
        reservation = self.budget.reserve_required(month, request_id, 1)
        try:
            adapter = self.adapter_factory(self.api_key)
            manifest = self.capture_service.capture(
                adapter,
                {"bookmakers": list(self.policy.book_allowlist), "markets": list(self.policy.markets)},
                attempt_id,
            )
            quota = self._quota_headers(manifest.response_headers_allowlisted)
            normalized = normalize_h2h(
                (self.capture_service.data_root / manifest.raw_path).read_bytes(),
                manifest,
                self.active_events(),
                self.policy,
            )
            self.budget.finalize_success(reservation.reservation_id, month, *quota)
        except Exception:
            self.budget.finalize_uncertain(reservation.reservation_id)
            raise
        if self.lineage is not None:
            self.lineage.append_manifests((manifest,))
            for quote in normalized.quotes:
                self.lineage.ledger.append("moneyline-quote-v1", quote.quote_id, quote)
        return CaptureBundle(
            (manifest, *normalized.quotes),
            {
                "quote_ids": [quote.quote_id for quote in normalized.quotes],
                "rejection_codes": [item.reason_code for item in normalized.rejections],
            },
        )

    @staticmethod
    def _quota_headers(headers: Mapping[str, str]) -> tuple[int, int, int]:
        required = ("x-requests-used", "x-requests-remaining", "x-requests-last")
        try:
            values = tuple(int(headers[name]) for name in required)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("odds quota headers are missing or invalid") from error
        if any(value < 0 for value in values):
            raise ValueError("odds quota headers must be nonnegative")
        return cast(tuple[int, int, int], values)


class ArrowOutcomeAdapter(OutcomeAdapter):
    """Schedule Arrow outcome reader that preserves CaptureService lineage identity."""

    def capture(self, request: dict[str, object], run_id: str) -> OutcomeCapture:
        source_event_id = request.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            raise ValueError("outcome request requires source_event_id")
        manifest = self.capture_service.capture(self.source, request, run_id)
        payload = (self.capture_service.data_root / manifest.raw_path).read_bytes()
        return OutcomeCapture(manifest, self._normalize_arrow(payload, manifest, source_event_id))

    @staticmethod
    def _normalize_arrow(
        payload: bytes, manifest: CaptureManifest, source_event_id: str
    ) -> OutcomeObservation:
        try:
            frame = pl.read_ipc(BytesIO(payload))
        except Exception as error:
            raise ValueError("outcome schedules IPC payload is unreadable") from error
        missing = sorted(_SCHEDULE_COLUMNS - set(frame.columns))
        if missing:
            raise ValueError(f"outcome schedules missing required columns: {', '.join(missing)}")
        rows = cast(
            list[dict[str, object]], frame.select(sorted(_SCHEDULE_COLUMNS)).to_dicts()
        )
        matches = [row for row in rows if row["game_id"] == source_event_id]
        if len(matches) != 1:
            raise ValueError("outcome source_event_id must select exactly one schedule row")
        row = matches[0]
        home_score = _score(row["home_score"], "home_score")
        away_score = _score(row["away_score"], "away_score")
        status = "final" if home_score is not None and away_score is not None else "unresolved"
        source_observed = manifest.source_snapshot_at_utc or manifest.response_received_at_utc
        return OutcomeObservation(
            source=manifest.source,
            source_event_id=source_event_id,
            game_status=cast(OutcomeStatus, status),
            home_score=home_score if status == "final" else None,
            away_score=away_score if status == "final" else None,
            home_team=_team(row["home_team"], "home_team"),
            away_team=_team(row["away_team"], "away_team"),
            finalized_at_utc=source_observed if status == "final" else None,
            source_observed_at_utc=source_observed,
            retrieved_at_utc=manifest.response_received_at_utc,
            observation_time_basis=(
                "source_snapshot" if manifest.source_snapshot_at_utc is not None else "retrieval_fallback"
            ),
            capture_id=manifest.capture_id,
            raw_payload_sha256=manifest.raw_payload_sha256,
            code_sha=manifest.code_sha,
            dependency_lock_sha256=manifest.dependency_lock_sha256,
        )
