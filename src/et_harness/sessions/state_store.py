"""Atomic, revisioned state store with an audit integrity chain (§9.5, §16).

Write protocol (audit first, then state):

1. Load and verify the current state (see *verification* below).
2. Check ``expected_revision``; a mismatch raises ``WORKFLOW_REVISION_CONFLICT``.
3. Build the new document: revision + 1, ``integrity.previous_state_hash`` =
   hash of the current document, ``integrity.audit_sequence`` = next audit
   sequence number. Validate it against the schema.
4. Append a ``state.write`` audit entry recording the new and previous hashes.
5. Atomically replace the state file.

Verification on every load: the state file must be schema-valid, and its
canonical hash must equal ``state_hash`` in the latest ``state.write`` entry
for this state key, at the sequence the document records. Anything else is
``STATE_TAMPER_DETECTED``, with one exception: if the process died between
steps 4 and 5, the file still holds exactly the previous state named by that
entry. That is a crash, not tampering: the store records a
``state.write_rolled_back`` entry and returns the valid prior state.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from et_harness.audit.log import AuditLog
from et_harness.common.atomic import atomic_write_text
from et_harness.common.errors import HarnessError
from et_harness.common.hashing import digest
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document

WRITE_EVENT = "state.write"
ROLLBACK_EVENT = "state.write_rolled_back"


class StateStore:
    def __init__(
        self,
        path: Path,
        audit: AuditLog,
        *,
        schema: str = "orchestration-state",
        state_key: str | None = None,
        revision_path: tuple[str, ...] = ("workflow", "revision"),
    ) -> None:
        self.path = Path(path)
        self.audit = audit
        self.schema = schema
        self.state_key = state_key or self.path.name
        self._revision_path = revision_path

    # --- helpers -------------------------------------------------------------
    def _revision(self, document: dict[str, Any]) -> int:
        node: Any = document
        for part in self._revision_path:
            node = node[part]
        return int(node)

    def _set_revision(self, document: dict[str, Any], value: int) -> None:
        node: Any = document
        for part in self._revision_path[:-1]:
            node = node[part]
        node[self._revision_path[-1]] = value

    def _validate(self, document: dict[str, Any]) -> None:
        validate_document(document, self.schema, source=str(self.path))

    def _write_entries(self) -> list[dict[str, Any]]:
        return [
            e for e in self.audit.entries()
            if e["event_type"] in (WRITE_EVENT, ROLLBACK_EVENT)
            and e.get("details", {}).get("state_key") == self.state_key
        ]

    def _tamper(self, message: str, **context: Any) -> HarnessError:
        return HarnessError(
            ReasonCode.STATE_TAMPER_DETECTED, message,
            {"state_file": str(self.path), "state_key": self.state_key, **context},
        )

    def _audit_fields(self, document: dict[str, Any]) -> dict[str, Any]:
        workflow = document.get("workflow", {}) if isinstance(document, dict) else {}
        return {
            "workflow_id": workflow.get("id"),
            "feature_id": workflow.get("feature_id"),
            "state": workflow.get("current_state"),
        }

    # --- public API ----------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def create(self, document: dict[str, Any]) -> dict[str, Any]:
        """Persist the first revision (0) of a new state document."""
        if self.path.exists() or self._write_entries():
            raise HarnessError(
                ReasonCode.WORKFLOW_REVISION_CONFLICT,
                f"state already exists for {self.state_key}",
                {"state_file": str(self.path)},
            )
        new = copy.deepcopy(document)
        self._set_revision(new, 0)
        return self._commit(new, previous_hash=None)

    def load(self) -> dict[str, Any]:
        """Load and verify the state; raise ``STATE_TAMPER_DETECTED`` on mismatch."""
        writes = self._write_entries()
        if not self.path.exists():
            if writes:
                raise self._tamper("state file is missing but the audit log records writes")
            raise HarnessError(
                ReasonCode.STATE_NOT_FOUND, f"no state at {self.path}", {"state_file": str(self.path)}
            )
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise self._tamper(f"state file is unreadable: {exc}") from exc
        if not writes:
            raise self._tamper("state file exists without any audit record")
        actual_hash = digest(document)
        last = writes[-1]
        details = last["details"]
        if actual_hash == details["state_hash"]:
            # The document must also be the one written at the sequence it records.
            recorded_sequence = (document.get("integrity") or {}).get("audit_sequence")
            producer = next(
                (e for e in writes if e["event_type"] == WRITE_EVENT
                 and e["sequence"] == recorded_sequence), None,
            )
            if producer is None or producer["details"]["state_hash"] != actual_hash:
                raise self._tamper("state integrity.audit_sequence does not match the audit log")
            self._validate(document)
            return document
        if last["event_type"] == WRITE_EVENT and actual_hash == details.get("previous_state_hash"):
            # Crash between audit append and state replace: prior state is intact.
            self._validate(document)
            self.audit.append(
                ROLLBACK_EVENT,
                reason_codes=[ReasonCode.STATE_WRITE_INTERRUPTED],
                result="recovered",
                details={
                    "state_key": self.state_key,
                    "state_hash": actual_hash,
                    "abandoned_state_hash": details["state_hash"],
                    "abandoned_sequence": last["sequence"],
                },
                **self._audit_fields(document),
            )
            return document
        raise self._tamper(
            "state file does not match the last audit record",
            expected_hash=details["state_hash"], actual_hash=actual_hash,
        )

    def update(
        self,
        expected_revision: int,
        mutate: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        """Apply ``mutate`` to a copy of the current state and persist it.

        ``mutate`` may modify the copy in place or return a replacement.
        Revision and integrity fields are always set by the store.
        """
        current = self.load()
        revision = self._revision(current)
        if revision != expected_revision:
            raise HarnessError(
                ReasonCode.WORKFLOW_REVISION_CONFLICT,
                f"stale revision: expected {expected_revision}, current is {revision}",
                {"expected_revision": expected_revision, "current_revision": revision},
            )
        working = copy.deepcopy(current)
        result = mutate(working)
        new = result if result is not None else working
        self._set_revision(new, revision + 1)
        return self._commit(new, previous_hash=digest(current))

    def _commit(self, new: dict[str, Any], *, previous_hash: str | None) -> dict[str, Any]:
        sequence = self.audit.next_sequence()
        new.setdefault("integrity", {})
        new["integrity"]["previous_state_hash"] = previous_hash
        new["integrity"]["audit_sequence"] = sequence
        self._validate(new)
        state_hash = digest(new)
        self.audit.append(
            WRITE_EVENT,
            expected_sequence=sequence,
            result="committed",
            details={
                "state_key": self.state_key,
                "state_hash": state_hash,
                "previous_state_hash": previous_hash,
                "revision": self._revision(new),
            },
            **self._audit_fields(new),
        )
        atomic_write_text(self.path, json.dumps(new, indent=2, sort_keys=True) + "\n")
        return new
