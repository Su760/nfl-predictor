from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import RawResponse

ALLOWED_HEADERS = {
    "x-requests-last",
    "x-requests-used",
    "x-requests-remaining",
    "content-type",
    "date",
}
_ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


class OddsApiAdapter:
    source = "the_odds_api"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20.0)
        self.clock = clock

    def fetch(self, request: dict[str, Any]) -> RawResponse:
        requested_bookmakers = tuple(request["bookmakers"])
        if not all(isinstance(value, str) and value for value in requested_bookmakers):
            raise ValueError("bookmakers must contain 1 through 10 explicit keys")
        bookmakers = tuple(sorted(set(requested_bookmakers)))
        if not 1 <= len(bookmakers) <= 10:
            raise ValueError("bookmakers must contain 1 through 10 explicit keys")
        if tuple(request["markets"]) != ("h2h",):
            raise ValueError("only full-game h2h is allowed in odds policy v1")
        public_params = {
            "bookmakers": ",".join(bookmakers),
            "dateFormat": "iso",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }
        fingerprint = hashlib.sha256(
            json.dumps(public_params, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        started = self.clock()
        response = self.client.get(
            _ODDS_URL,
            params={**public_params, "apiKey": self.api_key},
        )
        received = self.clock()
        headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower() in ALLOWED_HEADERS
        }
        return RawResponse(
            source=self.source,
            request_fingerprint=fingerprint,
            request_started_at_utc=started,
            response_received_at_utc=received,
            http_status=response.status_code,
            payload=response.content,
            allowlisted_headers=headers,
        )
