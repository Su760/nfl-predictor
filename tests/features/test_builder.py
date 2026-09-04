from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.lineage import NormalizedFact
from nfl_predictor.features.builder import FeatureBuilder
from nfl_predictor.features.policy import FeaturePolicy
from nfl_predictor.features.schema import FEATURE_NAMES_V1, FEATURE_SCHEMA_V1
from nfl_predictor.features.team_strength import (
    PointInTimeRatingService,
    regressed_prior,
    shrunk_ewma,
)
from nfl_predictor.features.venue import VenueStore

CUTOFF = datetime(2026, 9, 13, 17, tzinfo=UTC)
KICKOFF = CUTOFF + timedelta(hours=72)
HASH = "0" * 64


def _event(*, venue_id: str = "test-stadium", neutral_site: bool = False) -> EventVersion:
    return EventVersion(
        canonical_event_id="2026-NE-at-SEA",
        event_version=2,
        source_event_ids={"nfl": "401"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="SEA",
        away_team="NE",
        kickoff_at_utc=KICKOFF,
        venue_id=venue_id,
        neutral_site=neutral_site,
        observed_at_utc=CUTOFF - timedelta(days=30),
        available_at_utc=CUTOFF - timedelta(days=30),
        captured_at_utc=CUTOFF - timedelta(days=30),
        raw_payload_sha256=HASH,
    )


def _fact(
    fact_id: str,
    fact_type: str,
    *,
    entity_keys: dict[str, str],
    payload: dict[str, object],
    observed_at: datetime | None = None,
    available_at: datetime = CUTOFF - timedelta(days=1),
    captured_at: datetime = CUTOFF - timedelta(days=1),
    archive_published_at: datetime | None = None,
    grade: ProvenanceGrade = ProvenanceGrade.A,
) -> NormalizedFact:
    return NormalizedFact(
        fact_id=fact_id,
        capture_id=f"capture-{fact_id}",
        fact_type=fact_type,
        entity_keys=entity_keys,
        payload=payload,
        raw_pointer=f"raw/{fact_id}.json",
        observed_at_utc=observed_at,
        available_at_utc=available_at,
        captured_at_utc=captured_at,
        archive_published_at_utc=archive_published_at,
        provenance_grade=grade,
        normalization_schema_version="fact-v1",
        fact_content_sha256=HASH,
    )


class CutoffFactStore:
    def __init__(self, records: list[NormalizedFact]) -> None:
        self.records = records

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        del event
        return [
            fact
            for fact in self.records
            if fact.available_at_utc <= cutoff
            and (fact.provenance_grade is not ProvenanceGrade.A or fact.captured_at_utc <= cutoff)
            and (
                fact.provenance_grade is not ProvenanceGrade.B
                or (
                    fact.archive_published_at_utc is not None
                    and fact.archive_published_at_utc <= cutoff
                )
            )
        ]


class ReturningFactStore:
    def __init__(self, records: list[NormalizedFact]) -> None:
        self.records = records

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        del event, cutoff
        return list(self.records)


def _rating_service() -> PointInTimeRatingService:
    return PointInTimeRatingService(policy=_policy())


def _policy(*, elo_k_factor: float = 20.0) -> FeaturePolicy:
    return FeaturePolicy(
        policy_version="feature-v1",
        schema_version="feature-schema-v1",
        ewma_halflife_games=4.0,
        prior_effective_games=4.0,
        offseason_regression_to_mean=1.0 / 3.0,
        epa_ridge_alpha=10.0,
        minimum_qb_attempts=50,
        short_week_max_rest_days=6,
        offseason_rest_cap_days=14,
        elo_initial=1500.0,
        elo_home_field_points=0.0,
        elo_k_factor=elo_k_factor,
        elo_offseason_retention=1.0,
        elo_logistic_scale=400.0,
        elo_mov_denominator=2.2,
        elo_mov_rating_scale=0.001,
        massey_home_field_points=0.0,
    )


def _write_venue_csv(path: Path, *, altitude_m: float = 20.0) -> None:
    path.write_text(
        "venue_id,effective_from_utc,effective_to_utc,available_at_utc,altitude_m,"
        "surface_turf,roof_capable,source_url\n"
        f"test-stadium,2026-01-01T00:00:00Z,,2026-01-01T00:00:00Z,{altitude_m},"
        "1,1,https://www.nfl.com/\n",
        encoding="utf-8",
    )


def _base_facts() -> list[NormalizedFact]:
    return [
        _fact(
            "league-strength-2026",
            "league_strength_prior",
            entity_keys={"season": "2026"},
            payload={
                "elo": 1500.0,
                "colley": 0.5,
                "massey": 0.0,
                "off_epa": 0.0,
                "def_epa": 0.0,
                "pass_epa": 0.0,
                "rush_epa": 0.0,
            },
        ),
        _fact(
            "sea-prior",
            "team_passing_prior",
            entity_keys={"team": "SEA"},
            payload={"epa_per_play": 0.10, "cpoe": 1.5},
        ),
        _fact(
            "ne-prior",
            "team_passing_prior",
            entity_keys={"team": "NE"},
            payload={"epa_per_play": -0.04, "cpoe": -0.5},
        ),
        _fact(
            "division",
            "division_alignment",
            entity_keys={"season": "2026", "home_team": "SEA", "away_team": "NE"},
            payload={"same_division": False},
        ),
    ]


def _team_strength_prior(
    fact_id: str, team: str, *, elo: float, colley: float, massey: float, sign: float
) -> NormalizedFact:
    return _fact(
        fact_id,
        "team_strength_prior",
        entity_keys={"season": "2026", "team": team},
        payload={
            "elo": elo,
            "colley": colley,
            "massey": massey,
            "off_epa": 0.4 * sign,
            "def_epa": -0.4 * sign,
            "pass_epa": 0.8 * sign,
            "rush_epa": 0.2 * sign,
        },
    )


def _completed_game_fact(fact_id: str, *, team: str, home_score: int = 1) -> NormalizedFact:
    return _fact(
        fact_id,
        "completed_game",
        entity_keys={"team": team, "canonical_event_id": "2026-NE-at-SEA-prior"},
        payload={
            "season": 2026,
            "home_team": "SEA",
            "away_team": "NE",
            "home_score": home_score,
            "away_score": 0,
            "neutral_site": True,
            "kickoff_at_utc": (CUTOFF - timedelta(days=7)).isoformat(),
        },
        observed_at=CUTOFF - timedelta(days=7) + timedelta(hours=4),
    )


def _team_game_epa_fact(
    fact_id: str,
    *,
    offense_team: str,
    defense_team: str,
    multiplier: float,
) -> NormalizedFact:
    return _fact(
        fact_id,
        "team_game_epa",
        entity_keys={
            "canonical_event_id": "2026-NE-at-SEA-prior",
            "offense_team": offense_team,
            "defense_team": defense_team,
        },
        payload={
            "offense_epa_per_play": 3.0 * multiplier,
            "pass_epa_per_play": 6.0 * multiplier,
            "rush_epa_per_play": 9.0 * multiplier,
            "plays": 1,
        },
        observed_at=CUTOFF - timedelta(days=7) + timedelta(hours=4),
    )


@dataclass
class FeatureFixture:
    builder: FeatureBuilder
    store: CutoffFactStore
    event: EventVersion
    origin: ForecastOrigin
    decision_at: datetime
    venue_path: Path


@pytest.fixture
def feature_fixture(tmp_path: Path) -> FeatureFixture:
    venue_path = tmp_path / "venues.csv"
    _write_venue_csv(venue_path)
    store = CutoffFactStore(_base_facts())
    return FeatureFixture(
        builder=FeatureBuilder(
            store, _policy(), VenueStore.from_csv(venue_path), _rating_service()
        ),
        store=store,
        event=_event(),
        origin=ForecastOrigin.for_kickoff(Origin.T72, KICKOFF),
        decision_at=CUTOFF,
        venue_path=venue_path,
    )


def test_week_one_uses_versioned_priors_not_global_medians(
    feature_fixture: FeatureFixture,
) -> None:
    snapshot = feature_fixture.builder.build(
        feature_fixture.event,
        feature_fixture.origin,
        feature_fixture.decision_at,
        mode="replay",
    )

    assert snapshot.values["home_qb_epa"] == pytest.approx(0.10)
    assert snapshot.values["away_qb_cpoe"] == pytest.approx(-0.5)
    assert snapshot.values["home_prior_weight"] == 1.0
    assert snapshot.values["home_games_observed"] == 0
    assert "GLOBAL_MEDIAN_FILL" not in snapshot.reason_codes


def test_explicit_current_capture_facts_bypass_global_store_history(
    feature_fixture: FeatureFixture,
) -> None:
    current_facts = [
        NormalizedFact.model_validate(
            {
                **fact.model_dump(),
                "fact_id": f"current-{fact.fact_id}",
                "capture_id": "current-capture",
                "provenance_grade": ProvenanceGrade.C,
                "archive_published_at_utc": CUTOFF - timedelta(seconds=1),
            }
        )
        for fact in _base_facts()
    ]

    snapshot = feature_fixture.builder.build(
        feature_fixture.event,
        feature_fixture.origin,
        CUTOFF,
        mode="replay",
        facts=current_facts,
    )

    assert snapshot.input_fact_ids == sorted(fact.fact_id for fact in current_facts)
    assert snapshot.input_manifest_ids == ["current-capture"]
    assert not set(snapshot.input_fact_ids) & {
        fact.fact_id for fact in feature_fixture.store.records
    }


def test_low_attempt_qb_history_uses_team_prior(feature_fixture: FeatureFixture) -> None:
    feature_fixture.store.records.append(
        _fact(
            "sea-qb-small-sample",
            "qb_trailing",
            entity_keys={"team": "SEA"},
            payload={"attempts": 49, "epa_per_play": 9.0, "cpoe": 99.0},
            observed_at=CUTOFF - timedelta(days=2),
        )
    )

    snapshot = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="live"
    )

    assert snapshot.values["home_qb_epa"] == pytest.approx(0.10)
    assert snapshot.values["home_qb_cpoe"] == pytest.approx(1.5)


