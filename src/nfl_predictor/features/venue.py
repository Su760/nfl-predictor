from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlparse

from nfl_predictor.contracts.events import EventVersion

from .schema import VENUE_FEATURE_NAMES

VENUE_CSV_HEADERS = (
    "venue_id",
    "effective_from_utc",
    "effective_to_utc",
    "available_at_utc",
    "altitude_m",
    "surface_turf",
    "roof_capable",
    "source_url",
)


def _parse_utc(value: str, *, field: str, optional: bool = False) -> datetime | None:
    if optional and not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an aware-UTC ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must be an aware-UTC ISO timestamp")
    return parsed


def _parse_bool(value: str, *, field: str) -> bool:
    if value not in {"0", "1"}:
        raise ValueError(f"{field} must be 0 or 1")
    return value == "1"


@dataclass(frozen=True)
class VenueRow:
    venue_id: str
    effective_from_utc: datetime
    effective_to_utc: datetime | None
    available_at_utc: datetime
    altitude_m: float
    surface_turf: bool
    roof_capable: bool
    source_url: str

    @property
    def source_urls(self) -> tuple[str, ...]:
        return tuple(member.strip() for member in self.source_url.split("|") if member.strip())


class VenueStore:
    def __init__(self, rows: list[VenueRow], csv_sha256: str) -> None:
        self.rows = tuple(rows)
        self.csv_sha256 = csv_sha256
        self._validate_no_overlaps()

    @classmethod
    def from_csv(cls, path: str | Path) -> VenueStore:
        payload = Path(path).read_bytes()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("venue CSV must be UTF-8") from error
        reader = csv.DictReader(io.StringIO(text))
        if tuple(reader.fieldnames or ()) != VENUE_CSV_HEADERS:
            raise ValueError(f"venue CSV headers must be exactly {VENUE_CSV_HEADERS}")
        rows: list[VenueRow] = []
        for line_number, raw in enumerate(reader, start=2):
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"venue CSV row {line_number} has the wrong number of columns")
            venue_id = raw["venue_id"].strip()
            source_url = raw["source_url"].strip()
            if not venue_id:
                raise ValueError(f"venue CSV row {line_number} requires venue_id")
            source_urls = tuple(
                member.strip() for member in source_url.split("|") if member.strip()
            )
            if not source_urls or any(
                urlparse(member).scheme != "https" or not urlparse(member).netloc
                for member in source_urls
            ):
                raise ValueError(f"venue CSV row {line_number} requires an HTTPS source_url")
            try:
                altitude = float(raw["altitude_m"])
            except ValueError as error:
                raise ValueError(f"venue CSV row {line_number} has invalid altitude_m") from error
            if not math.isfinite(altitude):
                raise ValueError(f"venue CSV row {line_number} has non-finite altitude_m")
            effective_from = _parse_utc(raw["effective_from_utc"], field="effective_from_utc")
            effective_to = _parse_utc(
                raw["effective_to_utc"], field="effective_to_utc", optional=True
            )
            available_at = _parse_utc(raw["available_at_utc"], field="available_at_utc")
            assert effective_from is not None
            assert available_at is not None
            if effective_to is not None and effective_to <= effective_from:
                raise ValueError("effective_to_utc must be after effective_from_utc")
            rows.append(
                VenueRow(
                    venue_id=venue_id,
                    effective_from_utc=effective_from,
                    effective_to_utc=effective_to,
                    available_at_utc=available_at,
                    altitude_m=altitude,
                    surface_turf=_parse_bool(raw["surface_turf"], field="surface_turf"),
                    roof_capable=_parse_bool(raw["roof_capable"], field="roof_capable"),
                    source_url=source_url,
                )
            )
        if not rows:
            raise ValueError("venue CSV must contain at least one row")
        return cls(rows, hashlib.sha256(payload).hexdigest())

    def _validate_no_overlaps(self) -> None:
        by_venue: dict[str, list[VenueRow]] = {}
        for row in self.rows:
            by_venue.setdefault(row.venue_id, []).append(row)
        for venue_id, rows in by_venue.items():
            ordered = sorted(rows, key=lambda row: row.effective_from_utc)
            for previous, current in pairwise(ordered):
                if previous.effective_to_utc is None or (
                    current.effective_from_utc < previous.effective_to_utc
                ):
                    raise ValueError(f"overlapping effective rows for venue {venue_id}")

    def features(self, event: EventVersion, cutoff: datetime) -> dict[str, float]:
        if event.venue_id is None:
            raise ValueError("event must name a venue_id")
        eligible = [
            row
            for row in self.rows
            if row.venue_id == event.venue_id
            and row.available_at_utc <= cutoff
            and row.effective_from_utc <= event.kickoff_at_utc
            and (row.effective_to_utc is None or event.kickoff_at_utc < row.effective_to_utc)
        ]
        if len(eligible) != 1:
            raise ValueError("event must resolve to exactly one effective venue row")
        row = eligible[0]
        values = {
            "venue_altitude_m": float(row.altitude_m),
            "surface_turf": float(row.surface_turf),
            "roof_capable": float(row.roof_capable),
        }
        if tuple(values) != VENUE_FEATURE_NAMES:
            raise ValueError("venue feature block does not match V1 schema")
        return values
