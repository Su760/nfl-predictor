from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from nfl_predictor.betting.policy import (
    BookSettlementContract,
    OddsPolicy,
    load_odds_policy,
)
from nfl_predictor.capture.service import CaptureService
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.lineage import CaptureManifest, NormalizedFact
from nfl_predictor.contracts.markets import MoneylineQuote
from nfl_predictor.features.builder import FeatureBuilder
from nfl_predictor.features.policy import load_feature_policy
from nfl_predictor.features.schema import FEATURE_NAMES_V1
from nfl_predictor.features.team_strength import PointInTimeRatingService
from nfl_predictor.features.venue import VenueStore
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.markets.budget import InMemoryBudgetRepository, OddsBudget
from nfl_predictor.runtime.capture import (
    ArrowOutcomeAdapter,
    NflverseFootballNormalizer,
    OptionalOddsCapture,
    RequiredFootballCapture,
)
from nfl_predictor.runtime.lineage import DurableLineageRepository
from nfl_predictor.sources.base import BuildIdentity, RawResponse
from nfl_predictor.sources.nflverse import NflverseAdapter
from nfl_predictor.workflows.forecast import MarketCaptureDisabled

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
BUILD = BuildIdentity("1" * 40, "2" * 64)


def _event() -> EventVersion:
    return EventVersion(
        canonical_event_id="event-2026-02-gb-chi",
        event_version=1,
        source_event_ids={"nflverse": "2026_02_GB_CHI"},
        season=2026,
        season_type="REG",
        week=2,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=datetime(2026, 9, 13, 18, tzinfo=UTC),
        venue_id="soldier-field",
        neutral_site=False,
        observed_at_utc=NOW,
        available_at_utc=NOW,
        captured_at_utc=NOW,
        raw_payload_sha256="a" * 64,
    )


def _obligation() -> OriginObligation:
    event = _event()
    return OriginObligation(event, ForecastOrigin.for_kickoff(Origin.T72, event.kickoff_at_utc), "v1")


def _schedules() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["2025_01_GB_CHI", "2026_01_SEA_SF", "2026_02_GB_CHI"],
            "season": [2025, 2026, 2026],
            "game_type": ["REG", "REG", "REG"],
            "week": [1, 1, 2],
            "gameday": ["2025-09-07", "2026-09-06", "2026-09-13"],
            "gametime": ["18:00", "18:00", "18:00"],
            "away_team": ["GB", "SEA", "GB"],
            "home_team": ["CHI", "SF", "CHI"],
            "away_score": [17, 14, None],
            "home_score": [20, 21, None],
            "location": ["Home", "Home", "Home"],
            "div_game": [True, False, True],
            "stadium_id": ["soldier-field", "levi", "soldier-field"],
        }
    )


def _pbp() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [
                "2025_01_GB_CHI",
                "2025_01_GB_CHI",
                "2026_01_SEA_SF",
                "2026_01_SEA_SF",
            ],
            "play_id": [1, 2, 1, 2],
            "posteam": ["GB", "CHI", "SEA", "SF"],
            "defteam": ["CHI", "GB", "SF", "SEA"],
            "passer_player_id": ["qb-gb", "qb-chi", "qb-sea", "qb-sf"],
            "pass_attempt": [1, 1, 1, 1],
            "rush_attempt": [0, 0, 0, 0],
            "epa": [0.1, 0.2, -0.1, 0.3],
            "qb_epa": [0.1, 0.2, -0.1, 0.3],
            "cpoe": [1.0, 2.0, -1.0, 3.0],
        }
    )


class CaptureFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.data_root = tmp_path / "private-data"
        self.data_root.mkdir()
        self.lineage = DurableLineageRepository(tmp_path / "lineage", self.data_root)
        self.obligation = _obligation()
        self.cutoff = NOW + timedelta(minutes=1)
        self.network_attempts = 0
        clock_values = iter((NOW - timedelta(seconds=2), NOW, NOW - timedelta(seconds=2), NOW))

        def clock() -> datetime:
            return next(clock_values)

        def load_schedules(*, seasons: list[int]) -> pl.DataFrame:
            self.network_attempts += 1
            assert seasons == [2025, 2026]
            return _schedules()

        def load_pbp(*, seasons: list[int]) -> pl.DataFrame:
            self.network_attempts += 1
            assert seasons == [2025, 2026]
            return _pbp()

        self.adapter = NflverseAdapter(
            loaders={"schedules": load_schedules, "pbp": load_pbp}, clock=clock
        )
        self.service = CaptureService(self.data_root, lambda *_: None, BUILD)
        self.required = RequiredFootballCapture(
            capture_service=self.service,
            adapter=self.adapter,
            normalizer=_normalizer(),
            lineage=self.lineage,
        )


@pytest.fixture
def capture_fixture(tmp_path: Path) -> CaptureFixture:
    return CaptureFixture(tmp_path)


def test_nflverse_ipc_normalizes_complete_feature_fact_families(
    capture_fixture: CaptureFixture,
) -> None:
    bundle = capture_fixture.required.capture_live(capture_fixture.obligation, "attempt-1")
    facts = [row for row in bundle.records if isinstance(row, NormalizedFact)]

    assert {fact.fact_type for fact in facts} == {
        "completed_game",
        "division_alignment",
        "league_strength_prior",
        "qb_trailing",
        "team_game_epa",
        "team_passing_prior",
        "team_strength_prior",
    }
    assert {fact.capture_id for fact in facts} <= {
        row.capture_id for row in bundle.records if isinstance(row, CaptureManifest)
    }
    assert all(fact.provenance_grade is ProvenanceGrade.A for fact in facts)
    assert all(
        fact.entity_keys.get("canonical_event_id") == capture_fixture.obligation.event.canonical_event_id
        for fact in facts
        if fact.fact_type in {"league_strength_prior", "division_alignment"}
    )
    assert not any(
        fact.entity_keys.get("canonical_event_id") == capture_fixture.obligation.event.canonical_event_id
        and fact.fact_type in {"completed_game", "team_game_epa"}
        for fact in facts
    )