def test_qb_history_at_attempt_threshold_uses_attempt_weighted_ewma(
    feature_fixture: FeatureFixture,
) -> None:
    feature_fixture.store.records.extend(
        [
            _fact(
                "sea-qb-older",
                "qb_trailing",
                entity_keys={"team": "SEA"},
                payload={"attempts": 25, "epa_per_play": 0.0, "cpoe": 0.0},
                observed_at=CUTOFF - timedelta(days=8),
            ),
            _fact(
                "sea-qb-newer",
                "qb_trailing",
                entity_keys={"team": "SEA"},
                payload={"attempts": 25, "epa_per_play": 1.0, "cpoe": 10.0},
                observed_at=CUTOFF - timedelta(days=1),
            ),
        ]
    )

    snapshot = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
    )

    assert snapshot.values["home_qb_epa"] == pytest.approx(0.5432136168629449)
    assert snapshot.values["home_qb_cpoe"] == pytest.approx(5.432136168629449)


def test_versioned_shrinkage_and_offseason_prior_formulas() -> None:
    estimate, prior_weight = shrunk_ewma(
        [0.0, 1.0], prior=0.5, halflife_games=1.0, prior_effective_games=2.0
    )

    assert estimate == pytest.approx(4.0 / 7.0)
    assert prior_weight == pytest.approx(4.0 / 7.0)
    assert regressed_prior(0.8, league_mean=0.2, fraction=0.25) == pytest.approx(0.65)
    assert regressed_prior(None, league_mean=0.2, fraction=0.25) == pytest.approx(0.2)


