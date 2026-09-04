from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from nfl_predictor.capture.service import CaptureService
from nfl_predictor.sources.base import BuildIdentity
from nfl_predictor.sources.nflverse import NflverseAdapter
from nfl_predictor.sources.odds_api import OddsApiAdapter

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
_ODDS_FIXTURE = Path(__file__).parents[1] / "fixtures" / "odds" / "nfl_h2h.json"
_SCHEDULES_FIXTURE = Path(__file__).parents[1] / "fixtures" / "nflverse" / "schedules.parquet"


def _client(
    *, status_code: int = 200, content: bytes | None = None
) -> tuple[httpx.Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code,
            content=content if content is not None else _ODDS_FIXTURE.read_bytes(),
            headers={
                "x-requests-last": "1",
                "x-requests-used": "8",
                "x-requests-remaining": "492",
                "content-type": "application/json",
                "date": "Fri, 28 Aug 2026 12:00:00 GMT",
                "x-provider-trace": "drop-me",
                "authorization": "drop-me-too",
            },
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def test_request_fingerprint_excludes_key_and_keeps_only_allowlisted_headers() -> None:
    client, requests = _client()
    response = OddsApiAdapter(api_key="never-log-me", client=client, clock=lambda: _NOW).fetch(
        {
            "sport": "americanfootball_nfl",
            "bookmakers": ["book-b", "book-a"],
            "markets": ["h2h"],
        }
    )

    assert dict(requests[0].url.params) == {
        "bookmakers": "book-a,book-b",
        "dateFormat": "iso",
        "markets": "h2h",
        "oddsFormat": "decimal",
        "apiKey": "never-log-me",
    }
    assert (
        response.request_fingerprint
        == "f77b631a9e82b6381e4194d65585b18caa3e3ec63b1d022df0abc8c0344ced68"
    )
    assert "never-log-me" not in response.request_fingerprint
    assert response.allowlisted_headers == {
        "content-type": "application/json",
        "date": "Fri, 28 Aug 2026 12:00:00 GMT",
        "x-requests-last": "1",
        "x-requests-remaining": "492",
        "x-requests-used": "8",
    }


def test_public_request_fingerprint_is_deterministic_for_bookmaker_order() -> None:
    first_client, _ = _client()
    second_client, _ = _client()
    first = OddsApiAdapter("key-one", first_client, clock=lambda: _NOW).fetch(
        {"bookmakers": ["book-b", "book-a"], "markets": ["h2h"]}
    )
    second = OddsApiAdapter("key-two", second_client, clock=lambda: _NOW).fetch(
        {"bookmakers": ["book-a", "book-b"], "markets": ["h2h"]}
    )

    assert first.request_fingerprint == second.request_fingerprint


@pytest.mark.parametrize("bookmakers", [[], [""], [f"book-{index}" for index in range(11)]])
def test_odds_policy_requires_one_through_ten_explicit_bookmakers(
    bookmakers: list[str],
) -> None:
    client, _ = _client()
    adapter = OddsApiAdapter("key", client, clock=lambda: _NOW)

    with pytest.raises(ValueError, match="1 through 10"):
        adapter.fetch({"bookmakers": bookmakers, "markets": ["h2h"]})


@pytest.mark.parametrize("markets", [[], ["spreads"], ["h2h", "totals"]])
def test_odds_policy_accepts_only_full_game_h2h(markets: list[str]) -> None:
    client, _ = _client()
    adapter = OddsApiAdapter("key", client, clock=lambda: _NOW)

    with pytest.raises(ValueError, match="only full-game h2h"):
        adapter.fetch({"bookmakers": ["book-a"], "markets": markets})


def test_non_200_response_preserves_provider_status_and_exact_bytes() -> None:
    payload = b'{"message":"quota exhausted"}\n'
    client, _ = _client(status_code=429, content=payload)

    response = OddsApiAdapter("key", client, clock=lambda: _NOW).fetch(
        {"bookmakers": ["book-a"], "markets": ["h2h"]}
    )

    assert response.http_status == 429
    assert response.payload == payload


def test_api_key_is_absent_from_persisted_capture_fields(tmp_path: Path) -> None:
    client, _ = _client()
    adapter = OddsApiAdapter("never-persist-me", client, clock=lambda: _NOW)
    service = CaptureService(
        tmp_path,
        normalizer=lambda payload, manifest: None,
        build_identity=BuildIdentity("1" * 40, "2" * 64),
    )

    service.capture(
        adapter,
        {"bookmakers": ["book-a"], "markets": ["h2h"]},
        run_id="run-1",
    )

    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*.*"))
    assert b"never-persist-me" not in persisted


def test_nflverse_serializes_an_injected_supported_dataset_without_live_io() -> None:
    loader_requests: list[list[int]] = []

    def load_schedules(*, seasons: list[int]) -> pl.DataFrame:
        loader_requests.append(seasons)
        return pl.read_parquet(_SCHEDULES_FIXTURE)

    adapter = NflverseAdapter(loaders={"schedules": load_schedules}, clock=lambda: _NOW)
    response = adapter.fetch({"dataset": "schedules", "seasons": [2025, 2024]})
    restored = pl.read_ipc(response.payload)

    assert loader_requests == [[2024, 2025]]
    assert restored.to_dicts() == [
        {
            "game_id": "2024_01_ARI_BUF",
            "season": 2024,
            "week": 1,
            "home_team": "BUF",
            "away_team": "ARI",
        }
    ]
    assert response.http_status == 200
    assert response.source_snapshot_at_utc is None


def test_nflverse_fingerprint_is_deterministic_for_season_order() -> None:
    loader = lambda *, seasons: pl.read_parquet(_SCHEDULES_FIXTURE)
    adapter = NflverseAdapter(loaders={"schedules": loader}, clock=lambda: _NOW)

    first = adapter.fetch({"dataset": "schedules", "seasons": [2025, 2024]})
    second = adapter.fetch({"dataset": "schedules", "seasons": [2024, 2025]})

    assert first.request_fingerprint == second.request_fingerprint


def test_nflverse_rejects_participation_even_when_an_injected_loader_offers_it() -> None:
    called = False

    def load_participation(*, seasons: list[int]) -> pl.DataFrame:
        nonlocal called
        called = True
        return pl.DataFrame()

    adapter = NflverseAdapter(loaders={"participation": load_participation}, clock=lambda: _NOW)

    with pytest.raises(ValueError, match="unsupported nflverse dataset"):
        adapter.fetch({"dataset": "participation", "seasons": [2024]})
    assert called is False


@pytest.mark.parametrize(
    "dataset",
    ["schedules", "pbp", "team_stats", "player_stats", "rosters"],
)
def test_nflverse_supports_only_the_allowlisted_archival_datasets(dataset: str) -> None:
    adapter = NflverseAdapter(
        loaders={dataset: lambda *, seasons: pl.DataFrame({"season": seasons})},
        clock=lambda: _NOW,
    )

    response = adapter.fetch({"dataset": dataset, "seasons": [2024]})

    assert pl.read_ipc(response.payload)["season"].to_list() == [2024]
