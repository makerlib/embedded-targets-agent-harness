"""Sequential workflow executor (§9.15).

Supports: ordered steps, preconditions and postconditions, `when` conditions
(conditional branches), steps that need an approval (the run pauses with
APPROVAL_REQUIRED; the approval service is HK-011), per-step retry policy,
per-step timeouts (passed to the step, which must honour them), failure
propagation (stop or continue), cancellation, cleanup steps that always run,
a persisted run record, resume from the first incomplete step, and
step-level evidence.

Parallel execution and nested workflows are not supported (§9.15, §5.5).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from et_harness import API_VERSION
from et_harness.common.atomic import atomic_write_text
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document
from et_harness.tools.base import CancelToken

DONE = {"passed", "skipped_by_condition"}
FAILING = {"failed", "blocked", "inconclusive"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class StepOutcome:
    status: str  # passed | failed | blocked | inconclusive
    message: str = ""
    reason_codes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


Check = Callable[["StepContext"], tuple[bool, str]]


@dataclass
class StepContext:
    run_id: str
    step_id: str
    attempt: int
    evidence_dir: Path
    timeout_seconds: float | None
    cancel: CancelToken
    shared: dict[str, Any]


@dataclass
class RetryPolicy:
    maximum_retries: int = 0
    retry_on: frozenset[str] = frozenset({"failed", "inconclusive"})


@dataclass
class Step:
    id: str
    run: Callable[[StepContext], StepOutcome]
    preconditions: list[Check] = field(default_factory=list)
    postconditions: list[Check] = field(default_factory=list)
    when: Callable[[dict[str, Any]], bool] | None = None
    requires_approval: bool = False
    approved: Callable[[], bool] | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float | None = None
    on_failure: str = "stop"  # stop | continue


@dataclass
class RunResult:
    run_id: str
    status: str
    steps: list[dict[str, Any]]
    reason_codes: list[str]
    path: Path


class SequentialExecutor:
    def __init__(self, run_dir: Path, *, workflow_id: str | None = None, execution_profile: str | None = None,
                 cancel: CancelToken | None = None, clock: Callable[[], str] = utc_now) -> None:
        self.run_dir = Path(run_dir)
        self.workflow_id = workflow_id
        self.execution_profile = execution_profile
        self.cancel = cancel or CancelToken()
        self.clock = clock

    # --- persistence ---------------------------------------------------------------
    def _path(self, run_id: str) -> Path:
        return self.run_dir / f"{run_id}.json"

    def _save(self, record: dict[str, Any]) -> None:
        record["updated_at"] = self.clock()
        validate_document(record, "verification-run", source=record["run_id"], code=ReasonCode.INTERNAL_ERROR)
        atomic_write_text(self._path(record["run_id"]), json.dumps(record, indent=2) + "\n")

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        if not path.is_file():
            raise HarnessError(ReasonCode.STATE_NOT_FOUND, f"no run record {run_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        validate_document(record, "verification-run", source=str(path), code=ReasonCode.STATE_TAMPER_DETECTED)
        return record

    def next_run_id(self) -> str:
        existing = sorted(self.run_dir.glob("run-*.json")) if self.run_dir.is_dir() else []
        return f"run-{len(existing) + 1:03d}"

    # --- execution -----------------------------------------------------------------
    def run(self, steps: list[Step], cleanup: list[Step] = (), *, run_id: str | None = None,
            resume: bool = False, shared: dict[str, Any] | None = None) -> RunResult:
        shared = shared if shared is not None else {}
        ids = [s.id for s in steps] + [c.id for c in cleanup]
        if len(ids) != len(set(ids)):
            raise HarnessError(ReasonCode.INTERNAL_ERROR, "step IDs must be unique")
        if resume:
            if run_id is None:
                raise HarnessError(ReasonCode.INTERNAL_ERROR, "resume needs a run ID")
            record = self.load(run_id)
            if record["status"] == "completed":
                raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED, f"{run_id} already completed")
            if [s["id"] for s in record["steps"]] != ids:
                raise HarnessError(ReasonCode.STATE_TAMPER_DETECTED, f"{run_id} was recorded for other steps")
        else:
            run_id = run_id or self.next_run_id()
            if self._path(run_id).exists():
                raise HarnessError(ReasonCode.WORKFLOW_REVISION_CONFLICT, f"run {run_id} already exists")
            record = {
                "api_version": API_VERSION, "kind": "VerificationRun", "run_id": run_id,
                "workflow_id": self.workflow_id, "execution_profile": self.execution_profile,
                "status": "running", "created_at": self.clock(), "reason_codes": [],
                "steps": [{"id": s.id, "kind": "step", "status": "pending", "attempts": 0, "reason_codes": [],
                           "message": "", "evidence": [], "started_at": None, "ended_at": None} for s in steps]
                + [{"id": c.id, "kind": "cleanup", "status": "pending", "attempts": 0, "reason_codes": [],
                    "message": "", "evidence": [], "started_at": None, "ended_at": None} for c in cleanup],
            }
        record["status"] = "running"
        self._save(record)
        by_id = {s["id"]: s for s in record["steps"]}
        stop_reason: tuple[str, list[str]] | None = None
        try:
            for step in steps:
                entry = by_id[step.id]
                if entry["status"] in DONE or (entry["status"] in FAILING and step.on_failure == "continue"):
                    continue
                if stop_reason is not None:
                    entry.update(status="not_run", message="not run: an earlier step stopped the run")
                    continue
                if self.cancel.cancelled:
                    entry.update(status="cancelled", message="run cancelled before this step")
                    stop_reason = ("cancelled", [])
                    continue
                outcome = self._run_step(run_id, step, entry, shared)
                self._save(record)
                if entry["status"] == "paused_for_approval":
                    stop_reason = ("paused_for_approval", [ReasonCode.APPROVAL_REQUIRED.value])
                elif entry["status"] == "cancelled":
                    stop_reason = ("cancelled", [])
                elif entry["status"] in FAILING and step.on_failure == "stop":
                    stop_reason = ({"blocked": "blocked"}.get(entry["status"], "failed"), outcome.reason_codes)
        except KeyboardInterrupt:
            self.cancel.cancel()
            stop_reason = ("cancelled", [])
            for entry in record["steps"]:
                if entry["status"] == "running":
                    entry.update(status="cancelled", message="interrupted", ended_at=self.clock())
        finally:
            for step in cleanup:
                entry = by_id[step.id]
                entry["started_at"] = self.clock()
                try:
                    outcome = step.run(StepContext(run_id, step.id, 1, self._step_dir(run_id, step.id),
                                                   step.timeout_seconds, CancelToken(), shared))
                    entry.update(status=outcome.status, message=outcome.message,
                                 reason_codes=outcome.reason_codes, evidence=outcome.evidence)
                except Exception as exc:  # noqa: BLE001 - cleanup failures are recorded, not raised
                    entry.update(status="failed", message=f"cleanup error: {exc}")
                entry["attempts"] += 1
                entry["ended_at"] = self.clock()
            if stop_reason is None:
                any_failed = any(by_id[s.id]["status"] in FAILING for s in steps)
                record["status"] = "failed" if any_failed else "completed"
            else:
                record["status"], codes = stop_reason
                record["reason_codes"] = sorted(set(record["reason_codes"]) | set(codes))
            self._save(record)
        return RunResult(run_id, record["status"], record["steps"], record["reason_codes"], self._path(run_id))

    def _step_dir(self, run_id: str, step_id: str) -> Path:
        return self.run_dir / run_id / step_id

    def _run_step(self, run_id: str, step: Step, entry: dict[str, Any], shared: dict[str, Any]) -> StepOutcome:
        entry["started_at"] = self.clock()
        entry["status"] = "running"
        if step.when is not None and not step.when(shared):
            entry.update(status="skipped_by_condition", message="condition not met", ended_at=self.clock())
            return StepOutcome("skipped_by_condition")
        if step.requires_approval and not (step.approved and step.approved()):
            entry.update(status="paused_for_approval", reason_codes=[ReasonCode.APPROVAL_REQUIRED.value],
                         message="step requires an approval", ended_at=self.clock())
            return StepOutcome("blocked", reason_codes=[ReasonCode.APPROVAL_REQUIRED.value])
        outcome = StepOutcome("failed", "not run")
        for attempt in range(1, step.retry.maximum_retries + 2):
            ctx = StepContext(run_id, step.id, entry["attempts"] + 1, self._step_dir(run_id, step.id),
                              step.timeout_seconds, self.cancel, shared)
            for check in step.preconditions:
                ok, message = check(ctx)
                if not ok:
                    outcome = StepOutcome("blocked", f"precondition failed: {message}",
                                          [ReasonCode.GATE_PRECONDITION_FAILED.value])
                    break
            else:
                outcome = step.run(ctx)
                if outcome.status == "passed":
                    for check in step.postconditions:
                        ok, message = check(ctx)
                        if not ok:
                            outcome = StepOutcome("failed", f"postcondition failed: {message}",
                                                  outcome.reason_codes, outcome.evidence)
                            break
            entry["attempts"] += 1
            entry["evidence"] = entry["evidence"] + [e for e in outcome.evidence if e not in entry["evidence"]]
            if self.cancel.cancelled:
                outcome = StepOutcome("cancelled", "run cancelled", outcome.reason_codes, outcome.evidence)
                break
            if outcome.status not in step.retry.retry_on or outcome.status == "blocked":
                break
        entry.update(status=outcome.status, message=outcome.message, reason_codes=outcome.reason_codes,
                     ended_at=self.clock())
        shared.setdefault("outcomes", {})[step.id] = outcome.status
        return outcome
