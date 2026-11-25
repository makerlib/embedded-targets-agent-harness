"""Gate execution and immutable attempts (§9.16, §20, §21 gates/).

One call to ``GateRunner.run_gate`` produces exactly one attempt file,
``gates/<gate-id>/attempt-NNN.yaml``, written once and made read-only, with
its artefacts in ``gates/<gate-id>/attempt-NNN/``. ``aggregate.yaml`` is a
derived summary that is rewritten after each attempt.

Retry rules apply to the ``baseline`` and ``verification`` purposes within one
rework cycle: a further attempt needs a reason, and at most
``1 + maximum_retries`` attempts are allowed. Development checks and
test-design runs may be repeated freely; they never count toward the
verification level.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from et_harness import API_VERSION
from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document
from et_harness.common.yaml_io import dump_yaml, load_yaml_file
from et_harness.gates.definitions import GateDefinition, GateRegistry, evaluate_precondition
from et_harness.tools.base import CancelToken, ToolContext
from et_harness.tools.registry import SessionManager, execute_tool

PURPOSE_BY_STATE = {
    "INITIALISING": "baseline",
    "TEST_DESIGN": "test design",
    "DEVELOPING": "development check",
    "VERIFYING": "verification",
}
COUNTED_PURPOSES = ("baseline", "verification")
PROFILES = ("developer", "qe", "ci", "hardware-lab")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def check_execution_profile(profile: str) -> None:
    """ci and hardware-lab are only valid on the matching runner (§3.12, §32)."""
    if profile not in PROFILES:
        raise HarnessError(ReasonCode.CONFIG_INVALID, f"unknown execution profile {profile!r}")
    if profile == "ci" and os.environ.get("GITLAB_CI") != "true":
        raise HarnessError(ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE,
                           "the ci execution profile runs only inside GitLab CI (GITLAB_CI=true)")
    if profile == "hardware-lab" and not os.environ.get("ET_HARNESS_HARDWARE_LAB_RUNNER"):
        raise HarnessError(ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE,
                           "the hardware-lab profile needs a registered runner (ET_HARNESS_HARDWARE_LAB_RUNNER)")


def _normalise(text: str) -> str:
    text = re.sub(r"0x[0-9a-fA-F]+", "0x?", text)
    text = re.sub(r"\d+", "N", text)
    return re.sub(r"(/[^\s:]+)+", "<path>", text).strip()


def failure_signature(failing_tests: list[str], stderr: Path | None,
                      reason_codes: list[str]) -> tuple[str | None, str | None]:
    """Normalised failure signature (UC-21 MVP): stable across runs with the same cause."""
    if failing_tests:
        digest = hashlib.sha256("\n".join(sorted(failing_tests)).encode()).hexdigest()[:16]
        return f"tests:{digest}", "failing tests: " + ", ".join(sorted(failing_tests)[:3])
    if stderr is not None and stderr.is_file():
        lines = [l for l in stderr.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        tail = [_normalise(l) for l in lines[-5:]]
        if tail:
            return "stderr:" + hashlib.sha256("\n".join(tail).encode()).hexdigest()[:16], tail[-1][:200]
    if reason_codes:
        return "codes:" + "+".join(sorted(reason_codes)), None
    return None, None


@dataclass
class GateRunContext:
    repo_root: Path
    workflow: dict[str, Any]
    execution_profile: str = "developer"
    retry_reason: str | None = None
    cancel: CancelToken = field(default_factory=CancelToken)
    output_limit_bytes: int = 1_000_000
    redaction_patterns: tuple[str, ...] = ()


class GateRunner:
    def __init__(self, registry: GateRegistry, audit: AuditLog, *, sessions: SessionManager | None = None,
                 clock=utc_now) -> None:
        self.registry = registry
        self.audit = audit
        self.sessions = sessions or SessionManager()
        self.clock = clock

    # --- paths ---------------------------------------------------------------------
    @staticmethod
    def bundle_dir(repo_root: Path, workflow: dict[str, Any]) -> Path:
        wf = workflow["workflow"]
        return Path(repo_root) / ".agent-harness" / "evidence" / wf["feature_id"] / wf["id"]

    @staticmethod
    def attempts(bundle: Path, gate_id: str) -> list[dict[str, Any]]:
        gate_dir = bundle / "gates" / gate_id
        if not gate_dir.is_dir():
            return []
        return [load_yaml_file(p) for p in sorted(gate_dir.glob("attempt-*.yaml"))]

    # --- run -----------------------------------------------------------------------
    def run_gate(self, gate_id: str, ctx: GateRunContext) -> dict[str, Any]:
        check_execution_profile(ctx.execution_profile)
        wf = ctx.workflow["workflow"]
        state = wf["current_state"]
        purpose = PURPOSE_BY_STATE.get(state)
        if purpose is None:
            raise HarnessError(ReasonCode.TOOL_PERMISSION_DENIED,
                               f"gates cannot run in {state}; allowed in {', '.join(PURPOSE_BY_STATE)}")
        defn = self.registry.get(gate_id)
        bundle = self.bundle_dir(ctx.repo_root, ctx.workflow)
        previous = self.attempts(bundle, gate_id)
        cycle = ctx.workflow["rework"]["current_cycle"]
        if purpose in COUNTED_PURPOSES:
            same = [a for a in previous if a["purpose"] == purpose and a["rework_cycle"] == cycle]
            if len(same) > defn.maximum_retries:
                raise HarnessError(ReasonCode.RETRY_LIMIT_EXCEEDED,
                                   f"{gate_id}: retry limit reached ({defn.maximum_retries} retr"
                                   f"{'y' if defn.maximum_retries == 1 else 'ies'} per cycle)")
            if same and not (ctx.retry_reason or "").strip():
                raise HarnessError(ReasonCode.GATE_PRECONDITION_FAILED,
                                   f"{gate_id} already has {len(same)} {purpose} attempt(s) in cycle {cycle}; "
                                   "a retry needs --retry-reason (spec §20 require_retry_reason)")
        number = len(previous) + 1
        attempt_dir = bundle / "gates" / gate_id / f"attempt-{number:03d}"
        if attempt_dir.exists():  # a crashed run left artefacts without an attempt record
            attempt_dir.rename(attempt_dir.with_name(f"{attempt_dir.name}.incomplete-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"))
        attempt_dir.mkdir(parents=True)
        start = self.clock()
        reason_codes: list[str] = []
        invocations: list[dict[str, Any]] = []
        evidence: dict[str, Path] = {}
        test_counts = None
        failing: list[str] = []
        stderr_path = stdout_path = None
        result: str | None = None
        precondition_records = []

        # dependencies
        for dep in defn.depends_on:
            dep_attempts = [a for a in self.attempts(bundle, dep) if a["purpose"] == purpose
                            and a["rework_cycle"] == cycle]
            if not dep_attempts or dep_attempts[-1]["result"] != "PASSED":
                status = dep_attempts[-1]["result"] if dep_attempts else "not run"
                precondition_records.append({"id": f"dependency:{dep}", "status": "failed",
                                             "message": f"{dep} is {status} for {purpose}"})
                result = "BLOCKED"
        # preconditions (never skipped)
        for raw in defn.preconditions:
            pre = evaluate_precondition(raw, repo_root=ctx.repo_root, workflow=ctx.workflow)
            precondition_records.append({"id": pre.id, "status": "passed" if pre.ok else "failed",
                                         "message": pre.message})
            if not pre.ok:
                result = "BLOCKED"
        if result == "BLOCKED":
            reason_codes.append(ReasonCode.GATE_PRECONDITION_FAILED.value)

        persona = self._persona(state, wf["profile"])
        if result is None:
            for action in defn.actions:
                if "when" in action and not evaluate_precondition(
                        action["when"], repo_root=ctx.repo_root, workflow=ctx.workflow).ok:
                    invocations.append({"action_id": action["id"], "tool": action["tool"],
                                        "execution_mode": action.get("execution_mode", "process"),
                                        "exit_code": None, "duration_ms": 0, "skipped_by_condition": True})
                    continue
                tool = self.registry.tools.get(action["tool"])
                tool_ctx = ToolContext(
                    repo_root=ctx.repo_root, attempt_dir=attempt_dir, evidence_dir=bundle,
                    timeout_seconds=defn.timeout_for(action, ctx.execution_profile),
                    execution_profile=ctx.execution_profile, output_limit_bytes=ctx.output_limit_bytes,
                    redaction_patterns=ctx.redaction_patterns, cancel=ctx.cancel, workflow=ctx.workflow)
                try:
                    tr = execute_tool(tool, action.get("inputs") or {}, tool_ctx, self.sessions)
                except HarnessError as exc:
                    tr = None
                    error = exc.message
                    reason_codes.append(exc.code.value)
                except Exception as exc:  # noqa: BLE001 - a tool crash is a recorded INCONCLUSIVE
                    tr = None
                    error = f"{type(exc).__name__}: {exc}"
                if tr is None:
                    invocations.append({"action_id": action["id"], "tool": action["tool"],
                                        "execution_mode": tool.describe().execution_mode,
                                        "exit_code": None, "duration_ms": 0, "error": error})
                    result = "INCONCLUSIVE"
                    break
                invocations.append({
                    "action_id": action["id"], "tool": action["tool"],
                    "execution_mode": tool.describe().execution_mode, "exit_code": tr.exit_code,
                    "duration_ms": tr.duration_ms, "timed_out": tr.timed_out,
                    "stdout_path": self._rel(tr.stdout_path, bundle), "stderr_path": self._rel(tr.stderr_path, bundle),
                    "stdout_truncated": tr.stdout_truncated, "stderr_truncated": tr.stderr_truncated,
                    "error": tr.error,
                })
                self.audit.append("tool.executed", workflow_id=wf["id"], feature_id=wf["feature_id"],
                                  persona=persona, state=state, tool=action["tool"], action=f"{gate_id}/{action['id']}",
                                  result="timeout" if tr.timed_out else ("cancelled" if tr.cancelled else
                                         ("error" if tr.error else str(tr.exit_code))),
                                  details={"exit_code": tr.exit_code, "duration_ms": tr.duration_ms,
                                           **({"tests": tr.test_counts} if tr.test_counts else {})})
                evidence.update(tr.evidence)
                stdout_path, stderr_path = tr.stdout_path or stdout_path, tr.stderr_path or stderr_path
                if tr.test_counts is not None:
                    test_counts = tr.test_counts
                    failing = tr.failing_tests
                if tr.error:
                    reason_codes.append(ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE.value)
                    result = "BLOCKED"
                    break
                if tr.timed_out:
                    reason_codes.append(ReasonCode.GATE_TIMEOUT.value)
                    result = "INCONCLUSIVE"
                    break
                if tr.cancelled:
                    result = "INCONCLUSIVE"
                    break

        if test_counts is not None:
            counts_file = attempt_dir / "test-counts.json"
            counts_file.write_text(json.dumps(test_counts, indent=2) + "\n", encoding="utf-8")
            evidence["test-counts"] = counts_file

        baseline_counts = ((ctx.workflow.get("initialisation") or {}).get("baseline_test_counts") or {}).get(gate_id)
        conditions = self._conditions(defn, invocations, evidence, test_counts, baseline_counts, purpose)
        if result is None:
            failed_conditions = [c for c in conditions if c["status"] == "failed"]
            result = "FAILED" if failed_conditions else "PASSED"
            if any(c["id"] == "all_required_evidence_present" and c["status"] == "failed" for c in conditions):
                reason_codes.append(ReasonCode.GATE_EVIDENCE_MISSING.value)
        signature = summary = None
        if result != "PASSED":
            signature, summary = failure_signature(failing, stderr_path, reason_codes)

        artefacts = [{"evidence_id": eid, "path": self._rel(p, bundle), "sha256": sha256_file(p),
                      "bytes": p.stat().st_size} for eid, p in sorted(evidence.items()) if p.is_file()]
        exit_codes = [i["exit_code"] for i in invocations if not i.get("skipped_by_condition")]
        attempt = {
            "api_version": API_VERSION, "kind": "GateAttempt", "gate_id": gate_id, "gate_version": defn.version,
            "level": defn.level, "attempt": number, "workflow_id": wf["id"], "feature_id": wf["feature_id"],
            "execution_profile": ctx.execution_profile, "purpose": purpose, "authority": "advisory",
            "rework_cycle": cycle, "start_time": start, "end_time": self.clock(),
            "tool_invocations": invocations,
            "exit_code": exit_codes[-1] if exit_codes else None,
            "test_counts": test_counts, "result": result, "reason_codes": sorted(set(reason_codes)),
            "conditions": conditions, "preconditions": precondition_records, "artefacts": artefacts,
            "stdout_path": self._rel(stdout_path, bundle), "stderr_path": self._rel(stderr_path, bundle),
            "log_hash": sha256_file(stdout_path) if stdout_path and stdout_path.is_file() else None,
            "failure_signature": signature, "failure_summary": summary,
            "retry_reason": ctx.retry_reason if number > 1 else None,
            "baseline_test_counts": baseline_counts,
        }
        validate_document(attempt, "gate-result", source=f"{gate_id} attempt {number}", code=ReasonCode.INTERNAL_ERROR)
        attempt_file = bundle / "gates" / gate_id / f"attempt-{number:03d}.yaml"
        self._write_once(attempt_file, dump_yaml(attempt))
        for path in attempt_dir.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        self._write_aggregate(bundle, defn)
        details: dict[str, Any] = {"level": defn.level, "attempt": number, "execution_profile": ctx.execution_profile,
                                   "purpose": purpose}
        if test_counts is not None:
            details["tests"] = test_counts
        if signature:
            details["failure_signature"] = signature
        self.audit.append("gate.attempt", workflow_id=wf["id"], feature_id=wf["feature_id"], persona=persona,
                          state=state, action=gate_id, tool=defn.actions[0]["tool"], result=result,
                          reason_codes=sorted(set(reason_codes)), evidence=[self._rel(attempt_file, bundle)],
                          details=details)
        return attempt

    # --- helpers -------------------------------------------------------------------
    @staticmethod
    def _persona(state: str, profile: str) -> str:
        from et_harness.orchestration.states import State, owner

        return owner(State(state), profile)

    @staticmethod
    def _rel(path: Path | None, bundle: Path) -> str | None:
        if path is None:
            return None
        try:
            return path.relative_to(bundle).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _write_once(path: Path, text: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    @staticmethod
    def _conditions(defn: GateDefinition, invocations, evidence, counts, baseline, purpose) -> list[dict[str, Any]]:
        out = []
        for cond in defn.pass_conditions:
            if cond == "action_exit_code_zero":
                ran = [i for i in invocations if not i.get("skipped_by_condition")]
                ok = bool(ran) and all(i["exit_code"] == 0 for i in ran)
                out.append({"id": cond, "status": "passed" if ok else "failed",
                            "message": f"exit codes {[i['exit_code'] for i in ran]}"})
            elif cond == "all_required_evidence_present":
                missing = [e for e in defn.required_evidence
                           if e not in evidence or not evidence[e].is_file() or evidence[e].stat().st_size == 0]
                out.append({"id": cond, "status": "failed" if missing else "passed",
                            "message": f"missing: {missing}" if missing else "all present"})
            elif cond == "zero_failed_tests":
                if counts is None:
                    out.append({"id": cond, "status": "failed", "message": "no test counts captured"})
                else:
                    ok = counts["failed"] == 0 and counts["run"] > 0
                    out.append({"id": cond, "status": "passed" if ok else "failed",
                                "message": f"{counts['failed']} failed of {counts['run']} run"})
            elif cond == "test_count_not_below_baseline":
                if purpose == "baseline" or baseline is None:
                    out.append({"id": cond, "status": "not_applicable",
                                "message": "baseline run" if purpose == "baseline" else "no baseline count recorded"})
                elif counts is None:
                    out.append({"id": cond, "status": "failed", "message": "no test counts captured"})
                else:
                    ok = counts["run"] >= baseline["run"]
                    out.append({"id": cond, "status": "passed" if ok else "failed",
                                "message": f"{counts['run']} run vs baseline {baseline['run']}"})
        return out

    def _write_aggregate(self, bundle: Path, defn: GateDefinition) -> None:
        attempts = self.attempts(bundle, defn.id)
        counted = [a for a in attempts if a["purpose"] in COUNTED_PURPOSES]
        latest = (counted or attempts)[-1]
        aggregate = {
            "gate_id": defn.id, "level": defn.level, "attempts": len(attempts),
            "result": latest["result"], "latest_attempt": latest["attempt"], "latest_purpose": latest["purpose"],
            "passed": latest["result"] == "PASSED" and latest["purpose"] in COUNTED_PURPOSES,
            "history": [{"attempt": a["attempt"], "purpose": a["purpose"], "rework_cycle": a["rework_cycle"],
                         "result": a["result"], "execution_profile": a["execution_profile"]} for a in attempts],
        }
        path = bundle / "gates" / defn.id / "aggregate.yaml"
        if path.exists():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        path.write_text(dump_yaml(aggregate), encoding="utf-8")