def test_required_capture_publishes_only_through_batch_boundary(
    capture_fixture: CaptureFixture, tmp_path: Path
) -> None:
    class BoundaryRepository(DurableLineageRepository):
        publishing = False
        published = False

        def publish_capture_batch(
            self,
            obligation: OriginObligation,
            attempt_id: str,
            manifests: Sequence[CaptureManifest],
            facts: Sequence[NormalizedFact],
        ) -> Any:
            self.publishing = True
            try:
                result = super().publish_capture_batch(
                    obligation, attempt_id, manifests, facts
                )
            finally:
                self.publishing = False
            self.published = True
            return result

        def append_manifests(self, manifests: Sequence[CaptureManifest]) -> None:
            if not self.publishing:
                raise AssertionError("manifests published outside capture batch")
            super().append_manifests(manifests)

        def append_facts(self, facts: Sequence[NormalizedFact]) -> None:
            if not self.publishing:
                raise AssertionError("facts published outside capture batch")
            super().append_facts(facts)

    repository = BoundaryRepository(tmp_path / "boundary-lineage", capture_fixture.data_root)
    capture_fixture.required.lineage = repository

    capture_fixture.required.capture_live(capture_fixture.obligation, "attempt-1")

    assert repository.published is True
    assert len(tuple(repository.iter_facts())) == 18


def test_schedule_kickoff_converts_eastern_with_dst_and_uses_canonical_identity() -> None:
    schedules = _schedules().filter(pl.col("game_id") == "2026_02_GB_CHI")
    schedules = pl.concat(
        [
            schedules,
            schedules.with_columns(
                pl.lit("2026_15_SEA_SF").alias("game_id"),
                pl.lit(15, dtype=pl.Int64).alias("week"),
                pl.lit("2026-12-13").alias("gameday"),
                pl.lit("SEA").alias("away_team"),
                pl.lit("SF").alias("home_team"),
                pl.lit("levi").alias("stadium_id"),
            ),
        ]
    )

    september, december = _normalizer().normalize_events(_ipc(schedules), _manifest())

    assert september.kickoff_at_utc == datetime(2026, 9, 13, 22, tzinfo=UTC)
    assert december.kickoff_at_utc == datetime(2026, 12, 13, 23, tzinfo=UTC)
    assert september.canonical_event_id == "2026_REG_02_GB_CHI"
    assert december.canonical_event_id == "2026_REG_15_SEA_SF"
    assert september.source_event_ids == {"nflverse": "2026_02_GB_CHI"}
    assert december.source_event_ids == {"nflverse": "2026_15_SEA_SF"}


@pytest.mark.parametrize(
    ("gameday", "gametime"),
    [("2026-03-08", "02:30"), ("2026-11-01", "01:30")],
)
def test_schedule_kickoff_rejects_nonexistent_or_ambiguous_eastern_wall_time(
    gameday: str, gametime: str
) -> None:
    schedule = _schedules().head(1).with_columns(
        pl.lit(gameday).alias("gameday"), pl.lit(gametime).alias("gametime")
    )

    with pytest.raises(ValueError, match="ambiguous or nonexistent"):
        _normalizer().normalize_events(_ipc(schedule), _manifest())


