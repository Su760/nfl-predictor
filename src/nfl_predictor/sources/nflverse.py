from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import nflreadpy  # type: ignore[import-untyped]

from .base import RawResponse

LOADERS: dict[str, Callable[..., Any]] = {
    "schedules": nflreadpy.load_schedules,
    "pbp": nflreadpy.load_pbp,
    "team_stats": nflreadpy.load_team_stats,
    "player_stats": nflreadpy.load_player_stats,
    "rosters": nflreadpy.load_rosters,
}
_SUPPORTED_DATASETS = frozenset(LOADERS)


class NflverseAdapter:
    source = "nflverse"

    def __init__(
        self,
        loaders: Mapping[str, Callable[..., Any]],
        clock: Callable[[], datetime],
    ) -> None:
        self.loaders = loaders
        self.clock = clock

    def fetch(self, request: dict[str, Any]) -> RawResponse:
        dataset = str(request["dataset"])
        if dataset not in _SUPPORTED_DATASETS or dataset not in self.loaders:
            raise ValueError(f"unsupported nflverse dataset: {dataset}")
        seasons = tuple(sorted({int(value) for value in request["seasons"]}))
        started = self.clock()
        frame = self.loaders[dataset](seasons=list(seasons))
        buffer = io.BytesIO()
        frame.write_ipc(buffer)
        received = self.clock()
        public_request = {"dataset": dataset, "seasons": seasons}
        fingerprint = hashlib.sha256(
            json.dumps(public_request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return RawResponse(
            source=self.source,
            request_fingerprint=fingerprint,
            request_started_at_utc=started,
            response_received_at_utc=received,
            http_status=200,
            payload=buffer.getvalue(),
            allowlisted_headers={},
            # No earlier publication time is documented by this loader boundary.
            # Normalizers must therefore use the captured time for availability.
            source_snapshot_at_utc=None,
        )
