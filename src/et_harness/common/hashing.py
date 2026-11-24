"""Canonical JSON serialisation and SHA-256 digests.

HK-001 scope: canonicalisation for kernel-owned records (state and audit).
These records contain only strings, integers, booleans, nulls, lists and
objects, for which RFC 8785 reduces to: object members sorted by key, no
insignificant whitespace, UTF-8 output. Members are sorted by UTF-16 code
units as RFC 8785 requires. Floats are rejected rather than serialised
approximately; full RFC 8785 number handling for feature contracts is HK-002.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode

DIGEST_PREFIX = "sha256:"


def _utf16_key(key: str) -> list[int]:
    data = key.encode("utf-16-be")
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def _encode(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        raise HarnessError(
            ReasonCode.INTERNAL_ERROR,
            "floating-point values are not permitted in kernel-canonical records",
        )
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise HarnessError(
                    ReasonCode.INTERNAL_ERROR, "object keys must be strings"
                )
        out.append("{")
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:
        raise HarnessError(
            ReasonCode.INTERNAL_ERROR,
            f"unsupported type in canonical record: {type(value).__name__}",
        )


def canonical_json_bytes(value: Any) -> bytes:
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    """Return ``sha256:<hex>`` of the canonical JSON form of ``value``."""
    return sha256_bytes(canonical_json_bytes(value))