def test_point_in_time_rating_service_uses_task7_engines_and_returning_team_priors() -> None:
    facts = [
        _base_facts()[0],
        _team_strength_prior("sea-strength", "SEA", elo=1600.0, colley=0.8, massey=3.0, sign=1),
        _team_strength_prior("ne-strength", "NE", elo=1400.0, colley=0.2, massey=-3.0, sign=-1),
        _completed_game_fact("game-home-copy", team="SEA"),
        _completed_game_fact("game-away-copy", team="NE"),
        _team_game_epa_fact("epa-sea", offense_team="SEA", defense_team="NE", multiplier=1.0),
        _team_game_epa_fact("epa-ne", offense_team="NE", defense_team="SEA", multiplier=-1.0),
    ]

    values = _rating_service().features(_event(), CUTOFF, facts)

    assert values["home_elo"] == pytest.approx(1554.7196276944533)
    assert values["away_elo"] == pytest.approx(1445.280372305547)
    assert values["home_colley"] == pytest.approx(0.685)
    assert values["away_colley"] == pytest.approx(0.315)
    assert values["home_massey"] == pytest.approx(1.7)
    assert values["away_massey"] == pytest.approx(-1.7)
    assert values["home_off_epa"] == pytest.approx(0.2633333333333333)
    assert values["away_off_epa"] == pytest.approx(-0.2633333333333333)
    assert values["home_def_epa"] == pytest.approx(-0.2633333333333333)
    assert values["away_def_epa"] == pytest.approx(0.2633333333333333)
    assert values["home_pass_epa"] == pytest.approx(0.5266666666666666)
    assert values["away_pass_epa"] == pytest.approx(-0.5266666666666666)


