"""Kernel-only workflow transitions over the integrity-chained state store (§9.5, §15, §16).

Every transition:

1. verifies the state file against the audit log (StateStore.load);
2. checks the expected revision and the §15 table;
3. writes the new state (revision + 1, append-only history with owner);
4. appends a ``state.transition`` audit event, plus ``blocker.raised`` or
   ``blocker.resolved`` when a blocker changes.

A crash between 3 and 4 leaves a valid state whose transition event is
missing; the history entry in the state file still records it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode, is_emittable
from et_harness.orchestration.states import FINAL, State, check_transition, owner
from et_harness.sessions.state_store import StateStore

Mutator = Callable[[dict[str, Any]], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _codes(reason_codes: list[ReasonCode | str]) -> list[str]:
    out = []
    for code in reason_codes:
        try:
            rc = code if isinstance(code, ReasonCode) else ReasonCode(code)
        except ValueError:
            raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED, f"unknown reason code {code!r}") from None
        if not is_emittable(rc):
            raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED, f"reason code {rc.value} is reserved")
        if rc.value not in out:
            out.append(rc.value)
    return out


class WorkflowEngine:
    def __init__(self, store: StateStore, audit: AuditLog, *, clock: Callable[[], str] = utc_now) -> None:
        self.store = store
        self.audit = audit
        self.clock = clock

    def _event(self, doc: dict[str, Any], event_type: str, **fields: Any) -> None:
        wf = doc["workflow"]
        self.audit.append(event_type, workflow_id=wf["id"], feature_id=wf["feature_id"],
                          persona="kernel", state=wf["current_state"], **fields)

    def transition(
        self,
        expected_revision: int,
        target: State,
        reason: str,
        *,
        reason_codes: list[ReasonCode | str] = (),
        message: str = "",
        resume_state: State | None = None,
        tamper: bool = False,
        extra: Mutator | None = None,
    ) -> dict[str, Any]:
        current = self.store.load()
        wf = current["workflow"]
        if wf["revision"] != expected_revision:
            raise HarnessError(ReasonCode.WORKFLOW_REVISION_CONFLICT,
                               f"stale revision: expected {expected_revision}, current is {wf['revision']}",
                               {"expected_revision": expected_revision, "current_revision": wf["revision"]})
        source = State(wf["current_state"])
        resumable = State(wf["resumable_state"]) if wf.get("resumable_state") else None
        check_transition(source, target, reason, profile=wf["profile"],
                         qe_test_design=wf.get("qe_test_design", "off"), resumable=resumable, tamper=tamper)
        codes = _codes(list(reason_codes))
        if target is State.BLOCKED and not codes:
            raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED, "blocking requires at least one §27 reason code")
        now = self.clock()

        def mutate(doc: dict[str, Any]) -> None:
            w = doc["workflow"]
            w["previous_state"], w["current_state"] = source.value, target.value
            if target is State.BLOCKED:
                resume_to = resume_state or source
                doc["blocker"] = {"reason_code": codes[0], "reason_codes": codes, "message": message,
                                  "resume_state": resume_to.value, "from_state": source.value,
                                  "raised_at": now}
                w["resumable_state"] = resume_to.value
            elif target is State.INCONCLUSIVE:
                w["resumable_state"] = State.VERIFYING.value
            elif target in FINAL:
                w["resumable_state"] = None
            else:
                w["resumable_state"] = target.value
            if source is State.BLOCKED and target is not State.BLOCKED:
                doc["blocker"] = None
            if target is State.REWORK_PLANNING:
                doc["rework"]["current_cycle"] += 1
            if doc["ownership"].get("lease_id") is None:
                doc["ownership"]["persona"] = owner(target, w["profile"])
            doc["history"].append({
                "sequence": len(doc["history"]) + 1, "from": source.value, "to": target.value,
                "reason": reason, "revision": w["revision"] + 1,
                "owner": owner(target, w["profile"]), "at": now,
            })
            if extra is not None:
                extra(doc)

        new = self.store.update(expected_revision, mutate)
        self._event(new, "state.transition", action=f"{source.value} -> {target.value}", result="ok",
                    reason_codes=codes if target is State.BLOCKED else [],
                    details={"from": source.value, "to": target.value, "reason": reason})
        if target is State.BLOCKED:
            self._event(new, "blocker.raised", reason_codes=codes,
                        details={"resume_state": new["workflow"]["resumable_state"], "message": message})
        elif source is State.BLOCKED:
            self._event(new, "blocker.resolved",
                        details={"resume_state": target.value, "message": message})
        return new

    def replace_blocker(self, expected_revision: int, reason_codes: list[ReasonCode | str],
                        message: str) -> dict[str, Any]:
        """Record a new blocker on an already BLOCKED workflow (no transition)."""
        codes = _codes(list(reason_codes))
        now = self.clock()

        def mutate(doc: dict[str, Any]) -> None:
            if doc["workflow"]["current_state"] != State.BLOCKED.value:
                raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED, "workflow is not BLOCKED")
            blocker = dict(doc["blocker"] or {})
            merged = list(dict.fromkeys(codes + list(blocker.get("reason_codes") or [])))
            blocker.update(reason_code=codes[0], reason_codes=merged, message=message, raised_at=now)
            blocker.setdefault("resume_state", doc["workflow"]["resumable_state"])
            doc["blocker"] = blocker

        new = self.store.update(expected_revision, mutate)
        self._event(new, "blocker.raised", reason_codes=codes,
                    details={"resume_state": new["workflow"]["resumable_state"], "message": message})
        return new