def test_normalize_capture_emits_exact_fact_shapes_and_capture_lineage() -> None:
    schedule_manifest = _manifest(capture_id="schedule-1")
    pbp_manifest = _manifest(capture_id="pbp-1")

    facts = _normalizer().normalize_capture(
        _ipc(_schedules()), schedule_manifest, _ipc(_pbp()), pbp_manifest, _event()
    )

    by_type = {
        fact_type: [fact for fact in facts if fact.fact_type == fact_type]
        for fact_type in {fact.fact_type for fact in facts}
    }
    assert {name: len(items) for name, items in by_type.items()} == {
        "completed_game": 4,
        "division_alignment": 1,
        "league_strength_prior": 1,
        "qb_trailing": 4,
        "team_game_epa": 4,
        "team_passing_prior": 2,
        "team_strength_prior": 2,
    }
    division = by_type["division_alignment"][0]
    assert division.entity_keys == {
        "canonical_event_id": "event-2026-02-gb-chi",
        "season": "2026",
        "home_team": "CHI",
        "away_team": "GB",
    }
    assert division.payload == {"same_division": True}
    assert {
        fact.entity_keys["canonical_event_id"] for fact in by_type["completed_game"]
    } == {"2025_REG_01_GB_CHI", "2026_REG_01_SEA_SF"}
    assert {
        fact.entity_keys["canonical_event_id"]
        for fact in (*by_type["team_game_epa"], *by_type["qb_trailing"])
    } == {"2025_REG_01_GB_CHI", "2026_REG_01_SEA_SF"}
    assert {
        fact.provider_record_id for fact in by_type["completed_game"]
    } == {"2025_01_GB_CHI", "2026_01_SEA_SF"}
    assert all(
        set(fact.entity_keys) == {"canonical_event_id", "team"}
        and set(fact.payload)
        == {
            "season",
            "home_team",
            "away_team",
            "home_score",
            "away_score",
            "neutral_site",
            "kickoff_at_utc",
        }
        for fact in by_type["completed_game"]
    )
    assert all(
        set(fact.entity_keys)
        == {"canonical_event_id", "team", "offense_team", "defense_team"}
        and set(fact.payload)
        == {"offense_epa_per_play", "pass_epa_per_play", "rush_epa_per_play", "plays"}
        for fact in by_type["team_game_epa"]
    )
    assert {
        fact.provider_record_id: (fact.entity_keys, fact.payload)
        for fact in by_type["league_strength_prior"]
    } == {
        "league:2025": (
            {"canonical_event_id": "event-2026-02-gb-chi", "season": "2026"},
            {
                "elo": 1500.0,
                "colley": 0.5,
                "massey": 0.0,
                "off_epa": 0.0,
                "def_epa": 0.0,
                "pass_epa": 0.0,
                "rush_epa": 0.0,
            },
        )
    }
    assert {
        fact.provider_record_id: (fact.entity_keys, fact.payload)
        for fact in by_type["qb_trailing"]
    } == {
        "2025_01_GB_CHI:CHI:qb-chi": (
            {
                "canonical_event_id": "2025_REG_01_GB_CHI",
                "team": "CHI",
                "player_id": "qb-chi",
            },
            {"attempts": 1, "epa_per_play": 0.2, "cpoe": 2.0},
        ),
        "2025_01_GB_CHI:GB:qb-gb": (
            {
                "canonical_event_id": "2025_REG_01_GB_CHI",
                "team": "GB",
                "player_id": "qb-gb",
            },
            {"attempts": 1, "epa_per_play": 0.1, "cpoe": 1.0},
        ),
        "2026_01_SEA_SF:SEA:qb-sea": (
            {
                "canonical_event_id": "2026_REG_01_SEA_SF",
                "team": "SEA",
                "player_id": "qb-sea",
            },
            {"attempts": 1, "epa_per_play": -0.1, "cpoe": -1.0},
        ),
        "2026_01_SEA_SF:SF:qb-sf": (
            {
                "canonical_event_id": "2026_REG_01_SEA_SF",
                "team": "SF",
                "player_id": "qb-sf",
            },
            {"attempts": 1, "epa_per_play": 0.3, "cpoe": 3.0},
        ),
    }
    assert {
        fact.provider_record_id: (fact.entity_keys, fact.payload)
        for fact in by_type["team_passing_prior"]
    } == {
        "passing:2025:CHI": (
            {"team": "CHI", "season": "2026"},
            {"epa_per_play": 0.2, "cpoe": 2.0},
        ),
        "passing:2025:GB": (
            {"team": "GB", "season": "2026"},
            {"epa_per_play": 0.1, "cpoe": 1.0},
        ),
    }
    assert {
        fact.provider_record_id: (fact.entity_keys, fact.payload)
        for fact in by_type["team_strength_prior"]
    } == {
        "strength:2025:CHI": (
            {"team": "CHI", "season": "2026"},
            {
                "elo": 1513.862943611199,
                "colley": 0.625,
                "massey": 1.5000000000000002,
                "off_epa": 0.004166666666666667,
                "def_epa": 0.004166666666666667,
                "pass_epa": 0.004166666666666667,
                "rush_epa": 0.0,
            },
        ),
        "strength:2025:GB": (
            {"team": "GB", "season": "2026"},
            {
                "elo": 1486.137056388801,
                "colley": 0.375,
                "massey": -1.5000000000000002,
                "off_epa": -0.004166666666666667,
                "def_epa": -0.004166666666666667,
                "pass_epa": -0.004166666666666667,
                "rush_epa": 0.0,
            },
        ),
    }
    assert all(
        fact.lineage_capture_ids == ("schedule-1",)
        for fact in (*by_type["completed_game"], division)
    )
    assert all(
        fact.lineage_capture_ids == ("pbp-1",)
        for fact in (*by_type["team_game_epa"], *by_type["qb_trailing"])
    )
    assert all(
        fact.lineage_capture_ids == ("schedule-1", "pbp-1")
        for fact in (
            *by_type["league_strength_prior"],
            *by_type["team_strength_prior"],
            *by_type["team_passing_prior"],
        )
    )


def test_normalized_capture_publishes_and_builds_exact_v1_snapshot(tmp_path: Path) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    repository = DurableLineageRepository(tmp_path / "lineage", data_root)
    schedule_manifest = _manifest(capture_id="schedule-1", run_id="attempt-1")
    pbp_manifest = _manifest(capture_id="pbp-1", run_id="attempt-1")
    facts = _normalizer().normalize_capture(
        _ipc(_schedules()), schedule_manifest, _ipc(_pbp()), pbp_manifest, _event()
    )
    repository.publish_capture_batch(
        _obligation(), "attempt-1", (schedule_manifest, pbp_manifest), facts
    )
    policy = load_feature_policy("configs/feature_policy_v1.toml")
    snapshot = FeatureBuilder(
        repository,
        policy,
        VenueStore.from_csv("configs/venues_v1.csv"),
        PointInTimeRatingService(policy=policy),
    ).build(_event(), Origin.T72, NOW + timedelta(minutes=1), mode="live")

    assert tuple(snapshot.values) == FEATURE_NAMES_V1
    assert len(snapshot.values) == 42
    assert snapshot.values["division_game"] == 1.0
    assert snapshot.values["venue_altitude_m"] == 181.0
    assert snapshot.values["home_field"] == 1.0
    assert snapshot.values["neutral_site"] == 0.0
    assert snapshot.values["home_games_observed"] == 1.0
    assert snapshot.values["away_games_observed"] == 1.0
    assert snapshot.values["home_qb_epa"] == pytest.approx(0.2)
    assert snapshot.values["away_qb_epa"] == pytest.approx(0.1)
    assert snapshot.input_manifest_ids == ["pbp-1", "schedule-1"]
    selected = repository.resolve_facts(snapshot.input_fact_ids)
    assert snapshot.input_fact_ids == sorted(snapshot.input_fact_ids)
    assert {name: sum(fact.fact_type == name for fact in selected) for name in {
        fact.fact_type for fact in selected
    }} == {
        "completed_game": 2,
        "division_alignment": 1,
        "league_strength_prior": 1,
        "qb_trailing": 2,
        "team_game_epa": 2,
        "team_passing_prior": 2,
        "team_strength_prior": 2,
    }


