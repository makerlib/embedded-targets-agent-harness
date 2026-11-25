"""HK-004 AC-003..AC-007: gate execution, attempts, profiles, tools, test counts."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validation_errors
from et_harness.gates.definitions import GateRegistry
from et_harness.gates.runner import GateRunContext, GateRunner
from et_harness.gates.verify import run_verification
from et_harness.sessions.documents import new_orchestration_state
from et_harness.tools.base import CancelToken, ToolDescriptor, ToolResult
from et_harness.tools.registry import default_registry

SUITE = Path(__file__).resolve().parents[2] / "fixtures" / "gates" / "synthetic_suite.py"


def workflow(state: str = "VERIFYING", profile: str = "full", baseline: dict | None = None) -> dict:
    doc = new_orchestration_state(workflow_id="workflow-F-001-001", feature_id="F-001",
                                  contract_path="features/active/F-001.yaml", profile=profile)
    doc["workflow"]["current_state"] = state
    if baseline:
        doc["initialisation"] = {"checks": [], "baseline_test_counts": baseline}
    return doc


def gate_def(gate_id: str, argv: list[str] | None = None, **extra: Any) -> dict:
    action = {"id": "run", "tool": "process.command", "timeout_seconds": extra.pop("timeout", 30),
              "inputs": {"argv": argv or ["{python}", str(SUITE), "--junit", "{attempt_dir}/junit.xml",
                                          "--repo", "{repo}"], "junit": "{attempt_dir}/junit.xml"}}
    data = {"api_version": "embedded-targets.ai/v1", "kind": "VerificationGate",
            "gate": {"id": gate_id, "version": "1.0.0", "name": gate_id, "level": extra.pop("level", "L1")},
            "actions": extra.pop("actions", [action]),
            "evidence": {"required": extra.pop("required", ["junit-results", "test-counts"])},
            "pass_conditions": extra.pop("conditions", ["action_exit_code_zero", "zero_failed_tests",
                                                        "test_count_not_below_baseline",
                                                        "all_required_evidence_present"]),
            "failure_policy": {"maximum_retries": extra.pop("retries", 1)}}
    if "depends_on" in extra:
        data["gate"]["depends_on"] = extra.pop("depends_on")
    data.update(extra)
    return data


class Env:
    def __init__(self, tmp_path: Path, tools=None) -> None:
        self.root = tmp_path / "repo"
        self.root.mkdir()
        self.gates = tmp_path / "gates"
        self.gates.mkdir()
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.tools = tools or default_registry()

    def add(self, data: dict) -> None:
        (self.gates / f"{data['gate']['id']}.yaml").write_text(yaml.safe_dump(data))

    def registry(self) -> GateRegistry:
        return GateRegistry.load(None, tools=self.tools, extra_dirs=[self.gates])

    def run(self, gate_id: str, doc: dict | None = None, **ctx) -> dict:
        runner = GateRunner(self.registry(), self.audit)
        return runner.run_gate(gate_id, GateRunContext(self.root, doc or workflow(), **ctx))

    def bundle(self, doc: dict | None = None) -> Path:
        return GateRunner.bundle_dir(self.root, doc or workflow())


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Env:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("ET_HARNESS_HARDWARE_LAB_RUNNER", raising=False)
    return Env(tmp_path)


# --- AC-003 execution ------------------------------------------------------------------

def test_passing_gate_records_schema_valid_attempt(env: Env) -> None:
    env.add(gate_def("unit"))
    attempt = env.run("unit")
    assert attempt["result"] == "PASSED" and attempt["attempt"] == 1
    assert attempt["test_counts"] == {"run": 3, "passed": 3, "failed": 0, "skipped": 0}
    assert attempt["authority"] == "advisory" and attempt["purpose"] == "verification"
    assert validation_errors(attempt, "gate-result") == []
    ids = {a["evidence_id"] for a in attempt["artefacts"]}
    assert {"junit-results", "test-counts", "stdout", "stderr"} <= ids
    for entry in env.audit.entries():
        assert validation_errors(entry, "audit-record") == [], entry
    assert [e["event_type"] for e in env.audit.entries()] == ["tool.executed", "gate.attempt"]


def test_failing_tests_fail_gate_with_signature(env: Env) -> None:
    env.add(gate_def("unit", argv=["{python}", str(SUITE), "--junit", "{attempt_dir}/junit.xml", "--repo",
                                   "{repo}", "--require", "src/feature.c"]))
    attempt = env.run("unit")
    assert attempt["result"] == "FAILED"
    assert attempt["failure_signature"].startswith("tests:")
    assert "requires src/feature.c" in attempt["failure_summary"]
    again = env.run("unit", retry_reason="re-run after infrastructure check")
    assert again["failure_signature"] == attempt["failure_signature"]


def test_precondition_failure_blocks_without_running(env: Env) -> None:
    env.add(gate_def("unit", preconditions=[{"command_available": "matlab-not-installed-xyz"}]))
    attempt = env.run("unit")
    assert attempt["result"] == "BLOCKED"
    assert attempt["reason_codes"] == ["GATE_PRECONDITION_FAILED"]
    assert attempt["tool_invocations"] == []
    assert [e["event_type"] for e in env.audit.entries()] == ["gate.attempt"]


def test_dependency_not_passed_blocks(env: Env) -> None:
    env.add(gate_def("base", argv=["{python}", "-c", "import sys; sys.exit(1)"], required=[],
                     conditions=["action_exit_code_zero"]))
    env.add(gate_def("unit", depends_on=["base"]))
    assert env.run("unit")["result"] == "BLOCKED"  # base not run yet
    assert env.run("base")["result"] == "FAILED"
    blocked = env.run("unit", retry_reason="base now attempted")
    assert blocked["result"] == "BLOCKED" and "base is FAILED" in blocked["preconditions"][0]["message"]


def test_missing_required_evidence_prevents_pass(env: Env) -> None:
    env.add(gate_def("unit", argv=["{python}", "-c", "print('no junit written')"]))
    attempt = env.run("unit")
    assert attempt["result"] == "FAILED"
    assert "GATE_EVIDENCE_MISSING" in attempt["reason_codes"]
    statuses = {c["id"]: c["status"] for c in attempt["conditions"]}
    assert statuses["all_required_evidence_present"] == "failed" and statuses["zero_failed_tests"] == "failed"


def test_timeout_kills_process_tree(env: Env, tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = ("import subprocess, sys, time;"
              f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
              f"open({str(pid_file)!r}, 'w').write(str(p.pid)); time.sleep(60)")
    env.add(gate_def("slow", argv=["{python}", "-c", script], timeout=2))
    start = time.monotonic()
    attempt = env.run("slow")
    assert time.monotonic() - start < 20
    assert attempt["result"] == "INCONCLUSIVE" and "GATE_TIMEOUT" in attempt["reason_codes"]
    assert attempt["tool_invocations"][0]["timed_out"] is True
    grandchild = int(pid_file.read_text())
    time.sleep(0.2)
    assert not process_alive(grandchild)


def process_alive(pid: int) -> bool:
    """False if gone or a zombie (killed, waiting for a parent that may never reap it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        return "State:\tZ" not in status.read_text()
    return True


def test_missing_executable_blocks(env: Env) -> None:
    env.add(gate_def("tool", argv=["definitely-not-a-real-binary-xyz"], required=[],
                     conditions=["action_exit_code_zero"]))
    attempt = env.run("tool")
    assert attempt["result"] == "BLOCKED" and "MANDATORY_DEPENDENCY_UNAVAILABLE" in attempt["reason_codes"]


def test_when_condition_skips_action(env: Env) -> None:
    actions = [{"id": "only-if", "tool": "process.command", "timeout_seconds": 5, "when": {"file_exists": "nope"},
                "inputs": {"argv": ["{python}", "-c", "import sys; sys.exit(3)"]}},
               {"id": "always", "tool": "process.command", "timeout_seconds": 5,
                "inputs": {"argv": ["{python}", "-c", "pass"]}}]
    env.add(gate_def("cond", actions=actions, required=[], conditions=["action_exit_code_zero"]))
    attempt = env.run("cond")
    assert attempt["result"] == "PASSED"
    assert attempt["tool_invocations"][0]["skipped_by_condition"] is True


def test_gates_not_allowed_in_other_states(env: Env) -> None:
    env.add(gate_def("unit"))
    for state in ("CREATED", "PLANNING", "EVALUATING", "PASSED"):
        with pytest.raises(HarnessError) as info:
            env.run("unit", workflow(state))
        assert info.value.code is ReasonCode.TOOL_PERMISSION_DENIED


# --- AC-004 attempts and retries ---------------------------------------------------------

def test_attempt_files_are_write_once_and_read_only(env: Env) -> None:
    env.add(gate_def("unit"))
    env.run("unit")
    path = env.bundle() / "gates" / "unit" / "attempt-001.yaml"
    assert not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    for artefact in (env.bundle() / "gates" / "unit" / "attempt-001").iterdir():
        assert not artefact.stat().st_mode & stat.S_IWUSR
    with pytest.raises(FileExistsError):
        GateRunner._write_once(path, "forged")


def test_retry_requires_reason_and_limit(env: Env) -> None:
    env.add(gate_def("unit", retries=1))
    env.run("unit")
    with pytest.raises(HarnessError) as info:
        env.run("unit")
    assert info.value.code is ReasonCode.GATE_PRECONDITION_FAILED and "retry-reason" in info.value.message
    assert env.run("unit", retry_reason="flaky licence server")["retry_reason"] == "flaky licence server"
    with pytest.raises(HarnessError) as info:
        env.run("unit", retry_reason="third time")
    assert info.value.code is ReasonCode.RETRY_LIMIT_EXCEEDED


def test_new_rework_cycle_resets_retry_budget(env: Env) -> None:
    env.add(gate_def("unit", retries=0))
    env.run("unit")
    doc = workflow()
    doc["rework"]["current_cycle"] = 1
    assert env.run("unit", doc)["attempt"] == 2


def test_development_checks_are_unlimited(env: Env) -> None:
    env.add(gate_def("unit", retries=0))
    for _ in range(3):
        assert env.run("unit", workflow("DEVELOPING"))["purpose"] == "development check"


def test_aggregate_reflects_attempts(env: Env) -> None:
    env.add(gate_def("unit", argv=["{python}", str(SUITE), "--junit", "{attempt_dir}/junit.xml", "--repo",
                                   "{repo}", "--require", "done.txt"]))
    assert env.run("unit")["result"] == "FAILED"
    (env.root / "done.txt").write_text("x")
    assert env.run("unit", retry_reason="implementation fixed")["result"] == "PASSED"
    aggregate = yaml.safe_load((env.bundle() / "gates" / "unit" / "aggregate.yaml").read_text())
    assert aggregate["attempts"] == 2 and aggregate["result"] == "PASSED" and aggregate["passed"]
    assert [h["result"] for h in aggregate["history"]] == ["FAILED", "PASSED"]
    assert (env.bundle() / "gates" / "unit" / "attempt-001.yaml").exists()  # failed attempt preserved


def test_crashed_attempt_directory_does_not_block(env: Env) -> None:
    env.add(gate_def("unit"))
    (env.bundle() / "gates" / "unit" / "attempt-001").mkdir(parents=True)
    assert env.run("unit")["attempt"] == 1
    assert list((env.bundle() / "gates" / "unit").glob("attempt-001.incomplete-*"))


# --- AC-005 profiles ---------------------------------------------------------------------

def test_ci_profile_requires_gitlab(env: Env, monkeypatch) -> None:
    env.add(gate_def("unit"))
    with pytest.raises(HarnessError) as info:
        env.run("unit", execution_profile="ci")
    assert info.value.code is ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE
    monkeypatch.setenv("GITLAB_CI", "true")
    attempt = env.run("unit", execution_profile="ci")
    assert attempt["execution_profile"] == "ci" and attempt["authority"] == "advisory"


def test_hardware_lab_requires_runner_marker(env: Env, monkeypatch) -> None:
    env.add(gate_def("unit"))
    with pytest.raises(HarnessError):
        env.run("unit", execution_profile="hardware-lab")
    monkeypatch.setenv("ET_HARNESS_HARDWARE_LAB_RUNNER", "lab-7")
    assert env.run("unit", execution_profile="hardware-lab")["result"] == "PASSED"


def test_per_profile_timeout_is_used(env: Env) -> None:
    action = {"id": "run", "tool": "process.command", "timeout_seconds": {"developer": 1, "qe": 30},
              "inputs": {"argv": ["{python}", "-c", "import time; time.sleep(2)"]}}
    env.add(gate_def("t", actions=[action], required=[], conditions=["action_exit_code_zero"]))
    assert env.run("t", workflow("DEVELOPING"), execution_profile="developer")["result"] == "INCONCLUSIVE"
    assert env.run("t", workflow("DEVELOPING"), execution_profile="qe")["result"] == "PASSED"


# --- AC-006 tools --------------------------------------------------------------------------

def test_stdout_stderr_separate_redacted_and_limited(env: Env) -> None:
    script = "import sys; print('token=supersecret123 ' + 'x' * 5000); print('to-stderr', file=sys.stderr)"
    env.add(gate_def("io", argv=["{python}", "-c", script], required=[], conditions=["action_exit_code_zero"]))
    attempt = env.run("io", output_limit_bytes=2048)
    bundle = env.bundle()
    stdout = (bundle / attempt["stdout_path"]).read_text()
    stderr = (bundle / attempt["stderr_path"]).read_text()
    assert "supersecret123" not in stdout and "token=[REDACTED]" in stdout
    assert "output truncated at 2048 bytes" in stdout and attempt["tool_invocations"][0]["stdout_truncated"]
    assert stderr.strip() == "to-stderr" and "to-stderr" not in stdout


def test_argv_is_not_shell_interpreted(env: Env) -> None:
    marker = env.root / "pwned"
    env.add(gate_def("noshell", argv=["{python}", "-c", "import sys; print(sys.argv[1:])", f"; touch {marker}"],
                     required=[], conditions=["action_exit_code_zero"]))
    attempt = env.run("noshell")
    assert attempt["result"] == "PASSED" and not marker.exists()
    assert "; touch" in (env.bundle() / attempt["stdout_path"]).read_text()


class CountingSession:
    def __init__(self) -> None:
        self.starts = self.stops = self.runs = 0

    def describe(self) -> ToolDescriptor:
        return ToolDescriptor("synthetic.session", "1.0.0", "B", "session")

    def validate_inputs(self, inputs: dict) -> list[str]:
        return []

    def start(self, context) -> dict:
        self.starts += 1
        return {"id": self.starts}

    def run(self, session, inputs, context) -> ToolResult:
        self.runs += 1
        if inputs.get("cancel"):
            context.cancel.cancel()
            return ToolResult(None, 1, cancelled=True)
        return ToolResult(0, 1)

    def stop(self, session) -> None:
        self.stops += 1


def session_gate(gate_id: str, cancel: bool = False) -> dict:
    actions = [{"id": f"a{i}", "tool": "synthetic.session", "execution_mode": "session", "timeout_seconds": 5,
                "inputs": {"cancel": cancel and i == 1}} for i in range(2)]
    return gate_def(gate_id, actions=actions, required=[], conditions=["action_exit_code_zero"])


def test_session_tool_reused_and_stopped(tmp_path: Path) -> None:
    tool = CountingSession()
    registry = default_registry()
    registry.register(tool)
    env = Env(tmp_path, tools=registry)
    env.add(session_gate("s1"))
    env.add(session_gate("s2"))
    result = run_verification(["s1", "s2"], repo_root=env.root, workflow=workflow(), registry=env.registry(),
                              audit=env.audit)
    assert result.status == "completed"
    assert tool.runs == 4 and tool.starts == 1 and tool.stops == 1
    assert result.sessions_started == ["synthetic.session"] == result.sessions_stopped


def test_session_stopped_on_cancellation(tmp_path: Path) -> None:
    tool = CountingSession()
    registry = default_registry()
    registry.register(tool)
    env = Env(tmp_path, tools=registry)
    env.add(session_gate("s1", cancel=True))
    env.add(session_gate("s2"))
    token = CancelToken()
    result = run_verification(["s1", "s2"], repo_root=env.root, workflow=workflow(), registry=env.registry(),
                              audit=env.audit, cancel=token)
    assert result.status == "cancelled"
    assert tool.starts == 1 and tool.stops == 1
    assert result.attempts["s1"]["result"] == "INCONCLUSIVE"
    assert "s2" not in result.attempts


def test_schema_validator_structural_gate(env: Env) -> None:
    doc = workflow()
    attempt = env.run("structural", doc)  # no contract path on disk: contract check reports an error
    assert attempt["result"] == "FAILED"
    report = json.loads((env.bundle(doc) / "gates" / "structural" / "attempt-001" / "structural-result.json").read_text())
    assert report["checks"][0]["id"] == "feature-contract" and not report["checks"][0]["ok"]


# --- AC-007 baseline test counts ------------------------------------------------------------

def test_count_below_baseline_fails(env: Env) -> None:
    env.add(gate_def("unit"))  # 3 tests
    attempt = env.run("unit", workflow(baseline={"unit": {"run": 5, "passed": 5, "failed": 0, "skipped": 0}}))
    assert attempt["result"] == "FAILED"
    cond = {c["id"]: c for c in attempt["conditions"]}["test_count_not_below_baseline"]
    assert cond["status"] == "failed" and "3 run vs baseline 5" in cond["message"]


def test_no_baseline_count_is_not_applicable(env: Env) -> None:
    env.add(gate_def("unit"))
    attempt = env.run("unit")
    cond = {c["id"]: c for c in attempt["conditions"]}["test_count_not_below_baseline"]
    assert cond["status"] == "not_applicable" and attempt["result"] == "PASSED"
