from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from nfl_predictor.betting.policy import BookSettlementContract, OddsPolicy
from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.markets.normalizer import QuoteRejection, normalize_h2h

NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


PROVIDER_TEAM_NAMES = (
    {"provider_name": "Chicago Bears", "canonical_team": "CHI"},
    {"provider_name": "Green Bay Packers", "canonical_team": "GB"},
)


def _event(
    *,
    event_id: str = "event-1",
    source_event_ids: dict[str, str] | None = None,
) -> EventVersion:
    return EventVersion(
        canonical_event_id=event_id,
        event_version=1,
        source_event_ids=(
            {"the_odds_api": "provider-1"} if source_event_ids is None else source_event_ids
        ),
        season=2026,
        season_type="REG",
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=NOW + timedelta(hours=2),
        neutral_site=False,
        observed_at_utc=NOW - timedelta(days=1),
        available_at_utc=NOW - timedelta(days=1),
        captured_at_utc=NOW - timedelta(days=1),
        raw_payload_sha256="a" * 64,
    )


def _manifest() -> CaptureManifest:
    return CaptureManifest(
        capture_id="capture-1",
        run_id="run-1",
        source="the_odds_api",
        request_fingerprint="request-1",
        request_started_at_utc=NOW - timedelta(seconds=2),
        response_received_at_utc=NOW,
        http_status=200,
        raw_path="raw/odds.json",
        raw_payload_sha256="b" * 64,
        response_headers_allowlisted={},
        code_sha="1" * 40,
        dependency_lock_sha256="2" * 64,
        schema_version="raw-v1",
    )


def _contract(book_key: str = "book-a") -> BookSettlementContract:
    return BookSettlementContract(
        book_key=book_key,
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="2026-08-28",
        overtime_included=True,
        tie_handling="push",
        verified_on="2026-08-28",
    )


def _policy() -> OddsPolicy:
    return OddsPolicy.model_validate(
        {
            "book_allowlist": ("book-a",),
            "book_settlement_contracts": (_contract(),),
            "provider_team_names": PROVIDER_TEAM_NAMES,
        }
    )


def _payload(*, outcomes: list[dict[str, object]] | None = None) -> bytes:
    rows = outcomes or [
        {"name": "Chicago Bears", "price": 2.0},
        {"name": "Green Bay Packers", "price": 1.8},
    ]
    return json.dumps(
        [
            {
                "id": "provider-1",
                "commence_time": (NOW + timedelta(hours=2)).isoformat(),
                "home_team": "Chicago Bears",
                "away_team": "Green Bay Packers",
                "bookmakers": [
                    {
                        "key": "book-a",
                        "title": "Book A",
                        "last_update": (NOW - timedelta(minutes=2)).isoformat(),
                        "markets": [{"key": "h2h", "outcomes": rows}],
                    }
                ],
            }
        ]
    ).encode()


def test_valid_same_snapshot_two_way_moneyline_is_normalized() -> None:
    result = normalize_h2h(_payload(), _manifest(), (_event(),), _policy())

    assert len(result.quotes) == 1
    quote = result.quotes[0]
    assert quote.canonical_event_id == "event-1"
    assert quote.capture_id == "capture-1"
    assert quote.book_key == "book-a"
    assert [(item.side, item.team, item.decimal_price) for item in quote.selections] == [
        ("home", "CHI", Decimal("2.0")),
        ("away", "GB", Decimal("1.8")),
    ]
    assert quote.capture_lag_seconds == 2
    assert quote.provider_update_lag_seconds == 120
    assert quote.overtime_included is True
    assert quote.tie_handling == "push"
    assert result.rejections == ()


@pytest.mark.parametrize(
    ("commence_time", "reason"),
    [
        (None, "PROVIDER_KICKOFF_MISSING"),
        ("not-a-time", "PROVIDER_KICKOFF_INVALID"),
        (
            (NOW + timedelta(hours=2)).astimezone(timezone(timedelta(hours=-5))).isoformat(),
            "PROVIDER_KICKOFF_INVALID",
        ),
        ((NOW + timedelta(hours=3)).isoformat(), "PROVIDER_KICKOFF_MISMATCH"),
    ],
)
def test_provider_kickoff_must_exactly_match_active_event(
    commence_time: str | None, reason: str
) -> None:
    payload = json.loads(_payload())
    if commence_time is None:
        del payload[0]["commence_time"]
    else:
        payload[0]["commence_time"] = commence_time

    result = normalize_h2h(json.dumps(payload).encode(), _manifest(), (_event(),), _policy())

    assert result.quotes == ()
    assert result.rejections == (QuoteRejection("$[0]", reason),)


