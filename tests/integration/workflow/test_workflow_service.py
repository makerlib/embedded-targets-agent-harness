"""HK-003 AC-005, AC-006, AC-008: workflow service on a real Git repository."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from et_harness.audit.log import AuditLog
from et_harness.cli.main import cli
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validation_errors
from et_harness.contracts.approval import approve
from et_harness.contracts.lint import LintContext
from et_harness.contracts.model import load_contract
from et_harness.orchestration.service import WorkflowService

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "contracts" / "feature-001.yaml"
GATES = {"matlab-unittest": "L1", "codegen-compile-reference": "L3"}
PROJECT = """api_version: embedded-targets.ai/v1
kind: ProjectConfig
project: {id: product, name: Product}
harness: {version: 0.1.0}
gate_levels: {matlab-unittest: L1, codegen-compile-reference: L3}
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def write_approved(path: Path, approver: str = "jane.doe", edit=None) -> None:
    import yaml

    contract = load_contract(FIXTURE)
    data = contract.data
    if edit:
        edit(data)
    from et_harness.contracts.model import parse_contract

    approved = approve(parse_contract(data), approver, context=LintContext(gate_levels=GATES))
    path.write_text(yaml.safe_dump(approved.data, sort_keys=False))


@pytest.fixture
def product(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("ET_HARNESS_ORG_POLICY", "ET_HARNESS_USER_CONFIG", "ET_HARNESS_AGENT_INSTANCE"):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "product"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("".join(f".agent-harness/{d}/\n" for d in
                                             ("state", "handoffs", "evidence", "audit", "cache")))
    (root / ".agent-harness").mkdir()
    (root / ".agent-harness" / "project.yaml").write_text(PROJECT)
    (root / "src" / "blocks" / "adc").mkdir(parents=True)
    (root / "src" / "blocks" / "adc" / "adc.c").write_text("int adc;\n")
    (root / "features" / "active").mkdir(parents=True)
    write_approved(root / "features" / "active" / "FEATURE-001.yaml")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "baseline")
    return root


def state(root: Path) -> dict:
    return json.loads((root / ".agent-harness" / "state" / "orchestration-state.json").read_text())


# --- create -------------------------------------------------------------------------------

def test_create_and_start_reach_planning(product: Path) -> None:
    svc = WorkflowService(product)
    created = svc.create()
    assert created["workflow"]["id"] == "workflow-FEATURE-001-001"
    assert created["workflow"]["current_state"] == "CREATED"
    doc = svc.start()
    assert doc["workflow"]["current_state"] == "PLANNING"
    assert doc["repository"]["baseline_commit"] == git(product, "rev-parse", "HEAD").strip()
    assert doc["contract"]["hash"] == load_contract(product / "features/active/FEATURE-001.yaml").digest()
    checks = {c["id"]: c["status"] for c in doc["initialisation"]["checks"]}
    assert checks["repository_acceptable"] == checks["owner_approval_valid"] == "passed"
    assert checks["contract_and_scope_locked"] == "passed"
    assert {k for k, v in checks.items() if v == "not_implemented"} == {
        "required_packages_resolved", "baseline_passed", "host_guards_and_enforcement_probes"}
    lock = json.loads((product / ".agent-harness" / "state" / "scope-lock.json").read_text())
    assert lock["kind"] == "ScopeLock" and lock["contract_hash"] == doc["contract"]["hash"]
    for entry in AuditLog(product / ".agent-harness" / "audit" / "audit.jsonl").entries():
        assert validation_errors(entry, "audit-record") == [], entry


def test_create_requires_owner_approval(product: Path) -> None:
    path = product / "features" / "active" / "FEATURE-001.yaml"
    shutil.copy(FIXTURE, path)  # unapproved
    with pytest.raises(HarnessError) as info:
        WorkflowService(product).create()
    assert info.value.code is ReasonCode.OWNER_APPROVAL_MISSING


