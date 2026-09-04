from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MARKER_FIELDS = {"namespace", "idempotency_key", "content_sha256", "object_path"}


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was already committed with different bytes."""


class DataIntegrityError(RuntimeError):
    """Committed metadata or content does not verify."""


@dataclass(frozen=True)
class AppendResult:
    created: bool
    content_sha256: str
    marker_path: Path


def _contains_any(annotation: Any) -> bool:
    return annotation is Any or any(_contains_any(item) for item in get_args(annotation))


def _validate_json_native(value: Any, path: str = "$") -> None:
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError(f"{path} must contain only finite JSON-native numbers")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_native(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} must contain only JSON-native string keys")
            _validate_json_native(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains non-JSON-native value of type {type(value).__name__}")


def _visit_nested_models(value: Any) -> None:
    if isinstance(value, BaseModel):
        _validate_model_dynamic_fields(value)
    elif isinstance(value, dict):
        for item in value.values():
            _visit_nested_models(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _visit_nested_models(item)


def _validate_model_dynamic_fields(record: BaseModel) -> None:
    for name, field in type(record).model_fields.items():
        value = getattr(record, name)
        if _contains_any(field.annotation):
            _validate_json_native(value, f"$.{name}")
        else:
            _visit_nested_models(value)


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        _validate_model_dynamic_fields(value)
        value = value.model_dump(mode="json")
    _validate_json_native(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class LedgerStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def object_path(self, digest: str) -> Path:
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("digest must be a lowercase SHA-256 hex string")
        return self.root / "objects" / digest[:2] / digest

    def marker_path(self, namespace: str, key: str) -> Path:
        self._validate_namespace(namespace)
        if not key:
            raise ValueError("idempotency key must not be empty")
        safe_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / "commits" / namespace / f"{safe_key}.json"

    def append(self, namespace: str, idempotency_key: str, record: Any) -> AppendResult:
        payload = canonical_json_bytes(record)
        digest = hashlib.sha256(payload).hexdigest()
        marker = self.marker_path(namespace, idempotency_key)
        object_path = self.object_path(digest)

        self._mkdir_durable(object_path.parent)
        object_created = self._atomic_create(object_path, payload)
        if not object_created and object_path.read_bytes() != payload:
            raise DataIntegrityError(f"object at {object_path} does not match its content hash")

        marker_payload = canonical_json_bytes(
            {
                "namespace": namespace,
                "idempotency_key": idempotency_key,
                "content_sha256": digest,
                "object_path": object_path.relative_to(self.root).as_posix(),
            }
        )
        self._mkdir_durable(marker.parent)
        created = self._atomic_create(marker, marker_payload)
        if not created:
            stored = self._read_marker(marker, namespace, idempotency_key)
            if stored["content_sha256"] != digest:
                raise IdempotencyConflict(idempotency_key)
        return AppendResult(created, digest, marker)

    def read(self, namespace: str, idempotency_key: str) -> dict[str, Any] | None:
        marker = self.marker_path(namespace, idempotency_key)
        if not marker.exists():
            return None
        metadata = self._read_marker(marker, namespace, idempotency_key)
        digest = metadata["content_sha256"]
        object_path = self.object_path(digest)
        expected_relative = object_path.relative_to(self.root).as_posix()
        if metadata["object_path"] != expected_relative:
            raise DataIntegrityError("commit marker object path does not match content hash")
        try:
            payload = object_path.read_bytes()
        except FileNotFoundError as error:
            raise DataIntegrityError("committed object is missing") from error
        if hashlib.sha256(payload).hexdigest() != digest:
            raise DataIntegrityError("committed object content hash does not match marker")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DataIntegrityError("committed object is not valid JSON") from error
        if not isinstance(value, dict):
            raise DataIntegrityError("committed ledger record must be a JSON object")
        return value

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if _NAMESPACE.fullmatch(namespace) is None:
            raise ValueError("namespace must be one safe path component")

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
            LedgerStore._fsync_directory(item.parent)

    @staticmethod
    def _atomic_create(destination: Path, payload: bytes) -> bool:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.link(temporary, destination)
            LedgerStore._fsync_directory(destination.parent)
            return True
        except FileExistsError:
            return False
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_marker(marker: Path, namespace: str, key: str) -> dict[str, str]:
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DataIntegrityError("commit marker is unreadable") from error
        if not isinstance(value, dict) or set(value) != _MARKER_FIELDS:
            raise DataIntegrityError("commit marker fields are invalid")
        if not all(isinstance(item, str) for item in value.values()):
            raise DataIntegrityError("commit marker values must be strings")
        metadata = {str(name): str(item) for name, item in value.items()}
        if metadata["namespace"] != namespace or metadata["idempotency_key"] != key:
            raise DataIntegrityError("commit marker identity does not match lookup")
        if _DIGEST.fullmatch(metadata["content_sha256"]) is None:
            raise DataIntegrityError("commit marker digest is invalid")
        return metadata
