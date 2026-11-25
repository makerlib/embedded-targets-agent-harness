"""HK-004 AC-008..AC-010: baseline gates, verify, gate run CLI, evidence bundle."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from et_harness.audit.log import AuditLog
from et_harness.cli.main import cli
from et_harness.common.schemas import validation_errors
from et_harness.contracts.approval import approve
from et_harness.contracts.lint import LintContext
from et_harness.contracts.model import load_contract, parse_contract
from et_harness.evidence.bundle import validate
from et_harness.gates.runner import GateRunner
from et_harness.orchestration.service import WorkflowService
from et_harness.orchestration.states import State

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "contracts" / "feature-001.yaml"
GATES = REPO / "tests" / "fixtures" / "gates"
LEVELS = {"matlab-unittest": "L1", "codegen-compile-reference": "L3"}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def install_gate(root: Path, gate_id: str, level: str, extra: str = "", baseline: str = "record",
                 preconditions: str = "") -> None:
    text = (GATES / "synthetic-gate.yaml.tmpl").read_text()
    for key, value in {"gate_id": gate_id, "level": level, "script": str(GATES / "synthetic_suite.py"),
                       "extra": extra}.items():
        text = text.replace("{" + key + "}", value)
    text = text.replace("baseline: record", f"baseline: {baseline}")
    if preconditions:
        text = text.replace("actions:", f"preconditions:\n{preconditions}\nactions:", 1)
    gates = root / ".agent-harness" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / f"{gate_id}.yaml").write_text(text)


def make_product(tmp_path: Path, *, lite: bool = False, unit_extra: str = ', "--require", "src/blocks/adc/trigger.c"',
                 codegen_baseline: str = "record", codegen_extra: str = "", codegen_pre: str = "") -> Path:
    root = tmp_path / "product"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / ".gitignore").write_text("".join(f".agent-harness/{d}/\n" for d in
                                             ("state", "handoffs", "evidence", "audit", "cache")))
    (root / ".agent-harness").mkdir()
    (root / ".agent-harness" / "project.yaml").write_text(
        "api_version: embedded-targets.ai/v1\nkind: ProjectConfig\nproject: {id: p, name: P}\n"
        "harness: {version: 0.1.0}\n")
    install_gate(root, "matlab-unittest", "L1", unit_extra)
    install_gate(root, "codegen-compile-reference", "L3", codegen_extra, codegen_baseline, codegen_pre)
    (root / "src" / "blocks" / "adc").mkdir(parents=True)
    (root / "src" / "blocks" / "adc" / "adc.c").write_text("int adc;\n")
    data = load_contract(FIXTURE).data
    if lite:
        data["profile"] = "lite"
        data["acceptance_criteria"] = data["acceptance_criteria"][:2]
        data["verification"]["qe_test_design"] = "off"
    approved = approve(parse_contract(data), "jane.doe", context=LintContext(gate_levels=LEVELS))
    (root / "features" / "active").mkdir(parents=True)
    (root / "features" / "active" / "FEATURE-001.yaml").write_text(yaml.safe_dump(approved.data, sort_keys=False))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "baseline")
    return root


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("ET_HARNESS_ORG_POLICY", "ET_HARNESS_USER_CONFIG", "ET_HARNESS_AGENT_INSTANCE", "GITLAB_CI"):
        monkeypatch.delenv(var, raising=False)


def walk(svc: WorkflowService, *steps: tuple[State, str]) -> dict:
    doc = svc.open().document
    engine = svc._engine(doc["workflow"]["id"])
    for target, reason in steps:
        doc = engine.transition(doc["workflow"]["revision"], target, reason)
    return doc


TO_VERIFYING_LITE = [(State.PLAN_REVIEW, "PLAN_SUBMITTED"), (State.DEVELOPING, "PLAN_ACCEPTED"),
                     (State.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"), (State.VERIFYING, "HANDOFF_ACCEPTED")]


def run_cli(root: Path, *args: str):
    result = CliRunner().invoke(cli, [*args, "--project-root", str(root), "--json"], catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


# --- AC-008 baseline at INITIALISING ------------------------------------------------------------

def test_baseline_records_counts_and_tolerates_expected_failures(tmp_path: Path) -> None:
    svc = WorkflowService(make_product(tmp_path))
    svc.create()
    doc = svc.start()
    assert doc["workflow"]["current_state"] == "PLANNING"
    init = doc["initialisation"]
    assert init["baseline_gates"] == {"structural": "PASSED", "matlab-unittest": "FAILED",
                                      "codegen-compile-reference": "PASSED"}
    assert init["baseline_test_counts"]["matlab-unittest"] == {"run": 4, "passed": 3, "failed": 1, "skipped": 0}
    assert {c["id"]: c["status"] for c in init["checks"]}["baseline_passed"] == "passed"


def test_must_pass_baseline_failure_fails_workflow(tmp_path: Path) -> None:
    root = make_product(tmp_path, codegen_baseline="must_pass", codegen_extra=', "--require", "missing.txt"')
    svc = WorkflowService(root)
    svc.create()
    doc = svc.start()
    assert doc["workflow"]["current_state"] == "FAILED"
    assert doc["history"][-1]["reason"] == "BASELINE_FAILURE_ESTABLISHED"


def test_blocked_baseline_gate_blocks_workflow(tmp_path: Path) -> None:
    root = make_product(tmp_path, codegen_pre="  - command_available: reference-toolchain-xyz")
    svc = WorkflowService(root)
    svc.create()
    doc = svc.start()
    assert doc["workflow"]["current_state"] == "BLOCKED"
    assert "GATE_PRECONDITION_FAILED" in doc["blocker"]["reason_codes"]
    assert doc["workflow"]["resumable_state"] == "INITIALISING"


# --- AC-010 verify and gate run ---------------------------------------------------------------

def test_lite_verify_moves_to_evaluating(tmp_path: Path) -> None:
    root = make_product(tmp_path, lite=True)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    walk(svc, *TO_VERIFYING_LITE)
    (root / "src" / "blocks" / "adc" / "trigger.c").write_text("int trigger;\n")
    code, out = run_cli(root, "verify")
    assert code == 0, out
    data = out["data"]
    assert data["state"] == "EVALUATING" and data["transition"] == "VERIFYING -> EVALUATING (GATE_RUN_RECORDED)"
    assert {g: r["result"] for g, r in data["gates"].items()} == {
        "structural": "PASSED", "matlab-unittest": "PASSED", "codegen-compile-reference": "PASSED"}
    assert svc.open().document["history"][-1]["reason"] == "GATE_RUN_RECORDED"
    for entry in AuditLog(root / ".agent-harness" / "audit" / "audit.jsonl").entries():
        assert validation_errors(entry, "audit-record") == [], entry
    code, out = run_cli(root, "verify")  # EVALUATING: no more kernel gate runs
    assert code == 1 and out["reason_codes"] == ["TOOL_PERMISSION_DENIED"]


def test_failed_gate_still_reaches_evaluating(tmp_path: Path) -> None:
    root = make_product(tmp_path, lite=True)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    walk(svc, *TO_VERIFYING_LITE)
    code, out = run_cli(root, "verify")  # trigger.c not written: unit tests fail
    assert code == 1 and out["data"]["gates"]["matlab-unittest"]["result"] == "FAILED"
    assert out["data"]["state"] == "EVALUATING"


def test_full_profile_verify_does_not_transition(tmp_path: Path) -> None:
    root = make_product(tmp_path)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    walk(svc, (State.PLAN_REVIEW, "PLAN_SUBMITTED"), (State.TEST_DESIGN, "PLAN_ACCEPTED"),
         (State.DEVELOPING, "TESTS_ACCEPTED"), (State.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"),
         (State.VERIFYING, "HANDOFF_ACCEPTED"))
    code, out = run_cli(root, "verify", "--execution-profile", "qe")
    assert out["data"]["state"] == "VERIFYING" and out["data"]["transition"] is None


def test_gate_run_cli_development_check(tmp_path: Path) -> None:
    root = make_product(tmp_path, lite=True)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    walk(svc, (State.PLAN_REVIEW, "PLAN_SUBMITTED"), (State.DEVELOPING, "PLAN_ACCEPTED"))
    code, out = run_cli(root, "gate", "run", "matlab-unittest")
    assert code == 1 and out["data"]["result"] == "FAILED" and out["data"]["purpose"] == "development check"
    (root / "src" / "blocks" / "adc" / "trigger.c").write_text("x\n")
    code, out = run_cli(root, "gate", "run", "matlab-unittest")
    assert code == 0 and out["data"]["attempt"] == 3  # baseline=1, checks 2 and 3; no retry reason needed
    code, out = run_cli(root, "gate", "run", "no-such-gate")
    assert code == 4 and out["reason_codes"] == ["MANDATORY_DEPENDENCY_UNAVAILABLE"]
    code, out = run_cli(root, "gate", "run", "matlab-unittest", "--execution-profile", "ci")
    assert code == 4 and out["reason_codes"] == ["MANDATORY_DEPENDENCY_UNAVAILABLE"]


# --- AC-009 evidence bundle ------------------------------------------------------------------

@pytest.fixture
def verified(tmp_path: Path) -> Path:
    root = make_product(tmp_path, lite=True)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    walk(svc, *TO_VERIFYING_LITE)
    (root / "src" / "blocks" / "adc" / "trigger.c").write_text("int trigger;\n")
    svc.verify()
    return root


def bundle_of(root: Path) -> Path:
    svc = WorkflowService(root)
    return GateRunner.bundle_dir(root, svc.open().document)


def test_collect_builds_manifest(verified: Path) -> None:
    code, out = run_cli(verified, "evidence", "collect")
    assert code == 0, out
    bundle = bundle_of(verified)
    manifest = yaml.safe_load((bundle / "manifest.yaml").read_text())
    assert validation_errors(manifest, "evidence-manifest") == []
    assert manifest["evidence_bundle"]["authority"] == "advisory"
    for name in ("environment.yaml", "effective-config.yaml", "feature-contract.yaml", "scope-lock.yaml",
                 "approvals.yaml", "audit.jsonl", "repository-diff.patch"):
        assert (bundle / name).is_file(), name
    assert "trigger.c" in (bundle / "repository-diff.patch").read_text()
    integrity = manifest["integrity"]
    assert integrity["complete"] is False and integrity["missing"] == []
    assert set(integrity["not_implemented"]) == {"packages-lock", "plan", "enforcement-report", "handoffs",
                                                  "model-diffs"}
    verdict_item = next(i for i in manifest["items"] if i["id"] == "verdict")
    assert verdict_item["status"] == "not_applicable"  # HK-005: written after collection
    assert manifest["verification"]["highest_level_passed"] == "L3"
    assert {c["id"]: c["gate_results"] for c in manifest["acceptance_criteria"]}["AC-001"] == {
        "matlab-unittest": "PASSED"}
    listed = {a["path"] for a in manifest["artefacts"]}
    assert "gates/matlab-unittest/attempt-002.yaml" in listed  # baseline attempt 1, verification attempt 2
    assert any(p.startswith("test-results/") for p in listed)
    assert manifest["failures"][0]["purpose"] == "baseline"  # the expected baseline failure is preserved


def test_validate_detects_changes(verified: Path) -> None:
    run_cli(verified, "evidence", "collect")
    bundle = bundle_of(verified)
    assert validate(bundle)["ok"]
    code, out = run_cli(verified, "evidence", "validate")
    assert code == 0 and out["data"]["complete"] is False

    contract = bundle / "feature-contract.yaml"
    contract.write_text(contract.read_text() + "# edited\n")
    report = validate(bundle)
    assert not report["integrity_ok"] and report["modified"] == ["feature-contract.yaml"]

    run_cli(verified, "evidence", "collect")
    (bundle / "extra.txt").write_text("planted")
    report = validate(bundle)
    assert report["integrity_ok"] and report["added"] == ["extra.txt"] and not report["ok"]
    code, out = run_cli(verified, "evidence", "validate")
    assert code == 1 and out["reason_codes"] == ["EVIDENCE_MANIFEST_INCOMPLETE"]

    (bundle / "extra.txt").unlink()
    attempt = bundle / "gates" / "structural" / "attempt-001.yaml"
    attempt.chmod(0o644)
    attempt.unlink()
    report = validate(bundle)
    assert report["missing"] == ["gates/structural/attempt-001.yaml"]


def test_validate_detects_manifest_tamper(verified: Path) -> None:
    run_cli(verified, "evidence", "collect")
    manifest_path = bundle_of(verified) / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["integrity"]["complete"] = True
    manifest_path.write_text(yaml.safe_dump(manifest))
    report = validate(bundle_of(verified))
    assert report["manifest_hash_ok"] is False and not report["integrity_ok"]


def test_collect_before_verification_reports_missing_gates(tmp_path: Path) -> None:
    root = make_product(tmp_path)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    code, out = run_cli(root, "evidence", "collect")
    integrity = out["data"]["integrity"]
    assert set(integrity["missing"]) == {"gate:structural", "gate:matlab-unittest", "gate:codegen-compile-reference"}
    assert "qe-result" in integrity["not_implemented"]  # full profile