def test_create_requires_exactly_one_active_feature(product: Path) -> None:
    shutil.copy(product / "features/active/FEATURE-001.yaml", product / "features/active/FEATURE-002.yaml")
    with pytest.raises(HarnessError) as info:
        WorkflowService(product).create()
    assert info.value.code is ReasonCode.MULTIPLE_ACTIVE_FEATURES


def test_only_one_open_workflow(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    with pytest.raises(HarnessError) as info:
        svc.create()
    assert info.value.code is ReasonCode.TRANSITION_NOT_ALLOWED


def test_new_workflow_after_final_archives_previous(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    svc.block(["MANDATORY_DEPENDENCY_UNAVAILABLE"], "test")
    svc.cancel("abandoned in test")
    second = svc.create()
    assert second["workflow"]["id"] == "workflow-FEATURE-001-002"
    archived = product / ".agent-harness" / "state" / "archive" / "workflow-FEATURE-001-001"
    assert (archived / "orchestration-state.json").exists()


# --- start failures and resume ----------------------------------------------------------------

def test_dirty_baseline_blocks_then_resumes(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    (product / "src" / "blocks" / "adc" / "adc.c").write_text("int adc = 1;\n")
    doc = svc.start()
    assert doc["workflow"]["current_state"] == "BLOCKED"
    assert doc["blocker"]["reason_codes"] == ["BASELINE_DIRTY"]
    assert doc["workflow"]["resumable_state"] == "INITIALISING"
    git(product, "checkout", "--", ".")
    doc = svc.resume()
    assert doc["workflow"]["current_state"] == "PLANNING"
    reasons = [h["reason"] for h in doc["history"]]
    assert reasons == ["START_REQUESTED", "BLOCKER_RAISED", "BLOCKER_RESOLVED", "INITIALISATION_PASSED"]


def test_enforced_policy_blocks_honestly(product: Path) -> None:
    org = product.parent / "org.yaml"
    org.write_text("api_version: embedded-targets.ai/v1\nkind: OrganisationPolicy\nmetadata: {name: o}\n"
                   "settings: {enforcement: {minimum_write_tier: ENFORCED}}\n")
    import os
    os.environ["ET_HARNESS_ORG_POLICY"] = str(org)
    try:
        svc = WorkflowService(product)
        svc.create()
        doc = svc.start()
    finally:
        del os.environ["ET_HARNESS_ORG_POLICY"]
    assert doc["workflow"]["current_state"] == "BLOCKED"
    assert "ENFORCEMENT_TIER_INSUFFICIENT" in doc["blocker"]["reason_codes"]


def test_start_twice_rejected(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    svc.start()
    with pytest.raises(HarnessError) as info:
        svc.start()
    assert info.value.code is ReasonCode.TRANSITION_NOT_ALLOWED


def test_cancel_only_from_blocked(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    with pytest.raises(HarnessError):
        svc.cancel("no")
    svc.block(["TOOL_NOT_REGISTERED"], "x")
    assert svc.cancel("yes")["workflow"]["current_state"] == "FAILED"
    with pytest.raises(HarnessError):
        svc.block(["TOOL_NOT_REGISTERED"], "x")  # FAILED is final


# --- AC-005 tamper ----------------------------------------------------------------------------

def test_tampered_state_is_quarantined_restored_and_blocked(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    svc.start()
    path = product / ".agent-harness" / "state" / "orchestration-state.json"
    forged = state(product)
    forged["workflow"]["current_state"] = "PASSED"
    path.write_text(json.dumps(forged))
    status = svc.status()
    assert status["state"] == "BLOCKED"
    assert status["blocker"]["reason_codes"] == ["STATE_TAMPER_DETECTED"]
    assert status["resumable_state"] == "PLANNING"
    assert status["findings"][0]["reason_code"] == "STATE_TAMPER_DETECTED"
    quarantined = list((product / ".agent-harness" / "state" / "quarantine").glob("*.json"))
    assert len(quarantined) == 1 and json.loads(quarantined[0].read_text())["workflow"]["current_state"] == "PASSED"
    with pytest.raises(HarnessError):
        svc.resume()  # needs maintainer recovery
    assert svc.resume(maintainer_recovery=True)["workflow"]["current_state"] == "PLANNING"


def test_tamper_without_verified_copy_stops_without_writing(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    state_dir = product / ".agent-harness" / "state"
    for name in ("orchestration-state.json", "orchestration-state.lkg.json"):
        doc = json.loads((state_dir / name).read_text())
        doc["workflow"]["current_state"] = "PASSED"
        (state_dir / name).write_text(json.dumps(doc))
    audit_before = (product / ".agent-harness" / "audit" / "audit.jsonl").read_bytes()
    with pytest.raises(HarnessError) as info:
        svc.status()
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED
    assert (product / ".agent-harness" / "audit" / "audit.jsonl").read_bytes() == audit_before


def test_tampered_audit_log_stops(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    audit = product / ".agent-harness" / "audit" / "audit.jsonl"
    audit.write_text(audit.read_text().replace('"CREATED"', '"PASSED"', 1))
    with pytest.raises(HarnessError) as info:
        svc.status()
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED


def test_contract_mutation_after_lock_blocks(product: Path) -> None:
    svc = WorkflowService(product)
    svc.create()
    svc.start()
    path = product / "features" / "active" / "FEATURE-001.yaml"
    path.write_text(path.read_text().replace("tests/adc/**", "tests/**"))
    status = svc.status()
    assert status["state"] == "BLOCKED"
    assert status["blocker"]["reason_codes"] == ["CONTRACT_MUTATED_AFTER_LOCK"]
    with pytest.raises(HarnessError) as info:
        svc.resume()
    assert info.value.code is ReasonCode.CONTRACT_MUTATED_AFTER_LOCK
    git(product, "checkout", "--", "features")
    assert svc.resume()["workflow"]["current_state"] == "PLANNING"


# --- CLI and fresh processes ---------------------------------------------------------------------

def run_cli(root: Path, *args: str):
    result = CliRunner().invoke(cli, [*args, "--project-root", str(root), "--json"], catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


def test_cli_lifecycle(product: Path) -> None:
    assert run_cli(product, "workflow", "create")[0] == 0
    code, out = run_cli(product, "workflow", "start")
    assert code == 0 and out["data"]["state"] == "PLANNING"
    code, out = run_cli(product, "workflow", "status")
    assert code == 0 and out["data"]["owner"] == "planner" and out["data"]["contract"]["locked"]
    code, out = run_cli(product, "workflow", "block", "--reason", "TOOL_NOT_REGISTERED", "--message", "no tool")
    assert code == 4 and out["reason_codes"] == ["TOOL_NOT_REGISTERED"]
    code, out = run_cli(product, "workflow", "resume")
    assert code == 0 and out["data"]["state"] == "PLANNING"
    code, out = run_cli(product, "workflow", "history")
    assert [h["reason"] for h in out["data"]["history"]] == [
        "START_REQUESTED", "INITIALISATION_PASSED", "BLOCKER_RAISED", "BLOCKER_RESOLVED"]
    code, out = run_cli(product, "workflow", "block", "--reason", "NOT_A_CODE")
    assert code == 1 and out["reason_codes"] == ["TRANSITION_NOT_ALLOWED"]


def test_cli_without_workflow(product: Path) -> None:
    code, out = run_cli(product, "workflow", "status")
    assert code == 1 and out["reason_codes"] == ["STATE_NOT_FOUND"]


def test_fresh_process_resumes_from_disk(product: Path) -> None:
    def harness(*args: str) -> dict:
        r = subprocess.run([sys.executable, "-m", "et_harness.cli.main", *args, "--project-root", str(product),
                            "--json"], capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout)

    assert harness("workflow", "create")["ok"]
    assert harness("workflow", "start")["data"]["state"] == "PLANNING"
    assert harness("workflow", "status")["data"]["revision"] == 2