def test_direct_pbp_normalization_without_explicit_schedules_fails_closed() -> None:
    normalizer = _normalizer()
    with pytest.raises(ValueError, match="^pbp normalization requires paired schedules$"):
        normalizer.normalize_facts("pbp", _ipc(_pbp()), _manifest(), _event())

    facts = normalizer.normalize_facts(
        "pbp",
        _ipc(_pbp()),
        _manifest(capture_id="pbp-1"),
        _event(),
        schedules_payload=_ipc(_schedules()),
    )
    assert {name: sum(fact.fact_type == name for fact in facts) for name in {
        fact.fact_type for fact in facts
    }} == {"qb_trailing": 4, "team_game_epa": 4}


def test_reused_normalizer_cannot_consume_schedules_from_an_earlier_capture() -> None:
    normalizer = _normalizer()
    normalizer.normalize_capture(
        _ipc(_schedules()),
        _manifest(capture_id="schedule-1"),
        _ipc(_pbp()),
        _manifest(capture_id="pbp-1"),
        _event(),
    )
    target_only = _schedules().filter(pl.col("game_id") == "2026_02_GB_CHI")

    with pytest.raises(ValueError, match="schedule context"):
        normalizer.normalize_capture(
            _ipc(target_only),
            _manifest(capture_id="schedule-2"),
            _ipc(_pbp()),
            _manifest(capture_id="pbp-2"),
            _event(),
        )


@pytest.mark.parametrize("mutation", ["missing", "third-party"])
def test_completed_game_requires_exact_home_away_epa_directional_closure(mutation: str) -> None:
    pbp = _pbp()
    if mutation == "missing":
        pbp = pbp.filter(
            ~((pl.col("game_id") == "2025_01_GB_CHI") & (pl.col("posteam") == "CHI"))
        )
    else:
        pbp = pbp.with_columns(
            pl.when(
                (pl.col("game_id") == "2025_01_GB_CHI") & (pl.col("posteam") == "GB")
            )
            .then(pl.lit("ARI"))
            .otherwise(pl.col("defteam"))
            .alias("defteam")
        )

    with pytest.raises(ValueError, match="directional closure"):
        _normalizer().normalize_capture(
            _ipc(_schedules()), _manifest(), _ipc(pbp), _manifest(capture_id="pbp-1"), _event()
        )


def test_identical_capture_normalization_has_deterministic_fact_identity() -> None:
    normalizer = _normalizer()
    args = (
        _ipc(_schedules()),
        _manifest(capture_id="schedule-1"),
        _ipc(_pbp()),
        _manifest(capture_id="pbp-1"),
        _event(),
    )

    first = normalizer.normalize_capture(*args)
    second = normalizer.normalize_capture(*args)

    assert [(fact.fact_id, fact.fact_content_sha256) for fact in first] == [
        (fact.fact_id, fact.fact_content_sha256) for fact in second
    ]
    assert first == second


@pytest.mark.parametrize("metric", ["epa", "qb_epa", "cpoe"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nflverse_normalizer_rejects_nonfinite_pbp_metrics(metric: str, value: float) -> None:
    pbp = _pbp().with_columns(
        pl.when(pl.col("play_id") == 1).then(value).otherwise(pl.col(metric)).alias(metric)
    )

    with pytest.raises(ValueError, match=f"{metric} must be finite"):
        _normalizer().normalize_capture(
            _ipc(_schedules()), _manifest(), _ipc(pbp), _manifest(capture_id="pbp-1"), _event()
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("game_id", " ", "game_id must be non-blank"),
        ("home_team", "XYZ", "unknown NFL team abbreviation"),
        ("home_score", -1, "home_score must be an integer at least 0"),
    ],
)
def test_nflverse_normalizer_rejects_invalid_schedule_values(
    column: str, value: object, message: str
) -> None:
    schedules = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit(value))
        .otherwise(pl.col(column))
        .alias(column)
    )

    with pytest.raises((TypeError, ValueError), match=message):
        _normalizer().normalize_facts("schedules", _ipc(schedules), _manifest(), _event())


def test_nflverse_normalizer_rejects_same_schedule_teams_and_partial_scores() -> None:
    same_team = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit("CHI"))
        .otherwise(pl.col("away_team"))
        .alias("away_team")
    )
    with pytest.raises(ValueError, match="schedule home and away teams must differ"):
        _normalizer().normalize_facts("schedules", _ipc(same_team), _manifest(), _event())

    partial = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.col("away_score"))
        .alias("away_score")
    )
    with pytest.raises(ValueError, match="both present or both absent"):
        _normalizer().normalize_facts("schedules", _ipc(partial), _manifest(), _event())


