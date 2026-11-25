"""A verification run: required gates through the sequential executor (§9.15, §9.16).

Each gate is one executor step; a failed gate does not stop the run (§14.8:
a failed gate still reaches EVALUATING). Session-scoped tools are shared
across consecutive gate actions and stopped in a cleanup step, which runs on
completion, failure and cancellation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.gates.definitions import GateRegistry
from et_harness.gates.runner import GateRunContext, GateRunner, check_execution_profile
from et_harness.tools.base import CancelToken
from et_harness.tools.registry import SessionManager
from et_harness.workflows.executor import SequentialExecutor, Step, StepContext, StepOutcome

STATUS = {"PASSED": "passed", "FAILED": "failed", "BLOCKED": "blocked", "INCONCLUSIVE": "inconclusive"}


@dataclass
class VerificationResult:
    run_id: str
    status: str
    attempts: dict[str, dict[str, Any]]
    sessions_started: list[str]
    sessions_stopped: list[str]
    reason_codes: list[str]


def run_verification(
    gate_ids: list[str],
    *,
    repo_root: Path,
    workflow: dict[str, Any],
    registry: GateRegistry,
    audit: AuditLog,
    execution_profile: str = "developer",
    retry_reason: str | None = None,
    resume_run: str | None = None,
    cancel: CancelToken | None = None,
    output_limit_bytes: int = 1_000_000,
    redaction_patterns: tuple[str, ...] = (),
) -> VerificationResult:
    check_execution_profile(execution_profile)
    cancel = cancel or CancelToken()
    sessions = SessionManager()
    runner = GateRunner(registry, audit, sessions=sessions)
    bundle = GateRunner.bundle_dir(repo_root, workflow)
    ctx = GateRunContext(repo_root=Path(repo_root), workflow=workflow, execution_profile=execution_profile,
                         retry_reason=retry_reason, cancel=cancel, output_limit_bytes=output_limit_bytes,
                         redaction_patterns=redaction_patterns)
    attempts: dict[str, dict[str, Any]] = {}

    def gate_step(gate_id: str) -> Step:
        def run(step_ctx: StepContext) -> StepOutcome:
            try:
                attempt = runner.run_gate(gate_id, ctx)
            except HarnessError as exc:  # refused before an attempt existed (retry limit, permission)
                return StepOutcome("blocked", exc.message, [exc.code.value])
            attempts[gate_id] = attempt
            return StepOutcome(STATUS[attempt["result"]], f"{gate_id} attempt {attempt['attempt']}: {attempt['result']}",
                               list(attempt["reason_codes"]),
                               [f"gates/{gate_id}/attempt-{attempt['attempt']:03d}.yaml"])
        return Step(id=gate_id, run=run, on_failure="continue")

    def stop_sessions(_: StepContext) -> StepOutcome:
        errors = sessions.stop_all()
        return StepOutcome("failed" if errors else "passed", "; ".join(errors) or
                           f"stopped sessions: {', '.join(sessions.stopped) or 'none'}")

    executor = SequentialExecutor(bundle / "runs", workflow_id=workflow["workflow"]["id"],
                                  execution_profile=execution_profile, cancel=cancel)
    ordered = registry.order(gate_ids)
    result = executor.run([gate_step(g) for g in ordered], [Step(id="stop-sessions", run=stop_sessions)],
                          run_id=resume_run, resume=resume_run is not None)
    return VerificationResult(result.run_id, result.status, attempts, list(sessions.started),
                              list(sessions.stopped), result.reason_codes)
