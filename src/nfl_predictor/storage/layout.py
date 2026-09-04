from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _component(value: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value or "\x00" in value:
        raise ValueError(f"invalid path component: {value!r}")
    return value


def _positive(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class StorageLayout:
    """Pure path constructors for every namespace in Specification Section 7."""

    root: Path

    def raw_capture(
        self,
        source: str,
        year: int,
        month: int,
        day: int,
        capture_id: str,
        extension: str,
    ) -> Path:
        if not 1 <= month <= 12:
            raise ValueError("month must be between 1 and 12")
        if not 1 <= day <= 31:
            raise ValueError("day must be between 1 and 31")
        if extension not in {"json", "parquet"}:
            raise ValueError("raw capture extension must be json or parquet")
        return (
            self.root
            / "raw"
            / _component(source)
            / f"{_positive(year, 'year'):04d}"
            / f"{month:02d}"
            / f"{day:02d}"
            / f"{_component(capture_id)}.{extension}"
        )

    def capture_manifest(self, year: int, month: int, capture_id: str) -> Path:
        if not 1 <= month <= 12:
            raise ValueError("month must be between 1 and 12")
        return (
            self.root
            / "manifests"
            / "captures"
            / f"{_positive(year, 'year'):04d}"
            / f"{month:02d}"
            / f"{_component(capture_id)}.json"
        )

    def normalized_events(self, season: int, filename: str) -> Path:
        return self.root / "normalized" / "events" / self._season(season) / _component(filename)

    def normalized_quotes(self, season: int, week: int, filename: str) -> Path:
        return (
            self.root
            / "normalized"
            / "quotes"
            / self._season(season)
            / self._week(week)
            / _component(filename)
        )

    def feature_snapshots(self, season: int, week: int, filename: str) -> Path:
        return (
            self.root
            / "snapshots"
            / "features"
            / self._season(season)
            / self._week(week)
            / _component(filename)
        )

    def artifact(self, artifact_id: str) -> Path:
        return self.root / "artifacts" / _component(artifact_id)

    def predictions(self, season: int, week: int, filename: str) -> Path:
        return self._weekly_ledger("predictions", season, week, filename)

    def displayed_quote_candidates(self, season: int, week: int, filename: str) -> Path:
        return self._weekly_ledger("displayed_quote_candidates", season, week, filename)

    def actual_tickets(self, season: int, filename: str) -> Path:
        return (
            self.root / "ledgers" / "actual_tickets" / self._season(season) / _component(filename)
        )

    def outcomes(self, season: int, filename: str) -> Path:
        return self.root / "outcomes" / self._season(season) / _component(filename)

    def report(self, report_version: str) -> Path:
        return self.root / "reports" / _component(report_version)

    def _weekly_ledger(self, name: str, season: int, week: int, filename: str) -> Path:
        return (
            self.root
            / "ledgers"
            / name
            / self._season(season)
            / self._week(week)
            / _component(filename)
        )

    @staticmethod
    def _season(season: int) -> str:
        return f"season={_positive(season, 'season')}"

    @staticmethod
    def _week(week: int) -> str:
        return f"week={_positive(week, 'week')}"