def test_nflverse_normalizer_rejects_invalid_pbp_ids_teams_and_attempts() -> None:
    mutations = (
        (
            _pbp().with_columns(
                pl.when(
                    (pl.col("game_id") == "2025_01_GB_CHI") & (pl.col("play_id") == 1)
                )
                .then(pl.lit(" "))
                .otherwise(pl.col("game_id"))
                .alias("game_id")
            ),
            "game_id must be non-blank",
        ),
        (
            _pbp().with_columns(
                pl.when(pl.col("game_id") == "2025_01_GB_CHI")
                .then(pl.lit("XYZ"))
                .otherwise(pl.col("posteam"))
                .alias("posteam")
            ),
            "unknown NFL team abbreviation",
        ),
        (
            _pbp().with_columns(
                pl.when(pl.col("game_id") == "2025_01_GB_CHI")
                .then(pl.lit(2))
                .otherwise(pl.col("pass_attempt"))
                .alias("pass_attempt")
            ),
            "attempts must be binary",
        ),
    )
    for pbp, message in mutations:
        with pytest.raises((TypeError, ValueError), match=message):
            _normalizer().normalize_capture(
                _ipc(_schedules()),
                _manifest(),
                _ipc(pbp),
                _manifest(capture_id="pbp-1"),
                _event(),
            )


def test_target_game_pbp_is_excluded() -> None:
    target_pbp = _pbp().head(1).with_columns(
        pl.lit("2026_02_GB_CHI").alias("game_id"),
        pl.lit("GB").alias("posteam"),
        pl.lit("CHI").alias("defteam"),
    )
    facts = _normalizer().normalize_capture(
        _ipc(_schedules()),
        _manifest(),
        _ipc(pl.concat([_pbp(), target_pbp])),
        _manifest(capture_id="pbp-1"),
        _event(),
    )

    assert not any(
        fact.fact_type in {"team_game_epa", "qb_trailing"}
        and fact.provider_record_id is not None
        and fact.provider_record_id.startswith("2026_02_GB_CHI")
        for fact in facts
    )


@pytest.mark.parametrize("dataset", ["schedules", "pbp"])
def test_nflverse_normalizer_rejects_missing_frozen_required_columns(dataset: str) -> None:
    frame = _schedules() if dataset == "schedules" else _pbp()
    payload = _ipc(frame.drop(frame.columns[-1]))

    with pytest.raises(ValueError, match="required columns"):
        _normalizer().normalize_facts(
            dataset,
            payload,
            _manifest(),
            _event(),
            schedules_payload=_ipc(_schedules()) if dataset == "pbp" else None,
        )


def test_nflverse_normalizer_rejects_nonfinal_target_game_score_leakage() -> None:
    schedules = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2026_02_GB_CHI")
        .then(20)
        .otherwise(pl.col("home_score"))
        .alias("home_score")
    )

    with pytest.raises(ValueError, match="target game"):
        _normalizer().normalize_facts("schedules", _ipc(schedules), _manifest(), _event())


def test_nflverse_normalizer_rejects_duplicate_provider_row_ids() -> None:
    duplicate = pl.concat([_pbp(), _pbp().head(1)])

    with pytest.raises(ValueError, match="duplicate provider"):
        _normalizer().normalize_capture(
            _ipc(_schedules()),
            _manifest(),
            _ipc(duplicate),
            _manifest(capture_id="pbp-1"),
            _event(),
        )


def test_nflverse_normalizer_rejects_schedule_timestamp_after_capture_receipt() -> None:
    schedules = _schedules().with_columns(pl.lit("2026-09-11").alias("gameday"))

    with pytest.raises(ValueError, match="after capture receipt"):
        _normalizer().normalize_facts("schedules", _ipc(schedules), _manifest(), _event())


def test_nflverse_normalizer_rejects_non_target_pbp_after_capture_receipt() -> None:
    schedules = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2026_01_SEA_SF")
        .then(pl.lit("2026-09-11"))
        .otherwise(pl.col("gameday"))
        .alias("gameday"),
        pl.when(pl.col("game_id") == "2026_01_SEA_SF")
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.col("home_score"))
        .alias("home_score"),
        pl.when(pl.col("game_id") == "2026_01_SEA_SF")
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.col("away_score"))
        .alias("away_score"),
    )

    with pytest.raises(ValueError, match="after capture receipt"):
        _normalizer().normalize_capture(
            _ipc(schedules),
            _manifest(capture_id="schedule-1"),
            _ipc(_pbp()),
            _manifest(capture_id="pbp-1"),
            _event(),
        )


def test_replay_uses_only_pre_cutoff_archived_capture_batch(capture_fixture: CaptureFixture) -> None:
    capture_fixture.required.capture_live(capture_fixture.obligation, "attempt-1")
    attempts_before = capture_fixture.network_attempts

    bundle = capture_fixture.required.capture_replay(
        capture_fixture.obligation, "replay-1", capture_fixture.cutoff
    )

    assert all(
        record.captured_at_utc <= capture_fixture.cutoff
        for record in bundle.records
        if isinstance(record, NormalizedFact)
    )
    assert capture_fixture.network_attempts == attempts_before
    assert all(
        record.provenance_grade is ProvenanceGrade.C
        for record in bundle.records
        if isinstance(record, NormalizedFact)
    )


class _OddsAdapter:
    source = "the_odds_api"

    def __init__(
        self,
        *,
        payload: bytes = b"[]",
        events: list[str] | None = None,
    ) -> None:
        self.calls = 0
        self.raise_after_send = False
        self.payload = payload
        self.events = events

    def fetch(self, request: dict[str, Any]) -> RawResponse:
        self.calls += 1
        if self.events is not None:
            self.events.append("send")
        if self.raise_after_send:
            raise TimeoutError("sent but no response")
        return RawResponse(
            source=self.source,
            request_fingerprint="public-request",
            request_started_at_utc=NOW - timedelta(seconds=2),
            response_received_at_utc=NOW,
            http_status=200,
            payload=self.payload,
            allowlisted_headers={
                "x-requests-last": "1",
                "x-requests-used": "1",
                "x-requests-remaining": "399",
            },
        )