def test_manifest_uses_frozen_provider_source_key() -> None:
    wrong_manifest = _manifest().model_copy(update={"source": "odds_api"})

    manifest_result = normalize_h2h(_payload(), wrong_manifest, (_event(),), _policy())

    assert manifest_result.rejections[0].reason_code == "CAPTURE_SOURCE_MISMATCH"


def test_bootstrap_match_uses_canonical_teams_and_exact_kickoff_without_existing_alias() -> None:
    event = _event(source_event_ids={"nflverse": "2026_01_GB_CHI"})

    result = normalize_h2h(_payload(), _manifest(), (event,), _policy())

    assert len(result.quotes) == 1
    assert result.quotes[0].source_event_id == "provider-1"
    assert [selection.team for selection in result.quotes[0].selections] == ["CHI", "GB"]


def test_bootstrap_rejects_provider_id_owned_by_a_different_active_event() -> None:
    aliasless_match = _event(source_event_ids={"nflverse": "2026_01_GB_CHI"})
    conflicting_owner = _event(
        event_id="event-other",
        source_event_ids={"the_odds_api": "provider-1"},
    ).model_copy(
        update={
            "home_team": "DAL",
            "away_team": "PHI",
            "kickoff_at_utc": NOW + timedelta(hours=4),
        }
    )

    result = normalize_h2h(_payload(), _manifest(), (conflicting_owner, aliasless_match), _policy())

    assert result.quotes == ()
    assert result.rejections == (QuoteRejection("$[0]", "PROVIDER_EVENT_ID_OWNERSHIP_CONFLICT"),)


def test_matching_provider_id_with_multiple_active_owners_is_rejected() -> None:
    matching_owner = _event()
    second_owner = _event(
        event_id="event-other",
        source_event_ids={"the_odds_api": "provider-1"},
    ).model_copy(
        update={
            "home_team": "DAL",
            "away_team": "PHI",
            "kickoff_at_utc": NOW + timedelta(hours=4),
        }
    )

    result = normalize_h2h(_payload(), _manifest(), (matching_owner, second_owner), _policy())

    assert result.quotes == ()
    assert result.rejections == (QuoteRejection("$[0]", "PROVIDER_EVENT_ID_MULTIPLE_OWNERS"),)


def test_established_provider_alias_must_match_exactly_and_never_bootstraps_over_mismatch() -> None:
    accepted = normalize_h2h(_payload(), _manifest(), (_event(),), _policy())
    payload = json.loads(_payload())
    payload[0]["id"] = "provider-other"
    rejected = normalize_h2h(json.dumps(payload).encode(), _manifest(), (_event(),), _policy())

    assert len(accepted.quotes) == 1
    assert rejected.quotes == ()
    assert rejected.rejections == (QuoteRejection("$[0]", "PROVIDER_EVENT_ID_MISMATCH"),)


def test_provider_team_inversion_unknown_name_and_ambiguous_bootstrap_are_rejected() -> None:
    inverted = json.loads(_payload())
    inverted[0]["home_team"] = "Green Bay Packers"
    inverted[0]["away_team"] = "Chicago Bears"
    unknown = json.loads(_payload())
    unknown[0]["home_team"] = "Unknown Team"
    aliasless = _event(source_event_ids={"nflverse": "one"})
    duplicate = _event(event_id="event-2", source_event_ids={"nflverse": "two"})

    inverted_result = normalize_h2h(
        json.dumps(inverted).encode(), _manifest(), (_event(),), _policy()
    )
    unknown_result = normalize_h2h(
        json.dumps(unknown).encode(), _manifest(), (_event(),), _policy()
    )
    ambiguous_result = normalize_h2h(_payload(), _manifest(), (aliasless, duplicate), _policy())

    assert inverted_result.rejections == (QuoteRejection("$[0]", "PROVIDER_TEAM_INVERSION"),)
    assert unknown_result.rejections == (QuoteRejection("$[0]", "PROVIDER_TEAM_UNKNOWN"),)
    assert ambiguous_result.rejections == (QuoteRejection("$[0]", "EVENT_BOOTSTRAP_AMBIGUOUS"),)


