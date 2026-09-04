from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from nfl_predictor.betting.policy import OddsPolicy
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.contracts.markets import MoneylineQuote


@dataclass(frozen=True)
class QuoteRejection:
    raw_pointer: str
    reason_code: str


@dataclass(frozen=True)
class QuoteNormalizationResult:
    quotes: tuple[MoneylineQuote, ...]
    rejections: tuple[QuoteRejection, ...]


def normalize_h2h(
    raw_payload: bytes,
    manifest: CaptureManifest,
    active_events: Iterable[EventVersion],
    policy: OddsPolicy,
) -> QuoteNormalizationResult:
    payload = policy.decode_json(raw_payload)
    if manifest.source != policy.provider_source_key:
        return QuoteNormalizationResult((), (QuoteRejection("$", "CAPTURE_SOURCE_MISMATCH"),))
    events = tuple(active_events)
    quotes: list[MoneylineQuote] = []
    rejections: list[QuoteRejection] = []
    for event_index, provider_event in enumerate(payload):
        match = policy.match_event_result(provider_event, events)
        event = match.event
        if event is None:
            rejections.append(
                QuoteRejection(
                    f"$[{event_index}]",
                    match.reason_code or "EVENT_NOT_EXACTLY_MATCHED",
                )
            )
            continue
        bookmakers = provider_event.get("bookmakers", [])
        if not isinstance(bookmakers, list):
            rejections.append(QuoteRejection(f"$[{event_index}]", "BOOKMAKERS_INVALID"))
            continue
        for book_index, bookmaker in enumerate(bookmakers):
            pointer = f"$[{event_index}].bookmakers[{book_index}]"
            if not isinstance(bookmaker, Mapping):
                rejections.append(QuoteRejection(pointer, "BOOKMAKER_INVALID"))
                continue
            parsed: MoneylineQuote | Any = policy.parse_verified_full_game_h2h(
                bookmaker=bookmaker,
                event=event,
                source_event_id=match.source_event_id or "",
                manifest=manifest,
                raw_pointer=pointer,
            )
            if isinstance(parsed, QuoteRejection):
                rejections.append(parsed)
            else:
                quotes.append(parsed)
    return QuoteNormalizationResult(tuple(quotes), tuple(rejections))