def test_rating_controls_are_versioned_policy_inputs() -> None:
    facts = [
        _base_facts()[0],
        _team_strength_prior("sea-strength", "SEA", elo=1600.0, colley=0.8, massey=3.0, sign=1),
        _team_strength_prior("ne-strength", "NE", elo=1400.0, colley=0.2, massey=-3.0, sign=-1),
        _completed_game_fact("game-home-copy", team="SEA"),
        _completed_game_fact("game-away-copy", team="NE"),
    ]
    baseline_policy = _policy()
    high_k_policy = _policy(elo_k_factor=40.0)

    baseline = PointInTimeRatingService(policy=baseline_policy).features(_event(), CUTOFF, facts)
    high_k = PointInTimeRatingService(policy=high_k_policy).features(_event(), CUTOFF, facts)

    assert baseline_policy.elo_k_factor == 20.0
    assert high_k_policy.elo_k_factor == 40.0
    assert high_k["home_elo"] > baseline["home_elo"]
    assert high_k["away_elo"] < baseline["away_elo"]


def test_point_in_time_rating_service_uses_league_prior_for_expansion_week_one() -> None:
    league_prior = _base_facts()[0].model_copy(
        update={
            "payload": {
                "elo": 1510.0,
                "colley": 0.51,
                "massey": 1.0,
                "off_epa": 0.1,
                "def_epa": -0.1,
                "pass_epa": 0.2,
                "rush_epa": 0.05,
            }
        }
    )

    values = _rating_service().features(_event(), CUTOFF, [league_prior])

    assert values["home_elo"] == 1510.0
    assert values["away_elo"] == 1510.0
    assert values["home_colley"] == 0.51
    assert values["away_massey"] == 1.0
    assert values["home_off_epa"] == 0.1
    assert values["away_pass_epa"] == 0.2


def test_point_in_time_rating_service_rejects_missing_or_duplicate_required_priors() -> None:
    service = _rating_service()

    with pytest.raises(ValueError, match="exactly one league_strength_prior"):
        service.features(_event(), CUTOFF, [])

    duplicate_team = _team_strength_prior(
        "sea-strength-duplicate", "SEA", elo=1600.0, colley=0.8, massey=3.0, sign=1
    )
    with pytest.raises(ValueError, match="at most one team_strength_prior"):
        service.features(
            _event(),
            CUTOFF,
            [
                _base_facts()[0],
                _team_strength_prior(
                    "sea-strength", "SEA", elo=1600.0, colley=0.8, massey=3.0, sign=1
                ),
                duplicate_team,
            ],
        )


def test_point_in_time_rating_service_rejects_malformed_prior_payload() -> None:
    malformed = _base_facts()[0].model_copy(update={"payload": {"elo": 1500.0, "colley": 0.5}})

    with pytest.raises(ValueError, match="strength prior payload"):
        _rating_service().features(_event(), CUTOFF, [malformed])


def test_point_in_time_rating_service_rejects_conflicting_completed_game_duplicates() -> None:
    facts = [
        _base_facts()[0],
        _completed_game_fact("game-home-copy", team="SEA", home_score=1),
        _completed_game_fact("game-away-copy", team="NE", home_score=2),
    ]

    with pytest.raises(ValueError, match="conflicting completed_game"):
        _rating_service().features(_event(), CUTOFF, facts)


def test_point_in_time_rating_service_ignores_exact_and_post_cutoff_rating_rows() -> None:
    exact_game = _completed_game_fact("exact-game", team="SEA").model_copy(
        update={"observed_at_utc": CUTOFF}
    )
    future_epa = _team_game_epa_fact(
        "future-epa", offense_team="SEA", defense_team="NE", multiplier=100.0
    ).model_copy(update={"observed_at_utc": CUTOFF + timedelta(seconds=1)})

    values = _rating_service().features(
        _event(), CUTOFF, [_base_facts()[0], exact_game, future_epa]
    )

    assert values["home_elo"] == 1500.0
    assert values["away_elo"] == 1500.0
    assert values["home_off_epa"] == 0.0
    assert values["away_off_epa"] == 0.0