class _ObservedOddsBudget(OddsBudget):
    def __init__(self) -> None:
        super().__init__(InMemoryBudgetRepository(), hard_stop=400)
        self.events: list[str] = []
        self.successful_finalizations = 0
        self.uncertain_finalizations = 0

    def reserve_required(self, month: str, request_id: str, credits: int) -> Any:
        reservation = super().reserve_required(month, request_id, credits)
        self.events.append("reserve")
        return reservation

    def finalize_success(
        self,
        reservation_id: str,
        month: str,
        used: int,
        remaining: int,
        last: int,
    ) -> None:
        super().finalize_success(reservation_id, month, used, remaining, last)
        self.successful_finalizations += 1
        self.events.append("authoritative")

    def finalize_uncertain(self, reservation_id: str) -> None:
        super().finalize_uncertain(reservation_id)
        self.uncertain_finalizations += 1
        self.events.append("uncertain")


class _OddsFactory:
    def __init__(self, budget: _ObservedOddsBudget) -> None:
        self.budget = budget
        self.raise_after_send = False
        self.adapters: list[_OddsAdapter] = []
        self.reservation_open_at_construction = False

    def __call__(self, _: str) -> _OddsAdapter:
        self.budget.events.append("construct")
        self.reservation_open_at_construction = (
            self.budget.state("2026-09").open_reserved == 1
        )
        adapter = _OddsAdapter(events=self.budget.events)
        adapter.raise_after_send = self.raise_after_send
        self.adapters.append(adapter)
        return adapter


def _odds_policy(*, books: tuple[str, ...] = ("book-a",)) -> OddsPolicy:
    contracts = tuple(
        BookSettlementContract(
            book_key=book,
            market_semantics_version="nfl-h2h-v1",
            settlement_policy_url=f"https://example.test/{book}/rules",
            settlement_policy_version="v1",
            overtime_included=True,
            tie_handling="push",
            verified_on="2026-08-28",
        )
        for book in books
    )
    return load_odds_policy(Path("configs/odds_policy_v1.toml")).model_copy(
        update={
            "capture_enabled": True,
            "book_allowlist": books,
            "book_settlement_contracts": contracts,
            "worst_case_monthly_credits": 1,
        }
    )


def _odds_payload(
    *, include_second_book: bool = False, include_rejection: bool = False
) -> bytes:
    bookmakers: list[dict[str, object]] = [
        {
            "key": "book-a",
            "title": "Book A",
            "last_update": (NOW - timedelta(minutes=1)).isoformat(),
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"id": "home-price", "name": "Chicago Bears", "price": 2.05},
                        {"id": "away-price", "name": "Green Bay Packers", "price": 1.80},
                    ],
                }
            ],
        }
    ]
    if include_second_book:
        second_book = dict(bookmakers[0])
        second_book.update({"key": "book-b", "title": "Book B"})
        bookmakers.append(second_book)
    if include_rejection:
        bookmakers.append(
            {
                "key": "secret-unapproved-book",
                "title": "secret-provider-detail",
                "last_update": NOW.isoformat(),
                "markets": [],
            }
        )
    return json.dumps(
        [
            {
                "id": "provider-1",
                "sport_key": "americanfootball_nfl",
                "commence_time": _event().kickoff_at_utc.isoformat(),
                "home_team": "Chicago Bears",
                "away_team": "Green Bay Packers",
                "bookmakers": bookmakers,
            }
        ]
    ).encode()


@pytest.fixture
def odds_fixture(tmp_path: Path) -> Any:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    lineage = DurableLineageRepository(tmp_path / "lineage", data_root)
    budget = _ObservedOddsBudget()
    adapter_factory = _OddsFactory(budget)
    capture = OptionalOddsCapture(
        policy=_odds_policy(),
        api_key="test-key",
        budget=budget,
        capture_service=CaptureService(data_root, lambda *_: None, BUILD),
        adapter_factory=adapter_factory,
        active_events=lambda: (_event(),),
        month=lambda _: "2026-09",
        lineage=lineage,
    )
    return type(
        "OddsFixture",
        (),
        {
            "adapter_factory": adapter_factory,
            "budget": budget,
            "capture": capture,
            "lineage": lineage,
            "obligation": _obligation(),
        },
    )()


def test_odds_reservation_precedes_adapter_and_timeout_is_finalized_uncertain_once(
    odds_fixture: Any,
) -> None:
    odds_fixture.adapter_factory.raise_after_send = True

    with pytest.raises(TimeoutError):
        odds_fixture.capture.capture(odds_fixture.obligation, "attempt-1")

    assert odds_fixture.budget.state("2026-09").consumed_local == 1
    assert odds_fixture.adapter_factory.reservation_open_at_construction is True
    assert odds_fixture.adapter_factory.adapters[0].calls == 1
    assert odds_fixture.budget.events == ["reserve", "construct", "send", "uncertain"]
    assert odds_fixture.budget.successful_finalizations == 0
    assert odds_fixture.budget.uncertain_finalizations == 1


@pytest.mark.parametrize(
    ("policy", "api_key"),
    [(OddsPolicy(), "test-key"), (_odds_policy(), "   ")],
)
def test_disabled_or_blank_key_odds_capture_does_not_construct_an_http_client(
    tmp_path: Path, policy: OddsPolicy, api_key: str
) -> None:
    constructed = False
    data_root = tmp_path / "private-data"
    data_root.mkdir()

    def factory(_: str) -> _OddsAdapter:
        nonlocal constructed
        constructed = True
        return _OddsAdapter()

    capture = OptionalOddsCapture(
        policy=policy,
        api_key=api_key,
        budget=OddsBudget(InMemoryBudgetRepository(), 400),
        capture_service=CaptureService(data_root, lambda *_: None, BUILD),
        adapter_factory=factory,
        active_events=lambda: (_event(),),
        month=lambda _: "2026-09",
        lineage=DurableLineageRepository(tmp_path / "lineage", data_root),
    )

    assert capture.enabled is False
    with pytest.raises(MarketCaptureDisabled):
        capture.capture(_obligation(), "attempt-1")
    assert constructed is False


