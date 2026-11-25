"""HK-005 AC-005..AC-008: `harness evaluate` on a workflow, read-only behaviour, bootstrap records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validation_errors
from et_harness.evaluator import service as evaluator_service
from et_harness.evaluator.service import evaluate_bootstrap_records, evaluate_workflow
from et_harness.gates.runner import GateRunner
from et_harness.orchestration.service import WorkflowService
from product_helpers import REPO, TO_VERIFYING_LITE, make_product, run_cli, walk


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("ET_HARNESS_ORG_POLICY", "ET_HARNESS_USER_CONFIG", "ET_HARNESS_AGENT_INSTANCE", "GITLAB_CI"):
        monkeypatch.delenv(var, raising=False)


def to_evaluating(tmp_path: Path, *, implement: bool = True, collect: bool = True, max_cycles: int = 3) -> Path:
    root = make_product(tmp_path, lite=True, max_cycles=max_cycles)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    walk(svc, *TO_VERIFYING_LITE)
    if implement:
        (root / "src" / "blocks" / "adc" / "trigger.c").write_text("int trigger;\n")
    svc.verify()
    if collect:
        run_cli(root, "evidence", "collect")
    return root


def tree(root: Path, *, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(".git/") and not any(rel.startswith(e) for e in exclude):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def bundle(root: Path) -> Path:
    return GateRunner.bundle_dir(root, WorkflowService(root).open().document)


# --- AC-005 evaluate in EVALUATING ----------------------------------------------------------------

def test_real_workflow_is_inconclusive_until_later_features(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path)
    code, out = run_cli(root, "evaluate")
    verdict = out["data"]["verdict"]
    assert verdict["verdict"] == "INCONCLUSIVE" and verdict["authority"] == "advisory"
    assert verdict["reason_codes"] == ["NOT_IMPLEMENTED"]  # every gate passed; only later-feature evidence is open
    assert verdict["evidence"]["missing_artefacts"] == [] and "plan" in verdict["evidence"]["not_implemented"]
    assert verdict["verification"]["highest_level_passed"] == "L3" and verdict["verification"]["mandatory_gates_passed"]
    assert out["data"]["transition"] == "EVALUATING -> INCONCLUSIVE (EVIDENCE_INSUFFICIENT)"
    record = bundle(root) / "evaluations" / "evaluation-001.yaml"
    assert validation_errors(yaml.safe_load(record.read_text()), "verdict") == []
    assert not (bundle(root) / "verdict.yaml").exists()  # INCONCLUSIVE is resumable, not final
    events = AuditLog(root / ".agent-harness" / "audit" / "audit.jsonl").entries()
    issued = [e for e in events if e["event_type"] == "verdict.issued"]
    assert len(issued) == 1 and issued[0]["persona"] == "evaluator" and issued[0]["result"] == "INCONCLUSIVE"
    for e in events:
        assert validation_errors(e, "audit-record") == [], e


def test_failed_gate_goes_to_rework(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path, implement=False)
    code, out = run_cli(root, "evaluate")
    assert out["data"]["verdict"]["verdict"] == "FAILED"
    assert out["data"]["verdict"]["verification"]["failed_gates"] == ["matlab-unittest"]
    assert out["data"]["transition"] == "EVALUATING -> REWORK_PLANNING (REWORK_ALLOWED)"
    events = AuditLog(root / ".agent-harness" / "audit" / "audit.jsonl").entries()
    result = [e for e in events if e["event_type"] == "evaluation.result"][-1]
    assert result["details"]["rework_allowed"] is True and validation_errors(result, "audit-record") == []
    assert WorkflowService(root).open().document["rework"]["current_cycle"] == 1


def test_failure_without_rework_is_final_and_write_once(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path, implement=False, max_cycles=0)
    code, out = run_cli(root, "evaluate")
    assert out["data"]["transition"] == "EVALUATING -> FAILED (FAILURE_ESTABLISHED)"
    final = bundle(root) / "verdict.yaml"
    assert yaml.safe_load(final.read_text())["verdict"] == "FAILED"
    with pytest.raises(FileExistsError):
        GateRunner._write_once(final, "forged")


def test_passed_path_plumbing(tmp_path: Path, monkeypatch) -> None:
    """Exercise the PASSED transition by supplying the evidence later features will produce.

    The inputs are the real ones from disk, except that the manifest is marked
    complete and the open NOT_IMPLEMENTED checks are cleared, as HK-008..HK-012 will
    make them. This tests the plumbing, not a real PASSED.
    """
    root = to_evaluating(tmp_path)
    real = evaluator_service.build_inputs

    def completed(service):
        inputs, doc, b = real(service)
        inputs.manifest_complete, inputs.manifest_not_implemented, inputs.open_checks = True, [], []
        return inputs, doc, b

    monkeypatch.setattr(evaluator_service, "build_inputs", completed)
    out = evaluate_workflow(WorkflowService(root))
    assert out["verdict"]["verdict"] == "PASSED"
    assert out["verdict"]["reason_codes"] == ["VERDICT_NOT_AUTHORITATIVE"]
    assert out["transition"] == "EVALUATING -> PASSED (COMPLETION_RULES_SATISFIED)"
    assert yaml.safe_load((bundle(root) / "verdict.yaml").read_text())["authority"] == "advisory"


def test_evaluate_outside_evaluating_refused(tmp_path: Path) -> None:
    root = make_product(tmp_path, lite=True)
    svc = WorkflowService(root)
    svc.create()
    svc.start()
    with pytest.raises(HarnessError) as info:
        evaluate_workflow(svc)
    assert info.value.code is ReasonCode.TRANSITION_NOT_ALLOWED


# --- AC-006 read-only --------------------------------------------------------------------------------

def test_evaluator_writes_only_its_records(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path)
    before = tree(root, exclude=(".agent-harness/state/", ".agent-harness/audit/"))
    run_cli(root, "evaluate")
    after = tree(root, exclude=(".agent-harness/state/", ".agent-harness/audit/"))
    added = set(after) - set(before)
    assert all(p.endswith("evaluations/evaluation-001.yaml") for p in added) and len(added) == 1
    assert {k: v for k, v in after.items() if k in before} == before


def test_preview_writes_nothing(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path)
    before = tree(root)
    code, out = run_cli(root, "evaluate", "--preview")
    assert out["data"]["written"] == [] and out["data"]["transition"] is None
    assert out["data"]["verdict"]["evaluation"]["next_state"] == "preview"
    assert tree(root) == before


# --- integrity findings ------------------------------------------------------------------------------

def test_tampered_attempt_gives_inconclusive(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path)
    attempt = bundle(root) / "gates" / "matlab-unittest" / "attempt-002.yaml"
    attempt.chmod(0o644)
    attempt.write_text(attempt.read_text().replace("result: PASSED", "result: PASSED\n# edited"))
    code, out = run_cli(root, "evaluate", "--preview")
    finding = [f for f in out["data"]["verdict"]["findings"] if f["category"] == "integrity"]
    assert out["data"]["verdict"]["verdict"] == "INCONCLUSIVE" and finding


def test_out_of_scope_change_fails(tmp_path: Path) -> None:
    root = to_evaluating(tmp_path)
    (root / "lib").mkdir()
    (root / "lib" / "sneaky.c").write_text("x\n")
    code, out = run_cli(root, "evaluate", "--preview")
    assert out["data"]["verdict"]["verdict"] == "FAILED"
    assert out["data"]["verdict"]["scope"]["unauthorised_changes"] == ["lib/sneaky.c"]


# --- AC-008 bootstrap records --------------------------------------------------------------------------

def test_bootstrap_records_are_not_upgraded() -> None:
    results = {r["feature"]: r for r in evaluate_bootstrap_records(
        REPO / "docs" / "bootstrap-evidence" / "bootstrap-records.yaml", REPO)}
    assert set(results) == {"HK-001", "HK-002", "HK-003", "HK-004", "HK-015"}
    assert all(r["verdict"] != "PASSED" for r in results.values())
    assert all(r["contract_unchanged_since_lock"] for r in results.values())
    assert all(r["highest_level_passed"] == "L1" for r in results.values())
    for feature in ("HK-001", "HK-002", "HK-015"):
        assert results[feature]["verdict"] == "BLOCKED" and "APPROVAL_INVALID" in results[feature]["reason_codes"]
    for feature in ("HK-003", "HK-004"):
        assert results[feature]["verdict"] == "INCONCLUSIVE"