def test_same_sunday_game_order_uses_only_games_finalized_strictly_before_cutoff(
    feature_fixture: FeatureFixture,
) -> None:
    prior_kickoff = CUTOFF - timedelta(hours=4)
    feature_fixture.store.records.extend(
        [
            _fact(
                "sea-finished-before",
                "completed_game",
                entity_keys={"team": "SEA", "canonical_event_id": "earlier"},
                payload={
                    "season": 2026,
                    "home_team": "SEA",
                    "away_team": "ARI",
                    "home_score": 24,
                    "away_score": 17,
                    "neutral_site": False,
                    "kickoff_at_utc": prior_kickoff.isoformat(),
                },
                observed_at=CUTOFF - timedelta(seconds=1),
            ),
            _fact(
                "sea-finalized-at-cutoff",
                "completed_game",
                entity_keys={"team": "SEA", "canonical_event_id": "at-cutoff"},
                payload={
                    "season": 2026,
                    "home_team": "SEA",
                    "away_team": "LAR",
                    "home_score": 20,
                    "away_score": 17,
                    "neutral_site": False,
                    "kickoff_at_utc": (CUTOFF - timedelta(hours=2)).isoformat(),
                },
                observed_at=CUTOFF,
            ),
        ]
    )

    snapshot = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
    )

    assert snapshot.values["home_games_observed"] == 1.0
    assert "sea-finished-before" in snapshot.input_fact_ids
    assert "sea-finalized-at-cutoff" not in snapshot.input_fact_ids


def test_future_game_and_source_mutation_cannot_change_snapshot(
    feature_fixture: FeatureFixture,
) -> None:
    future = _fact(
        "future-game",
        "completed_game",
        entity_keys={"team": "SEA", "canonical_event_id": "future"},
        payload={"kickoff_at_utc": (KICKOFF + timedelta(days=7)).isoformat(), "home_score": 1},
        observed_at=KICKOFF + timedelta(days=7, hours=4),
        available_at=KICKOFF + timedelta(days=7, hours=4),
        captured_at=KICKOFF + timedelta(days=7, hours=4),
    )
    feature_fixture.store.records.append(future)
    before = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
    )
    feature_fixture.store.records[-1] = future.model_copy(
        update={
            "fact_id": "changed-future-game",
            "capture_id": "changed-future-capture",
            "provider_record_id": "changed-provider-record",
            "entity_keys": {"team": "DAL", "canonical_event_id": "changed-future"},
            "payload": {
                "season": 2027,
                "home_team": "DAL",
                "away_team": "MIA",
                "kickoff_at_utc": (KICKOFF + timedelta(days=8)).isoformat(),
                "home_score": 99,
                "away_score": 0,
                "neutral_site": True,
            },
            "raw_pointer": "raw/completely-changed.json",
            "fact_content_sha256": "f" * 64,
            "normalization_schema_version": "future-v99",
        }
    )

    after = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="live"
    )

    assert after.input_fact_ids == before.input_fact_ids
    assert after.values == before.values
    assert after.feature_vector_sha256 == before.feature_vector_sha256
    assert after.snapshot_id == before.snapshot_id


def test_neutral_site_zeroes_home_field(feature_fixture: FeatureFixture) -> None:
    snapshot = feature_fixture.builder.build(
        _event(neutral_site=True), feature_fixture.origin, CUTOFF, mode="live"
    )

    assert snapshot.values["home_field"] == 0.0
    assert snapshot.values["neutral_site"] == 1.0


def test_values_follow_exact_schema_order(feature_fixture: FeatureFixture) -> None:
    snapshot = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
    )

    assert tuple(snapshot.values) == FEATURE_NAMES_V1
    assert FEATURE_SCHEMA_V1 == tuple((name, "float64") for name in FEATURE_NAMES_V1)
    assert len(snapshot.values) == 42


def test_venue_csv_content_hash_changes_snapshot_identity_but_not_features(
    feature_fixture: FeatureFixture, tmp_path: Path
) -> None:
    baseline = feature_fixture.builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
    )
    copied = tmp_path / "venues-copy.csv"
    _write_venue_csv(copied)
    copied.write_bytes(copied.read_bytes() + b"\n")
    changed_builder = FeatureBuilder(
        feature_fixture.store,
        _policy(),
        VenueStore.from_csv(copied),
        _rating_service(),
    )

    changed = changed_builder.build(
        feature_fixture.event, feature_fixture.origin, CUTOFF, mode="live"
    )

    assert changed.values == baseline.values
    assert changed.feature_vector_sha256 == baseline.feature_vector_sha256
    assert changed.snapshot_id != baseline.snapshot_id


