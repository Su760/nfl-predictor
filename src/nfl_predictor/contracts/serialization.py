import hashlib
import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_feature_bytes(
    schema: Sequence[tuple[str, str]], values: Mapping[str, Any]
) -> bytes:
    expected = [name for name, _ in schema]
    if len(expected) != len(set(expected)):
        raise ValueError("canonical schema contains duplicate names")
    if set(values) != set(expected):
        raise ValueError("canonical values must match schema exactly")
    chunks: list[bytes] = []
    for name, kind in schema:
        encoded_name = name.encode("utf-8")
        chunks.append(struct.pack("<I", len(encoded_name)) + encoded_name)
        value = values.get(name)
        if value is None:
            chunks.append(b"N")
        elif kind == "float64":
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{name} must be finite")
            chunks.append(b"F" + struct.pack("<d", 0.0 if number == 0.0 else number))
        elif kind == "bool":
            chunks.append(b"B" + (b"\x01" if bool(value) else b"\x00"))
        elif kind == "int64":
            chunks.append(b"I" + struct.pack("<q", int(value)))
        elif kind == "string":
            payload = str(value).encode("utf-8")
            chunks.append(b"S" + struct.pack("<I", len(payload)) + payload)
        else:
            raise ValueError(f"unsupported canonical kind: {kind}")
    return b"".join(chunks)


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
