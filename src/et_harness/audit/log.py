"""Append-only, hash-chained JSON Lines audit log (§9.19).

Each entry carries the §9.19 fields, a contiguous ``sequence``, the hash of
the previous entry and its own ``entry_hash``. Editing, removing or
reordering any entry breaks the chain and is reported as
``STATE_TAMPER_DETECTED``. Removing entries from the tail is caught by the
state store, which cross-checks the state file against the last entry.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from et_harness.common.atomic import append_line_durable
from et_harness.common.errors import HarnessError
from et_harness.common.hashing import canonical_json_bytes, digest, sha256_bytes
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.redaction import Redactor

SPEC_FIELDS: tuple[str, ...] = (
    "timestamp",
    "workflow_id",
    "session_id",
    "feature_id",
    "persona",
    "agent_instance",
    "state",
    "event_type",
    "action",
    "tool",
    "package",
    "result",
    "reason_codes",
    "evidence",
    "approval_id",
)
_LIST_FIELDS = {"reason_codes", "evidence"}
GENESIS_HASH = sha256_bytes(b"et-harness-audit-genesis")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class AuditLog:
    def __init__(
        self,
        path: Path,
        *,
        redactor: Redactor | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.path = Path(path)
        self._redactor = redactor or Redactor()
        self._clock = clock

    # --- reading -------------------------------------------------------------
    def _raw_lines(self) -> Iterator[tuple[int, str]]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if line.strip():
                    yield number, line

    def entries(self) -> list[dict[str, Any]]:
        """Return all entries after verifying the full chain."""
        entries: list[dict[str, Any]] = []
        previous = GENESIS_HASH
        for number, line in self._raw_lines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise self._tamper(f"line {number} is not valid JSON") from exc
            if not isinstance(entry, dict):
                raise self._tamper(f"line {number} is not an object")
            expected_sequence = len(entries) + 1
            if entry.get("sequence") != expected_sequence:
                raise self._tamper(
                    f"line {number}: sequence {entry.get('sequence')!r}, expected {expected_sequence}"
                )
            if entry.get("previous_entry_hash") != previous:
                raise self._tamper(f"line {number}: previous_entry_hash does not chain")
            body = {k: v for k, v in entry.items() if k != "entry_hash"}
            if digest(body) != entry.get("entry_hash"):
                raise self._tamper(f"line {number}: entry_hash does not match content")
            previous = entry["entry_hash"]
            entries.append(entry)
        return entries

    def last_entry(self) -> dict[str, Any] | None:
        entries = self.entries()
        return entries[-1] if entries else None

    def next_sequence(self) -> int:
        return len(self.entries()) + 1

    # --- writing -------------------------------------------------------------
    def append(
        self,
        event_type: str,
        *,
        expected_sequence: int | None = None,
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Append one event and return the written entry.

        ``fields`` may set any §9.19 field. ``details`` holds event-specific
        data. All string content is redacted before hashing and writing.
        """
        unknown = set(fields) - set(SPEC_FIELDS)
        if unknown:
            raise HarnessError(
                ReasonCode.INTERNAL_ERROR, f"unknown audit fields: {sorted(unknown)}"
            )
        existing = self.entries()
        sequence = len(existing) + 1
        if expected_sequence is not None and expected_sequence != sequence:
            raise HarnessError(
                ReasonCode.WORKFLOW_REVISION_CONFLICT,
                f"audit sequence moved: expected {expected_sequence}, next is {sequence}",
            )
        previous = existing[-1]["entry_hash"] if existing else GENESIS_HASH

        entry: dict[str, Any] = {"sequence": sequence}
        for name in SPEC_FIELDS:
            entry[name] = [] if name in _LIST_FIELDS else None
        entry.update(fields)
        entry["timestamp"] = fields.get("timestamp") or self._clock()
        entry["event_type"] = event_type
        entry["reason_codes"] = [
            c.value if isinstance(c, ReasonCode) else str(c) for c in entry["reason_codes"]
        ]
        entry["details"] = details or {}
        entry = self._redactor.redact(entry)
        entry["sequence"] = sequence
        entry["previous_entry_hash"] = previous
        entry["entry_hash"] = digest(entry)

        line = canonical_json_bytes(entry).decode("utf-8")
        append_line_durable(self.path, line)
        return entry

    def _tamper(self, message: str) -> HarnessError:
        return HarnessError(
            ReasonCode.STATE_TAMPER_DETECTED,
            f"audit log integrity failure: {message}",
            {"audit_log": str(self.path)},
        )
