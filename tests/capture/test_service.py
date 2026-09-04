from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from nfl_predictor.capture.service import CaptureService
from nfl_predictor.sources.base import BuildIdentity, RawResponse
from nfl_predictor.sources.odds_api import OddsApiAdapter

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class _FakeAdapter:
    source = "test_source"

    def __init__(self, payload: bytes = b'{"games":[]}') -> None:
        self.payload = payload

    def fetch(self, request: dict[str, Any]) -> RawResponse:
        return RawResponse(
            source=self.source,
            request_fingerprint="f" * 64,
            request_started_at_utc=_NOW,
            response_received_at_utc=_NOW,
            http_status=200,
            payload=self.payload,
            allowlisted_headers={},
        )


def _build_identity() -> BuildIdentity:
    return BuildIdentity(code_sha="1" * 40, dependency_lock_sha256="2" * 64)


def test_raw_response_survives_normalizer_failure(tmp_path: Path) -> None:
    def failing_normalizer(payload: bytes, manifest: object) -> None:
        raise ValueError("schema drift")

    adapter = _FakeAdapter()
    service = CaptureService(
        tmp_path,
        normalizer=failing_normalizer,
        build_identity=_build_identity(),
    )

    with pytest.raises(ValueError, match="schema drift"):
        service.capture(adapter, {"sport": "nfl"}, run_id="run-1")

    raw_files = list((tmp_path / "raw" / adapter.source).rglob("*.bin"))
    manifests = list((tmp_path / "manifests" / "captures").rglob("*.json"))
    assert [path.read_bytes() for path in raw_files] == [adapter.payload]
    assert len(manifests) == 1
    persisted = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert persisted["raw_path"] == raw_files[0].relative_to(tmp_path).as_posix()


def test_capture_writes_raw_then_manifest_before_calling_normalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_write_once = CaptureService._write_once

    def recording_write_once(destination: Path, payload: bytes) -> None:
        events.append("raw" if "raw" in destination.parts else "manifest")
        real_write_once(destination, payload)

    def normalizer(payload: bytes, manifest: object) -> None:
        events.append("normalizer")

    monkeypatch.setattr(CaptureService, "_write_once", staticmethod(recording_write_once))
    service = CaptureService(tmp_path, normalizer=normalizer, build_identity=_build_identity())

    service.capture(_FakeAdapter(), {}, run_id="run-1")

    assert events == ["raw", "manifest", "normalizer"]


def test_write_once_is_a_no_op_for_identical_bytes_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "immutable" / "capture.bin"
    CaptureService._write_once(destination, b"first")
    first_stat = destination.stat()

    CaptureService._write_once(destination, b"first")

    assert destination.stat().st_ino == first_stat.st_ino
    with pytest.raises(RuntimeError, match="immutable path conflict"):
        CaptureService._write_once(destination, b"different")
    assert destination.read_bytes() == b"first"


def test_write_once_fsyncs_each_new_ancestor_and_published_destination_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fsynced_directories: list[Path] = []
    real_fsync_directory = CaptureService._fsync_directory

    def recording_fsync(directory: Path) -> None:
        fsynced_directories.append(directory)
        real_fsync_directory(directory)

    monkeypatch.setattr(CaptureService, "_fsync_directory", staticmethod(recording_fsync))
    destination = tmp_path / "raw" / "test_source" / "ab" / "capture.bin"

    CaptureService._write_once(destination, b"payload")

    assert fsynced_directories == [
        tmp_path,
        tmp_path / "raw",
        tmp_path / "raw" / "test_source",
        tmp_path / "raw" / "test_source" / "ab",
    ]


@pytest.mark.parametrize(
    ("code_sha", "lock_sha"),
    [
        ("0" * 40, "2" * 64),
        ("A" * 40, "2" * 64),
        ("1" * 39, "2" * 64),
        ("1" * 40, "0" * 64),
        ("1" * 40, "B" * 64),
        ("1" * 40, "2" * 63),
    ],
)
def test_build_identity_rejects_invalid_or_all_zero_digests(code_sha: str, lock_sha: str) -> None:
    with pytest.raises(ValueError):
        BuildIdentity(code_sha=code_sha, dependency_lock_sha256=lock_sha)


def test_timeout_propagates_without_fabricating_capture(tmp_path: Path) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    adapter = OddsApiAdapter("never-persist-me", client, clock=lambda: _NOW)
    service = CaptureService(
        tmp_path,
        normalizer=lambda payload, manifest: None,
        build_identity=_build_identity(),
    )

    with pytest.raises(httpx.ReadTimeout, match="timed out"):
        service.capture(
            adapter,
            {"bookmakers": ["book-a"], "markets": ["h2h"]},
            run_id="run-1",
        )

    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "manifests").exists()


@pytest.mark.parametrize("source", ["../escape", "source/name", ".", ""])
def test_adapter_source_cannot_escape_raw_root(tmp_path: Path, source: str) -> None:
    adapter = _FakeAdapter()
    adapter.source = source
    service = CaptureService(
        tmp_path, normalizer=lambda payload, manifest: None, build_identity=_build_identity()
    )

    with pytest.raises(ValueError, match="source"):
        service.capture(adapter, {}, run_id="run-1")

    assert not (tmp_path.parent / "escape").exists()