def test_draw_market_and_mixed_book_sides_are_rejected() -> None:
    draw = json.loads(_payload())
    draw[0]["bookmakers"][0]["markets"][0]["outcomes"].append({"name": "Draw", "price": 15.0})
    split = json.loads(_payload())
    split[0]["bookmakers"][0]["markets"] = [
        {"key": "h2h", "outcomes": [{"name": "Chicago Bears", "price": 2.0}]},
        {"key": "h2h", "outcomes": [{"name": "Green Bay Packers", "price": 1.8}]},
    ]
    payload = json.dumps(draw + split).encode()

    result = normalize_h2h(payload, _manifest(), (_event(),), _policy())

    assert result.quotes == ()
    assert {item.reason_code for item in result.rejections} == {
        "DRAW_OUTCOME_NOT_SUPPORTED",
        "SIDES_NOT_FROM_ONE_BOOK_SNAPSHOT",
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda row: row.update(id="wrong"), "PROVIDER_EVENT_ID_MISMATCH"),
        (
            lambda row: row["bookmakers"][0].update(key="not-allowed"),
            "BOOK_NOT_ALLOWLISTED",
        ),
        (
            lambda row: row["bookmakers"][0]["markets"][0]["outcomes"][0].update(price=1),
            "INVALID_DECIMAL_PRICE",
        ),
        (
            lambda row: row["bookmakers"][0].update(
                last_update=(NOW + timedelta(seconds=1)).isoformat()
            ),
            "PROVIDER_UPDATE_AFTER_RESPONSE",
        ),
    ],
)
def test_invalid_raw_evidence_is_preserved_with_a_reason_code(mutation, reason: str) -> None:
    payload = json.loads(_payload())
    mutation(payload[0])

    result = normalize_h2h(json.dumps(payload).encode(), _manifest(), (_event(),), _policy())

    assert result.quotes == ()
    assert result.rejections[0].reason_code == reason
    assert result.rejections[0].raw_pointer.startswith("$[0]")


def test_t60_quote_older_than_fifteen_minutes_is_preserved_but_ineligible() -> None:
    payload = json.loads(_payload())
    payload[0]["bookmakers"][0]["last_update"] = (NOW - timedelta(minutes=16)).isoformat()
    quote = normalize_h2h(json.dumps(payload).encode(), _manifest(), (_event(),), _policy()).quotes[
        0
    ]

    result = _policy().eligibility(quote, origin=Origin.T60, decision_at=NOW)

    assert result.eligible is False
    assert "PROVIDER_UPDATE_TOO_OLD" in result.reason_codes


def test_response_before_request_is_preserved_as_a_rejection() -> None:
    manifest = _manifest().model_copy(update={"request_started_at_utc": NOW + timedelta(seconds=1)})

    result = normalize_h2h(_payload(), manifest, (_event(),), _policy())

    assert result.quotes == ()
    assert result.rejections[0].reason_code == "RESPONSE_BEFORE_REQUEST"


def test_policy_loader_path_constant_is_not_assumed_by_normalizer(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JSON"):
        normalize_h2h(b"not-json", _manifest(), (_event(),), _policy())


def test_raw_pointers_preserve_actual_market_and_outcome_indices() -> None:
    payload = json.loads(_payload())
    payload[0]["bookmakers"][0]["markets"] = [
        {"key": "spreads", "outcomes": []},
        {
            "key": "h2h",
            "outcomes": [
                {"id": "away-id", "name": "Green Bay Packers", "price": 1.8},
                {"id": "home-id", "name": "Chicago Bears", "price": 2.0},
            ],
        },
    ]

    quote = normalize_h2h(json.dumps(payload).encode(), _manifest(), (_event(),), _policy()).quotes[
        0
    ]

    assert quote.selections[0].raw_pointer.endswith("markets[1].outcomes[1]")
    assert quote.selections[1].raw_pointer.endswith("markets[1].outcomes[0]")