def test_odds_capture_requires_a_real_durable_lineage_repository(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="DurableLineageRepository"):
        OptionalOddsCapture(
            policy=_odds_policy(),
            api_key="test-key",
            budget=OddsBudget(InMemoryBudgetRepository(), 400),
            capture_service=CaptureService(tmp_path, lambda *_: None, BUILD),
            adapter_factory=lambda _: _OddsAdapter(),
            active_events=lambda: (_event(),),
            month=lambda _: "2026-09",
            lineage=None,
        )


def test_odds_known_quota_is_authoritative_before_normalization_failure(tmp_path: Path) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    budget = _ObservedOddsBudget()
    capture = OptionalOddsCapture(
        policy=_odds_policy(),
        api_key="test-key",
        budget=budget,
        capture_service=CaptureService(data_root, lambda *_: None, BUILD),
        adapter_factory=lambda _: _OddsAdapter(payload=b"not-json", events=budget.events),
        active_events=lambda: (_event(),),
        month=lambda _: "2026-09",
        lineage=DurableLineageRepository(tmp_path / "lineage", data_root),
    )

    with pytest.raises(ValueError, match="odds payload is not valid JSON"):
        capture.capture(_obligation(), "attempt-1")

    state = budget.state("2026-09")
    assert state.authoritative_used == 1
    assert state.authoritative_remaining == 399
    assert budget.successful_finalizations == 1
    assert budget.uncertain_finalizations == 0
    assert budget.events[-1] == "authoritative"


def test_odds_persistence_failure_cannot_overwrite_authoritative_finalization(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()

    class FailingLineage(DurableLineageRepository):
        def append_manifests(self, manifests: Sequence[CaptureManifest]) -> None:
            raise RuntimeError("injected lineage failure")

    budget = _ObservedOddsBudget()
    capture = OptionalOddsCapture(
        policy=_odds_policy(),
        api_key="test-key",
        budget=budget,
        capture_service=CaptureService(data_root, lambda *_: None, BUILD),
        adapter_factory=lambda _: _OddsAdapter(payload=_odds_payload(), events=budget.events),
        active_events=lambda: (_event(),),
        month=lambda _: "2026-09",
        lineage=FailingLineage(tmp_path / "lineage", data_root),
    )

    with pytest.raises(RuntimeError, match="injected lineage failure"):
        capture.capture(_obligation(), "attempt-1")

    assert budget.successful_finalizations == 1
    assert budget.uncertain_finalizations == 0
    assert budget.state("2026-09").authoritative_remaining == 399


def test_odds_success_persists_resolvable_manifest_and_every_quote_before_return(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    lineage = DurableLineageRepository(tmp_path / "lineage", data_root)
    budget = _ObservedOddsBudget()
    capture = OptionalOddsCapture(
        policy=_odds_policy(books=("book-a", "book-b")),
        api_key="test-key",
        budget=budget,
        capture_service=CaptureService(data_root, lambda *_: None, BUILD),
        adapter_factory=lambda _: _OddsAdapter(
            payload=_odds_payload(include_second_book=True, include_rejection=True),
            events=budget.events,
        ),
        active_events=lambda: (_event(),),
        month=lambda _: "2026-09",
        lineage=lineage,
    )

    bundle = capture.capture(_obligation(), "attempt-1")

    manifest = next(row for row in bundle.records if isinstance(row, CaptureManifest))
    quotes = tuple(row for row in bundle.records if isinstance(row, MoneylineQuote))
    assert lineage.resolve_manifests([manifest.capture_id]) == [manifest]
    assert len(quotes) == 2
    assert tuple(
        MoneylineQuote.model_validate(lineage.ledger.read("moneyline-quote-v1", quote.quote_id))
        for quote in quotes
    ) == quotes
    assert budget.successful_finalizations == 1
    assert budget.uncertain_finalizations == 0
    assert manifest.response_headers_allowlisted["x-requests-used"] == "1"
    assert bundle.payload == {
        "quote_ids": [quote.quote_id for quote in quotes],
        "rejection_codes": ["BOOK_NOT_ALLOWLISTED"],
    }
    serialized_payload = json.dumps(bundle.payload).encode()
    assert b"test-key" not in serialized_payload
    assert b"secret-provider-detail" not in serialized_payload


def test_arrow_outcome_adapter_selects_exact_source_event(tmp_path: Path) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    payload = _ipc(_schedules())

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse(
                source=self.source,
                request_fingerprint="outcome-request",
                request_started_at_utc=NOW - timedelta(seconds=2),
                response_received_at_utc=NOW,
                http_status=200,
                payload=payload,
                allowlisted_headers={},
            )

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, lambda *_: None, BUILD), Source())
    captured = adapter.capture(
        {"dataset": "schedules", "seasons": [2026], "source_event_id": "2025_01_GB_CHI"},
        "outcome-run-1",
    )

    assert captured.observation.game_status == "final"
    assert captured.observation.home_score == 20
    assert captured.observation.away_score == 17
    assert captured.observation.raw_payload_sha256 == captured.manifest.raw_payload_sha256
    assert captured.observation.home_team == "CHI"
    assert captured.observation.away_team == "GB"
    assert captured.observation.finalized_at_utc == NOW
    assert captured.observation.source_observed_at_utc == NOW
    assert captured.observation.observation_time_basis == "retrieval_fallback"


