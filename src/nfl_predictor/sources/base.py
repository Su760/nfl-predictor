from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RawResponse:
    source: str
    request_fingerprint: str
    request_started_at_utc: datetime
    response_received_at_utc: datetime
    http_status: int
    payload: bytes
    allowlisted_headers: dict[str, str]
    source_snapshot_at_utc: datetime | None = None


class SourceAdapter(Protocol):
    source: str

    def fetch(self, request: dict[str, Any]) -> RawResponse: ...


@dataclass(frozen=True)
class BuildIdentity:
    code_sha: str
    dependency_lock_sha256: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", self.code_sha) is None or set(self.code_sha) == {"0"}:
            raise ValueError("code_sha must be a nonzero 40-character Git SHA")
        if re.fullmatch(r"[0-9a-f]{64}", self.dependency_lock_sha256) is None or set(
            self.dependency_lock_sha256
        ) == {"0"}:
            raise ValueError("dependency_lock_sha256 must be a nonzero SHA-256")
