from datetime import UTC, datetime, timedelta

import pytest

from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.identity.events import EventReconciler, QuarantinedEvent, ScheduleFact
from nfl_predictor.identity.teams import canonicalize_team

HOURS_3 = timedelta(hours=3)


@pytest.fixture
def schedule_fact() -> ScheduleFact:
    return ScheduleFact(
        source="nflverse",
        source_event_id="2026_01_NE_SEA",
        season=2026,
        season_type="REG",
        week=1,
        home_team="SEA",
        away_team="NE",
        kickoff_at_utc=datetime(2026, 9, 10, 0, 20, tzinfo=UTC),
        venue_id="lumen-field",
        neutral_site=False,
        observed_at_utc=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        available_at_utc=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        captured_at_utc=datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
        raw_payload_sha256="a" * 64,
    )


@pytest.fixture
def event_v1(schedule_fact: ScheduleFact) -> EventVersion:
    return schedule_fact.to_event_version()


def test_canonicalize_team_maps_nflverse_relocation_alias() -> None:
    assert canonicalize_team("STL") == "LAR"
    assert canonicalize_team("JAC") == "JAX"


def test_canonicalize_team_rejects_unknown_abbreviation() -> None:
    with pytest.raises(ValueError, match="unknown NFL team abbreviation"):
        canonicalize_team("XXX")


def test_schedule_fact_canonicalizes_participant_aliases(schedule_fact: ScheduleFact) -> None:
    aliased = schedule_fact.model_copy(update={"home_team": "LA", "away_team": "JAC"})

    event = aliased.to_event_version()

    assert (event.home_team, event.away_team) == ("LAR", "JAX")


def test_exact_same_event_returns_current(
    event_v1: EventVersion, schedule_fact: ScheduleFact
) -> None:
    result = EventReconciler().reconcile(schedule_fact, [event_v1])

    assert result == event_v1


def test_brand_new_event_creates_version_one(schedule_fact: ScheduleFact) -> None:
    new_fact = schedule_fact.model_copy(update={"source_event_id": "2026_01_NE_SEA_new"})

    result = EventReconciler().reconcile(new_fact, [])

    assert isinstance(result, EventVersion)
    assert result.canonical_event_id == "2026_REG_01_NE_SEA"
    assert result.event_version == 1


def test_kickoff_change_appends_version_without_changing_event_id(
    event_v1: EventVersion, schedule_fact: ScheduleFact
) -> None:
    changed = schedule_fact.model_copy(
        update={"kickoff_at_utc": schedule_fact.kickoff_at_utc + HOURS_3}
    )

    result = EventReconciler().reconcile(changed, [event_v1])

    assert isinstance(result, EventVersion)
    assert result.canonical_event_id == event_v1.canonical_event_id
    assert result.event_version == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [("venue_id", "alternate-venue"), ("neutral_site", True)],
)
def test_schedule_context_change_appends_version(
    event_v1: EventVersion, schedule_fact: ScheduleFact, field: str, value: str | bool
) -> None:
    changed = schedule_fact.model_copy(update={field: value})

    result = EventReconciler().reconcile(changed, [event_v1])

    assert isinstance(result, EventVersion)
    assert result.canonical_event_id == event_v1.canonical_event_id
    assert result.event_version == 2


def test_home_away_inversion_is_quarantined(
    event_v1: EventVersion, schedule_fact: ScheduleFact
) -> None:
    inverted = schedule_fact.model_copy(
        update={"home_team": schedule_fact.away_team, "away_team": schedule_fact.home_team}
    )

    result = EventReconciler().reconcile(inverted, [event_v1])

    assert isinstance(result, QuarantinedEvent)
    assert result.reason_code == "HOME_AWAY_MISMATCH"


def test_multiple_candidate_matches_are_quarantined(
    event_v1: EventVersion, schedule_fact: ScheduleFact
) -> None:
    duplicate_event = event_v1.model_copy(update={"canonical_event_id": "duplicate"})

    result = EventReconciler().reconcile(schedule_fact, [event_v1, duplicate_event])

    assert isinstance(result, QuarantinedEvent)
    assert result.reason_code == "AMBIGUOUS_EVENT_MATCH"


def test_unaliased_participant_match_is_quarantined(
    event_v1: EventVersion, schedule_fact: ScheduleFact
) -> None:
    unaliased = schedule_fact.model_copy(update={"source_event_id": "another-source-id"})

    result = EventReconciler().reconcile(unaliased, [event_v1])

    assert isinstance(result, QuarantinedEvent)
    assert result.reason_code == "UNALIASED_EXISTING_EVENT"