def test_effective_dated_flexed_venue_uses_event_version_at_kickoff(tmp_path: Path) -> None:
    venue_path = tmp_path / "venues.csv"
    venue_path.write_text(
        "venue_id,effective_from_utc,effective_to_utc,available_at_utc,altitude_m,"
        "surface_turf,roof_capable,source_url\n"
        "flex,2026-01-01T00:00:00Z,2026-09-01T00:00:00Z,2026-01-01T00:00:00Z,10,"
        "0,0,https://www.nfl.com/\n"
        "flex,2026-09-01T00:00:00Z,,2026-08-01T00:00:00Z,250,"
        "1,1,https://www.nfl.com/\n",
        encoding="utf-8",
    )
    builder = FeatureBuilder(
        CutoffFactStore(_base_facts()),
        _policy(),
        VenueStore.from_csv(venue_path),
        _rating_service(),
    )

    snapshot = builder.build(
        _event(venue_id="flex"), ForecastOrigin.for_kickoff(Origin.T72, KICKOFF), CUTOFF, "replay"
    )

    assert snapshot.values["venue_altitude_m"] == 250.0
    assert snapshot.values["surface_turf"] == 1.0
    assert snapshot.values["roof_capable"] == 1.0


@pytest.mark.parametrize(
    ("grade", "updates", "message"),
    [
        (ProvenanceGrade.A, {"captured_at_utc": CUTOFF + timedelta(seconds=1)}, "captured"),
        (
            ProvenanceGrade.B,
            {
                "archive_published_at_utc": CUTOFF + timedelta(seconds=1),
                "captured_at_utc": CUTOFF - timedelta(seconds=1),
            },
            "archive",
        ),
        (
            ProvenanceGrade.B,
            {"archive_published_at_utc": None, "captured_at_utc": CUTOFF - timedelta(seconds=1)},
            "archive",
        ),
    ],
)
def test_builder_fails_closed_when_injected_store_violates_grade_cutoff_rules(
    tmp_path: Path,
    grade: ProvenanceGrade,
    updates: dict[str, object],
    message: str,
) -> None:
    venue_path = tmp_path / "venues.csv"
    _write_venue_csv(venue_path)
    invalid = _base_facts()[0].model_copy(update={"provenance_grade": grade, **updates})
    builder = FeatureBuilder(
        ReturningFactStore([invalid, *_base_facts()[1:]]),
        _policy(),
        VenueStore.from_csv(venue_path),
        _rating_service(),
    )

    with pytest.raises(ValueError, match=message):
        builder.build(_event(), Origin.T72, CUTOFF, mode="replay")


def test_grade_b_archive_published_before_cutoff_allows_later_capture(tmp_path: Path) -> None:
    venue_path = tmp_path / "venues.csv"
    _write_venue_csv(venue_path)
    archived_prior = _base_facts()[1].model_copy(
        update={
            "provenance_grade": ProvenanceGrade.B,
            "archive_published_at_utc": CUTOFF - timedelta(seconds=1),
            "captured_at_utc": CUTOFF + timedelta(days=30),
        }
    )
    builder = FeatureBuilder(
        ReturningFactStore([_base_facts()[0], archived_prior, *_base_facts()[2:]]),
        _policy(),
        VenueStore.from_csv(venue_path),
        _rating_service(),
    )

    snapshot = builder.build(_event(), Origin.T72, CUTOFF, mode="replay")

    assert archived_prior.fact_id in snapshot.input_fact_ids
    assert snapshot.provenance_grade is ProvenanceGrade.B


def test_builder_rejects_event_version_not_available_and_captured_by_cutoff(
    tmp_path: Path,
) -> None:
    venue_path = tmp_path / "venues.csv"
    venue_path.write_text(
        "venue_id,effective_from_utc,effective_to_utc,available_at_utc,altitude_m,"
        "surface_turf,roof_capable,source_url\n"
        "test-stadium,2026-01-01T00:00:00Z,,2026-01-01T00:00:00Z,20,1,1,"
        "https://www.nfl.com/\n"
        "future-flex,2026-01-01T00:00:00Z,,2026-01-01T00:00:00Z,200,0,0,"
        "https://www.nfl.com/\n",
        encoding="utf-8",
    )
    builder = FeatureBuilder(
        CutoffFactStore(_base_facts()),
        _policy(),
        VenueStore.from_csv(venue_path),
        _rating_service(),
    )
    future_flex = _event().model_copy(
        update={
            "event_version": 3,
            "venue_id": "future-flex",
            "available_at_utc": CUTOFF + timedelta(seconds=1),
            "captured_at_utc": CUTOFF + timedelta(seconds=1),
        }
    )

    with pytest.raises(ValueError, match="event version"):
        builder.build(future_flex, Origin.T72, CUTOFF, mode="live")


