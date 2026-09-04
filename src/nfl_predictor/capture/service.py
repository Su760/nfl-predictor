from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.contracts.serialization import sha256_hex
from nfl_predictor.sources.base import BuildIdentity, SourceAdapter
from nfl_predictor.storage.ledger import canonical_json_bytes


class CaptureService:
    def __init__(
        self,
        data_root: Path,
        normalizer: Callable[[bytes, CaptureManifest], Any],
        build_identity: BuildIdentity,
    ) -> None:
        self.data_root = data_root
        self.normalizer = normalizer
        self.build_identity = build_identity

    @staticmethod
    def _write_once(destination: Path, payload: bytes) -> None:
        CaptureService._mkdir_durable(destination.parent)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise RuntimeError(f"immutable path conflict: {destination}")
            return

        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            try:
                os.link(temporary, destination)
                CaptureService._fsync_directory(destination.parent)
            except FileExistsError:
                if destination.read_bytes() != payload:
                    raise RuntimeError(f"immutable path conflict: {destination}") from None
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _mkdir_durable(directory: Path) -> None:
        missing: list[Path] = []
        existing = directory
        while not existing.exists():
            missing.append(existing)
            existing = existing.parent
        if not existing.is_dir():
            raise NotADirectoryError(existing)
        for item in reversed(missing):
            try:
                item.mkdir()
            except FileExistsError:
                if not item.is_dir():
                    raise
            CaptureService._fsync_directory(item.parent)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def capture(
        self,
        adapter: SourceAdapter,
        request: dict[str, Any],
        run_id: str,
    ) -> CaptureManifest:
        source = self._safe_source(adapter.source)
        response = adapter.fetch(request)
        capture_id = str(uuid4())
        digest = sha256_hex(response.payload)
        raw_path = self.data_root / "raw" / source / digest[:2] / f"{digest}.bin"
        self._write_once(raw_path, response.payload)
        manifest = CaptureManifest(
            capture_id=capture_id,
            run_id=run_id,
            source=source,
            request_fingerprint=response.request_fingerprint,
            request_started_at_utc=response.request_started_at_utc,
            response_received_at_utc=response.response_received_at_utc,
            http_status=response.http_status,
            raw_path=raw_path.relative_to(self.data_root).as_posix(),
            raw_payload_sha256=digest,
            response_headers_allowlisted=response.allowlisted_headers,
            source_snapshot_at_utc=response.source_snapshot_at_utc,
            code_sha=self.build_identity.code_sha,
            dependency_lock_sha256=self.build_identity.dependency_lock_sha256,
            schema_version="capture-v1",
        )
        manifest_path = self.data_root / "manifests" / "captures" / f"{capture_id}.json"
        self._write_once(manifest_path, canonical_json_bytes(manifest.model_dump(mode="json")))
        self.normalizer(response.payload, manifest)
        return manifest

    @staticmethod
    def _safe_source(source: str) -> str:
        if not source or source in {".", ".."} or Path(source).name != source or "\x00" in source:
            raise ValueError("adapter source must be one safe path component")
        return source
