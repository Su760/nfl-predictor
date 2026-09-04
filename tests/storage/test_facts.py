from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import NormalizedFact
from nfl_predictor.storage.facts import DuckDbPointInTimeStore, FactQuery, PointInTimeStore

StoreFactory = Callable[[list[NormalizedFact]], PointInTimeStore]


@pytest.fixture(params=[PointInTimeStore.from_records, DuckDbPointInTimeStore.from_records])
def store_factory(request: pytest.FixtureRequest) -> StoreFactory:
    return cast(StoreFactory, request.param)


@pytest.fixture
def fact_factory() -> Callable[..., NormalizedFact]:
    decision = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)

    def make_fact(
        fact_id: str,
        *,
        available_at: datetime = decision,
        captured_at: datetime = decision,
        archive_published_at: datetime | None = None,
        grade: ProvenanceGrade = ProvenanceGrade.A,
        fact_type: str = "team_week",
        entity_keys: dict[str, str] | None = None,
        provider_record_id: str | None = None,
    ) -> NormalizedFact:
        return NormalizedFact(
            fact_id=fact_id,
            capture_id=f"capture-{fact_id}",
            fact_type=fact_type,
            entity_keys=entity_keys or {"team": "SEA"},
            payload={"wins": 1},
            provider_record_id=provider_record_id,
            raw_pointer=f"raw/{fact_id}.json",
            available_at_utc=available_at,
            captured_at_utc=captured_at,
            archive_published_at_utc=archive_published_at,
            provenance_grade=grade,
            normalization_schema_version="1",
            fact_content_sha256="f" * 64,
        )

    return make_fact


def test_grade_a_rejects_fact_captured_after_decision(
    store_factory: StoreFactory,
    fact_factory: Callable[..., NormalizedFact],
) -> None:
    decision = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
    before = fact_factory("before", available_at=decision, captured_at=decision)
    after = fact_factory("after", captured_at=decision + timedelta(minutes=1))
    store = store_factory([before, after])

    selected = store.select(
        FactQuery(
            fact_type="team_week",
            entity_keys={"team": "SEA"},
            decision_at_utc=decision,
            provenance_grade=ProvenanceGrade.A,
        )
    )

    assert [fact.fact_id for fact in selected] == ["before"]


def test_grade_b_requires_archive_publication_by_decision(
    store_factory: StoreFactory,
    fact_factory: Callable[..., NormalizedFact],
) -> None:
    decision = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
    published = fact_factory("published", grade=ProvenanceGrade.B, archive_published_at=decision)
    unpublished = fact_factory("unpublished", grade=ProvenanceGrade.B, archive_published_at=None)
    published_late = fact_factory(
        "published-late",
        grade=ProvenanceGrade.B,
        archive_published_at=decision + timedelta(seconds=1),
    )

    selected = store_factory([published_late, unpublished, published]).select(
        FactQuery(
            fact_type="team_week",
            entity_keys={"team": "SEA"},
            decision_at_utc=decision,
            provenance_grade=ProvenanceGrade.B,
        )
    )

    assert [fact.fact_id for fact in selected] == ["published"]


def test_grade_c_uses_declared_availability_without_capture_or_archive_cutoff(
    store_factory: StoreFactory,
    fact_factory: Callable[..., NormalizedFact],
) -> None:
    decision = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
    reconstructed = fact_factory(
        "reconstructed",
        captured_at=decision + timedelta(days=30),
        grade=ProvenanceGrade.C,
    )
    unavailable = fact_factory(
        "unavailable",
        available_at=decision + timedelta(seconds=1),
        captured_at=decision + timedelta(days=30),
        grade=ProvenanceGrade.C,
    )

    selected = store_factory([unavailable, reconstructed]).select(
        FactQuery(
            fact_type="team_week",
            entity_keys={"team": "SEA"},
            decision_at_utc=decision,
            provenance_grade=ProvenanceGrade.C,
        )
    )

    assert [fact.fact_id for fact in selected] == ["reconstructed"]


def test_select_filters_entity_keys_and_keeps_latest_provider_version(
    store_factory: StoreFactory,
    fact_factory: Callable[..., NormalizedFact],
) -> None:
    decision = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
    old = fact_factory(
        "old",
        available_at=decision - timedelta(minutes=2),
        captured_at=decision - timedelta(minutes=2),
        provider_record_id="provider-1",
    )
    latest = fact_factory(
        "latest",
        available_at=decision - timedelta(minutes=1),
        captured_at=decision - timedelta(minutes=1),
        provider_record_id="provider-1",
    )
    other_team = fact_factory("other", entity_keys={"team": "SF"})

    selected = store_factory([latest, other_team, old]).select(
        FactQuery(
            fact_type="team_week",
            entity_keys={"team": "SEA"},
            decision_at_utc=decision,
            provenance_grade=ProvenanceGrade.A,
        )
    )

    assert [fact.fact_id for fact in selected] == ["latest"]


def _event() -> EventVersion:
    observed = datetime(2026, 8, 1, tzinfo=UTC)
    return EventVersion(
        canonical_event_id="2026-SEA-SF",
        event_version=1,
        source_event_ids={"nflverse": "game-1"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="SEA",
        away_team="SF",
        kickoff_at_utc=datetime(2026, 9, 7, 20, 20, tzinfo=UTC),
        venue_id="lumen",
        neutral_site=False,
        observed_at_utc=observed,
        available_at_utc=observed,
        captured_at_utc=observed,
        raw_payload_sha256="a" * 64,
    )


def test_for_event_features_matches_event_teams_and_venue_only(
    store_factory: StoreFactory,
    fact_factory: Callable[..., NormalizedFact],
) -> None:
    event_fact = fact_factory("event", entity_keys={"canonical_event_id": "2026-SEA-SF"})
    away_fact = fact_factory("away", entity_keys={"team": "SF"})
    venue_fact = fact_factory("venue", entity_keys={"venue_id": "lumen"})
    unrelated = fact_factory("unrelated", entity_keys={"team": "GB"})
    late = fact_factory(
        "late",
        entity_keys={"team": "SEA"},
        captured_at=datetime(2026, 9, 7, 0, 21, tzinfo=UTC),
    )

    selected = store_factory(
        [unrelated, venue_fact, late, event_fact, away_fact]
    ).for_event_features(_event(), datetime(2026, 9, 7, 0, 20, tzinfo=UTC))

    assert [fact.fact_id for fact in selected] == ["away", "event", "venue"]


def test_event_features_include_offense_and_defense_relationships(
    store_factory: StoreFactory,
    fact_factory: Callable[..., NormalizedFact],
) -> None:
    event = _event()
    cutoff = datetime(2026, 9, 7, 0, 20, tzinfo=UTC)
    home_on_offense = fact_factory(
        "home-on-offense",
        entity_keys={"offense_team": event.home_team, "defense_team": "OPP"},
    )
    home_on_defense = fact_factory(
        "home-on-defense",
        entity_keys={"offense_team": "OPP", "defense_team": event.home_team},
    )
    unrelated = fact_factory(
        "unrelated-matchup",
        entity_keys={"offense_team": "GB", "defense_team": "DAL"},
    )

    selected = store_factory([home_on_offense, home_on_defense, unrelated]).for_event_features(
        event, cutoff
    )

    assert {fact.fact_id for fact in selected} == {"home-on-offense", "home-on-defense"}
    assert {fact.entity_keys["offense_team"] for fact in selected} == {event.home_team, "OPP"}
