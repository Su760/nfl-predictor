from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.lineage import NormalizedFact
from nfl_predictor.features.builder import FeatureBuilder
from nfl_predictor.features.policy import FeaturePolicy
from nfl_predictor.features.team_strength import PointInTimeRatingService
from nfl_predictor.features.venue import VenueStore


class _Store:
    def __init__(self, facts: list[NormalizedFact]) -> None:
        self.facts = facts

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        del event
        return [
            fact
            for fact in self.facts
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


def _policy() -> FeaturePolicy:
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
        elo_k_factor=20.0,
        elo_offseason_retention=1.0,
        elo_logistic_scale=400.0,
        elo_mov_denominator=2.2,
        elo_mov_rating_scale=0.001,
        massey_home_field_points=0.0,
    )


def _rating_service(policy: FeaturePolicy) -> PointInTimeRatingService:
    return PointInTimeRatingService(policy=policy)


def _fact(
    fact_id: str,
    fact_type: str,
    entity_keys: dict[str, str],
    payload: dict[str, object],
    *,
    capture_prefix: str,
) -> NormalizedFact:
    before = datetime(2026, 9, 1, tzinfo=UTC)
    return NormalizedFact(
        fact_id=fact_id,
        capture_id=f"{capture_prefix}-{fact_id}",
        fact_type=fact_type,
        entity_keys=entity_keys,
        payload=payload,
        raw_pointer=f"raw/{capture_prefix}/{fact_id}.json",
        available_at_utc=before,
        captured_at_utc=before,
        provenance_grade=ProvenanceGrade.A,
        normalization_schema_version="fact-v1",
        fact_content_sha256="0" * 64,
    )


def _facts(capture_prefix: str) -> list[NormalizedFact]:
    return [
        _fact(
            "league-strength-2026",
            "league_strength_prior",
            {"season": "2026"},
            {
                "elo": 1500.0,
                "colley": 0.5,
                "massey": 0.0,
                "off_epa": 0.0,
                "def_epa": 0.0,
                "pass_epa": 0.0,
                "rush_epa": 0.0,
            },
            capture_prefix=capture_prefix,
        ),
        _fact(
            "sea-prior",
            "team_passing_prior",
            {"team": "SEA"},
            {"epa_per_play": 0.10, "cpoe": 1.5},
            capture_prefix=capture_prefix,
        ),
        _fact(
            "ne-prior",
            "team_passing_prior",
            {"team": "NE"},
            {"epa_per_play": -0.04, "cpoe": -0.5},
            capture_prefix=capture_prefix,
        ),
        _fact(
            "division",
            "division_alignment",
            {"season": "2026", "home_team": "SEA", "away_team": "NE"},
            {"same_division": False},
            capture_prefix=capture_prefix,
        ),
    ]


def test_live_and_replay_use_separate_stores_but_identical_feature_identity(
    tmp_path: Path,
) -> None:
    decision_at = datetime(2026, 9, 13, 17, tzinfo=UTC)
    kickoff = decision_at + timedelta(hours=72)
    venue_path = tmp_path / "venues.csv"
    venue_path.write_text(
        "venue_id,effective_from_utc,effective_to_utc,available_at_utc,altitude_m,"
        "surface_turf,roof_capable,source_url\n"
        "test,2026-01-01T00:00:00Z,,2026-01-01T00:00:00Z,20,1,1,"
        "https://www.nfl.com/\n",
        encoding="utf-8",
    )
    event = EventVersion(
        canonical_event_id="2026-NE-at-SEA",
        event_version=1,
        source_event_ids={"nfl": "401"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="SEA",
        away_team="NE",
        kickoff_at_utc=kickoff,
        venue_id="test",
        neutral_site=False,
        observed_at_utc=datetime(2026, 8, 1, tzinfo=UTC),
        available_at_utc=datetime(2026, 8, 1, tzinfo=UTC),
        captured_at_utc=datetime(2026, 8, 1, tzinfo=UTC),
        raw_payload_sha256="0" * 64,
    )
    origin = ForecastOrigin.for_kickoff(Origin.T72, kickoff)
    policy = _policy()
    live_builder = FeatureBuilder(
        _Store(list(reversed(_facts("live-capture")))),
        policy,
        VenueStore.from_csv(venue_path),
        _rating_service(policy),
    )
    replay_builder = FeatureBuilder(
        _Store(_facts("archive-capture")),
        policy,
        VenueStore.from_csv(venue_path),
        _rating_service(policy),
    )

    live = live_builder.build(event, origin, decision_at, mode="live")
    replay = replay_builder.build(event, origin, decision_at, mode="replay")

    assert replay.input_manifest_ids != live.input_manifest_ids
    assert replay.input_fact_ids == live.input_fact_ids
    assert replay.values == live.values
    assert replay.feature_vector_sha256 == live.feature_vector_sha256
    assert replay.snapshot_id == live.snapshot_id