@pytest.mark.parametrize("matching_rows", [0, 2])
def test_arrow_outcome_adapter_rejects_zero_or_multiple_source_event_matches(
    tmp_path: Path, matching_rows: int
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    schedules = _schedules().filter(pl.col("game_id") != "2025_01_GB_CHI")
    if matching_rows:
        match = _schedules().filter(pl.col("game_id") == "2025_01_GB_CHI")
        schedules = pl.concat([schedules, *([match] * matching_rows)])
    payload = _ipc(schedules)

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse("nflverse", "request", NOW, NOW, 200, payload, {})

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, lambda *_: None, BUILD), Source())

    with pytest.raises(ValueError, match="exactly one"):
        adapter.capture({"source_event_id": "2025_01_GB_CHI"}, "outcome-run-1")


def test_arrow_outcome_adapter_verifies_stored_raw_hash_before_parsing(tmp_path: Path) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()

    def tamper_after_storage(_: bytes, manifest: CaptureManifest) -> None:
        (data_root / manifest.raw_path).write_bytes(b"not-arrow-and-not-the-manifest-hash")

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse("nflverse", "request", NOW, NOW, 200, _ipc(_schedules()), {})

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, tamper_after_storage, BUILD), Source())

    with pytest.raises(ValueError, match="raw file hash does not match manifest"):
        adapter.capture({"source_event_id": "2025_01_GB_CHI"}, "outcome-run-1")


@pytest.mark.parametrize("schema_change", ["missing", "unexpected"])
def test_arrow_outcome_adapter_requires_exact_frozen_schedule_fields(
    tmp_path: Path, schema_change: str
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    schedules = _schedules()
    if schema_change == "missing":
        schedules = schedules.drop("stadium_id")
    else:
        schedules = schedules.with_columns(pl.lit("postponed").alias("game_status"))

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse("nflverse", "request", NOW, NOW, 200, _ipc(schedules), {})

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, lambda *_: None, BUILD), Source())

    with pytest.raises(ValueError, match="exact frozen schedule fields"):
        adapter.capture({"source_event_id": "2025_01_GB_CHI"}, "outcome-run-1")


def test_arrow_outcome_adapter_maps_absent_scores_to_unresolved_without_guessing(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse("nflverse", "request", NOW, NOW, 200, _ipc(_schedules()), {})

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, lambda *_: None, BUILD), Source())
    captured = adapter.capture(
        {"source_event_id": "2026_02_GB_CHI"}, "outcome-run-unresolved"
    )

    assert captured.observation.game_status == "unresolved"
    assert captured.observation.home_score is None
    assert captured.observation.away_score is None
    assert captured.observation.finalized_at_utc is None
    assert captured.observation.source_observed_at_utc == NOW
    assert captured.observation.observation_time_basis == "retrieval_fallback"


@pytest.mark.parametrize(
    ("home_score", "away_score"), [(20, None), (None, 17)]
)
def test_arrow_outcome_adapter_rejects_partial_scores(
    tmp_path: Path, home_score: int | None, away_score: int | None
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    schedules = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit(home_score, dtype=pl.Int64))
        .otherwise(pl.col("home_score"))
        .alias("home_score"),
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit(away_score, dtype=pl.Int64))
        .otherwise(pl.col("away_score"))
        .alias("away_score"),
    )

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse("nflverse", "request", NOW, NOW, 200, _ipc(schedules), {})

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, lambda *_: None, BUILD), Source())

    with pytest.raises(ValueError, match="both present or both absent"):
        adapter.capture({"source_event_id": "2025_01_GB_CHI"}, "outcome-run-1")


@pytest.mark.parametrize(("home_team", "away_team"), [("XYZ", "GB"), ("GB", "GB")])
def test_arrow_outcome_adapter_rejects_invalid_or_identical_teams(
    tmp_path: Path, home_team: str, away_team: str
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    schedules = _schedules().with_columns(
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit(home_team))
        .otherwise(pl.col("home_team"))
        .alias("home_team"),
        pl.when(pl.col("game_id") == "2025_01_GB_CHI")
        .then(pl.lit(away_team))
        .otherwise(pl.col("away_team"))
        .alias("away_team"),
    )

    class Source:
        source = "nflverse"

        def fetch(self, request: dict[str, Any]) -> RawResponse:
            return RawResponse("nflverse", "request", NOW, NOW, 200, _ipc(schedules), {})

    adapter = ArrowOutcomeAdapter(CaptureService(data_root, lambda *_: None, BUILD), Source())

    with pytest.raises(ValueError, match="unknown NFL team|home and away teams must differ"):
        adapter.capture({"source_event_id": "2025_01_GB_CHI"}, "outcome-run-1")


def _ipc(frame: pl.DataFrame) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    frame.write_ipc(buffer)
    return buffer.getvalue()


def _manifest(*, capture_id: str = "capture-1", run_id: str = "run-1") -> CaptureManifest:
    return CaptureManifest(
        capture_id=capture_id,
        run_id=run_id,
        source="nflverse",
        request_fingerprint="request-1",
        request_started_at_utc=NOW - timedelta(seconds=2),
        response_received_at_utc=NOW,
        http_status=200,
        raw_path="raw/nflverse.bin",
        raw_payload_sha256=hashlib.sha256(b"payload").hexdigest(),
        response_headers_allowlisted={},
        code_sha="1" * 40,
        dependency_lock_sha256="2" * 64,
        schema_version="capture-v1",
    )


def _normalizer() -> NflverseFootballNormalizer:
    return NflverseFootballNormalizer(load_feature_policy("configs/feature_policy_v1.toml"))