@pytest.mark.parametrize(
    "fact_type",
    [
        "raw_injury_counts",
        "current_game_box_score",
        "season_end_rating",
        "momentum",
        "closing_odds",
        "realized_weather",
        "realized_starter",
        "realized_participation",
        "current_game_outcome",
        "future_v2_family",
    ],
)
def test_builder_closed_allowlist_rejects_every_undeclared_fact_family(
    feature_fixture: FeatureFixture, fact_type: str
) -> None:
    feature_fixture.store.records.append(
        _fact(
            f"unexpected-{fact_type}",
            fact_type,
            entity_keys={"canonical_event_id": feature_fixture.event.canonical_event_id},
            payload={"value": 1},
        )
    )

    with pytest.raises(ValueError, match="fact family not allowed"):
        feature_fixture.builder.build(
            feature_fixture.event, feature_fixture.origin, CUTOFF, mode="live"
        )


def test_missing_division_alignment_fails_closed(feature_fixture: FeatureFixture) -> None:
    feature_fixture.store.records[:] = [
        fact for fact in feature_fixture.store.records if fact.fact_type != "division_alignment"
    ]

    with pytest.raises(ValueError, match="exactly one division_alignment"):
        feature_fixture.builder.build(
            feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
        )


def test_duplicate_division_alignment_fails_closed(feature_fixture: FeatureFixture) -> None:
    feature_fixture.store.records.append(
        _fact(
            "division-duplicate",
            "division_alignment",
            entity_keys={"season": "2026", "home_team": "SEA", "away_team": "NE"},
            payload={"same_division": True},
        )
    )

    with pytest.raises(ValueError, match="exactly one division_alignment"):
        feature_fixture.builder.build(
            feature_fixture.event, feature_fixture.origin, CUTOFF, mode="replay"
        )


def test_builder_rejects_completed_game_for_current_event(
    feature_fixture: FeatureFixture,
) -> None:
    feature_fixture.store.records.append(
        _fact(
            "current-result",
            "completed_game",
            entity_keys={
                "team": "SEA",
                "canonical_event_id": feature_fixture.event.canonical_event_id,
            },
            payload={
                "season": 2026,
                "home_team": "SEA",
                "away_team": "NE",
                "home_score": 99,
                "away_score": 0,
                "neutral_site": False,
                "kickoff_at_utc": feature_fixture.event.kickoff_at_utc.isoformat(),
            },
            observed_at=CUTOFF - timedelta(seconds=1),
        )
    )

    with pytest.raises(ValueError, match="current-game outcome"):
        feature_fixture.builder.build(
            feature_fixture.event, feature_fixture.origin, CUTOFF, mode="live"
        )


def test_builder_rejects_team_game_epa_for_current_event(
    feature_fixture: FeatureFixture,
) -> None:
    feature_fixture.store.records.append(
        _fact(
            "current-event-epa",
            "team_game_epa",
            entity_keys={
                "canonical_event_id": feature_fixture.event.canonical_event_id,
                "offense_team": "SEA",
                "defense_team": "NE",
            },
            payload={
                "offense_epa_per_play": 0.5,
                "pass_epa_per_play": 0.8,
                "rush_epa_per_play": 0.2,
                "plays": 60,
            },
            observed_at=CUTOFF - timedelta(seconds=1),
        )
    )

    with pytest.raises(ValueError, match="current-event team_game_epa"):
        feature_fixture.builder.build(
            feature_fixture.event, feature_fixture.origin, CUTOFF, mode="live"
        )


