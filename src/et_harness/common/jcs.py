"""RFC 8785 JSON Canonicalization Scheme (JCS) and contract digests (§9.4).

Differences from ``hashing.canonical_json_bytes`` (kernel records): numbers
other than integers are allowed here and serialised exactly as ECMAScript's
``Number.prototype.toString`` does, as RFC 8785 §3.2.2.3 requires.

Rejected, because they have no JSON or I-JSON representation:
NaN, Infinity, integers outside ±(2**53 − 1), lone surrogates in strings,
and any non-JSON type (dates, bytes, sets, ...).
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode

MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalisationError(HarnessError):
    def __init__(self, message: str, path: str) -> None:
        super().__init__(ReasonCode.FEATURE_CONTRACT_INVALID, f"{path}: {message}", {"path": path})


def _utf16_key(key: str) -> list[int]:
    data = key.encode("utf-16-be", "surrogatepass")
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def _string(value: str, path: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise CanonicalisationError("string contains a lone surrogate", path) from None
    # json.dumps(ensure_ascii=False) matches ECMAScript JSON.stringify escaping:
    # \b \t \n \f \r \" \\ and \u00xx (lowercase hex) for other controls.
    return json.dumps(value, ensure_ascii=False)


def ecmascript_number(value: float) -> str:
    """Serialise a finite float exactly as ECMAScript Number::toString."""
    if value == 0:
        return "0"  # covers -0.0
    sign = "-" if value < 0 else ""
    # repr() gives the shortest round-trip digits, as ECMAScript requires.
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(str(d) for d in digit_tuple).rstrip("0") or "0"
    trailing = len("".join(str(d) for d in digit_tuple)) - len(digits)
    k = len(digits)
    n = exponent + trailing + k  # value = 0.digits × 10^n
    if k <= n <= 21:
        text = digits + "0" * (n - k)
    elif 0 < n <= 21:
        text = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        text = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        text = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + text


def _encode(value: Any, out: list[str], path: str) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalisationError("integer outside the I-JSON safe range", path)
        out.append(str(value))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalisationError("NaN and Infinity are not JSON", path)
        out.append(ecmascript_number(value))
    elif isinstance(value, str):
        out.append(_string(value, path))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out, f"{path}/{i}")
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalisationError(f"object key {key!r} is not a string", path)
        out.append("{")
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(key, path))
            out.append(":")
            _encode(value[key], out, f"{path}/{key}")
        out.append("}")
    else:
        raise CanonicalisationError(f"{type(value).__name__} is not a JSON type", path)


def canonicalize(value: Any) -> bytes:
    """Return the RFC 8785 canonical UTF-8 bytes of ``value``."""
    out: list[str] = []
    _encode(value, out, "")
    return "".join(out).encode("utf-8")


def contract_digest(value: Any) -> str:
    """``sha256:<hex>`` over the RFC 8785 form (§9.4)."""
    return "sha256:" + hashlib.sha256(canonicalize(value)).hexdigest()


def ensure_json_data_model(value: Any) -> None:
    """Raise ``FEATURE_CONTRACT_INVALID`` if ``value`` is outside the JSON data model."""
    canonicalize(value)