def test_venue_loader_rejects_overlapping_effective_rows(tmp_path: Path) -> None:
    venue_path = tmp_path / "overlap.csv"
    venue_path.write_text(
        "venue_id,effective_from_utc,effective_to_utc,available_at_utc,altitude_m,"
        "surface_turf,roof_capable,source_url\n"
        "x,2026-01-01T00:00:00Z,,2026-01-01T00:00:00Z,1,0,0,https://www.nfl.com/\n"
        "x,2026-02-01T00:00:00Z,,2026-01-01T00:00:00Z,2,1,1,https://www.nfl.com/\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="overlapping"):
        VenueStore.from_csv(venue_path)


def test_venue_loader_validates_every_source_bundle_member(tmp_path: Path) -> None:
    venue_path = tmp_path / "sources.csv"
    venue_path.write_text(
        "venue_id,effective_from_utc,effective_to_utc,available_at_utc,altitude_m,"
        "surface_turf,roof_capable,source_url\n"
        "x,2026-01-01T00:00:00Z,,2026-01-01T00:00:00Z,1,0,0,"
        "https://www.nfl.com/|http://example.com/not-official\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="HTTPS source_url"):
        VenueStore.from_csv(venue_path)


def test_packaged_venue_csv_covers_2026_home_and_international_venues() -> None:
    path = Path(__file__).parents[2] / "configs" / "venues_v1.csv"

    store = VenueStore.from_csv(path)

    international = {
        "melbourne-cricket-ground",
        "maracana-stadium",
        "tottenham-hotspur-stadium",
        "wembley-stadium",
        "stade-de-france",
        "bernabeu-stadium",
        "fc-bayern-munich-arena",
        "estadio-banorte",
    }
    domestic = {
        "state-farm-stadium",
        "mercedes-benz-stadium",
        "m-and-t-bank-stadium",
        "highmark-stadium",
        "bank-of-america-stadium",
        "soldier-field",
        "paycor-stadium",
        "huntington-bank-field",
        "att-stadium",
        "empower-field-at-mile-high",
        "ford-field",
        "lambeau-field",
        "nrg-stadium",
        "lucas-oil-stadium",
        "everbank-stadium",
        "geha-field-at-arrowhead-stadium",
        "allegiant-stadium",
        "sofi-stadium",
        "hard-rock-stadium",
        "us-bank-stadium",
        "gillette-stadium",
        "caesars-superdome",
        "metlife-stadium",
        "lincoln-financial-field",
        "acrisure-stadium",
        "levis-stadium",
        "lumen-field",
        "raymond-james-stadium",
        "nissan-stadium",
        "northwest-stadium",
    }
    team_to_venue = {
        "ARI": "state-farm-stadium",
        "ATL": "mercedes-benz-stadium",
        "BAL": "m-and-t-bank-stadium",
        "BUF": "highmark-stadium",
        "CAR": "bank-of-america-stadium",
        "CHI": "soldier-field",
        "CIN": "paycor-stadium",
        "CLE": "huntington-bank-field",
        "DAL": "att-stadium",
        "DEN": "empower-field-at-mile-high",
        "DET": "ford-field",
        "GB": "lambeau-field",
        "HOU": "nrg-stadium",
        "IND": "lucas-oil-stadium",
        "JAX": "everbank-stadium",
        "KC": "geha-field-at-arrowhead-stadium",
        "LV": "allegiant-stadium",
        "LAC": "sofi-stadium",
        "LAR": "sofi-stadium",
        "MIA": "hard-rock-stadium",
        "MIN": "us-bank-stadium",
        "NE": "gillette-stadium",
        "NO": "caesars-superdome",
        "NYG": "metlife-stadium",
        "NYJ": "metlife-stadium",
        "PHI": "lincoln-financial-field",
        "PIT": "acrisure-stadium",
        "SF": "levis-stadium",
        "SEA": "lumen-field",
        "TB": "raymond-james-stadium",
        "TEN": "nissan-stadium",
        "WAS": "northwest-stadium",
    }

    assert len(store.rows) == 38
    assert {row.venue_id for row in store.rows} == domestic | international
    assert set(team_to_venue) == {
        "ARI",
        "ATL",
        "BAL",
        "BUF",
        "CAR",
        "CHI",
        "CIN",
        "CLE",
        "DAL",
        "DEN",
        "DET",
        "GB",
        "HOU",
        "IND",
        "JAX",
        "KC",
        "LV",
        "LAC",
        "LAR",
        "MIA",
        "MIN",
        "NE",
        "NO",
        "NYG",
        "NYJ",
        "PHI",
        "PIT",
        "SF",
        "SEA",
        "TB",
        "TEN",
        "WAS",
    }
    assert set(team_to_venue.values()) == domestic
    assert team_to_venue["BUF"] == "highmark-stadium"
    assert team_to_venue["TEN"] == "nissan-stadium"
    assert team_to_venue["LAC"] == team_to_venue["LAR"] == "sofi-stadium"
    assert team_to_venue["NYG"] == team_to_venue["NYJ"] == "metlife-stadium"
    assert all(len(row.source_urls) >= 2 for row in store.rows)
    assert all(url.startswith("https://") for row in store.rows for url in row.source_urls)
