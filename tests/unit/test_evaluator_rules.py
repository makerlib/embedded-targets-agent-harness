"""HK-005 AC-001..AC-004: deterministic evaluator rules (§22)."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from et_harness.contracts.model import load_contract
from et_harness.evaluator.rules import ApprovalEvidence, EvaluationInputs, GateEvidence, evaluate
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "feature-001.yaml"
H = "sha256:" + "a" * 64


def contract(**changes) -> dict:
    data = copy.deepcopy(load_contract(FIX).data)
    data["acceptance_criteria"] = data["acceptance_criteria"][:2]  # gate criteria only
    for key, value in changes.items():
        data[key] = value
    return data


def gates(**results) -> list[GateEvidence]:
    levels = {"structural": "L0", "matlab-unittest": "L1", "codegen-compile-reference": "L3"}
    out = []
    for gate_id, level in levels.items():
        out.append(GateEvidence(gate_id, level, results.get(gate_id, "PASSED"), "qe", 2,
                                {"run": 10, "passed": 10, "failed": 0, "skipped": 0},
                                {"run": 8, "passed": 7, "failed": 1, "skipped": 0}))
    return out


def good(**overrides) -> EvaluationInputs:
    inputs = EvaluationInputs(
        feature_id="FEATURE-001", workflow_id="workflow-FEATURE-001-001", profile="full", contract=contract(),
        contract_hash_locked=H, contract_hash_current=H, gates=gates(), manifest_present=True,
        manifest_integrity_ok=True, manifest_complete=True)
    return replace(inputs, **overrides)


def categories(result) -> set[str]:
    return {f.category for f in result.findings}


# --- AC-001 purity -----------------------------------------------------------------------------

def test_same_inputs_same_verdict() -> None:
    a, b = evaluate(good()), evaluate(good())
    assert a == b


def test_inputs_are_not_mutated() -> None:
    inputs = good(scope_findings=[{"reason_code": "PATH_OUTSIDE_SCOPE", "path": "x"}])
    before = copy.deepcopy(inputs)
    evaluate(inputs)
    assert inputs == before


# --- AC-002 PASSED only when everything holds ------------------------------------------------

def test_all_conditions_pass() -> None:
    result = evaluate(good())
    assert result.verdict == "PASSED" and result.findings == []
    assert result.highest_level_passed == "L3" and result.mandatory_gates_passed
    assert [c["status"] for c in result.criteria] == ["passed", "passed"]


NEGATIONS = {
    "contract hash": (dict(contract_hash_current="sha256:" + "b" * 64), "INCONCLUSIVE", "CONTRACT_HASH_MISMATCH"),
    "never locked": (dict(contract_hash_locked=None), "INCONCLUSIVE", "CONTRACT_HASH_MISMATCH"),
    "state chain": (dict(state_integrity_ok=False), "INCONCLUSIVE", "STATE_TAMPER_DETECTED"),
    "evidence corrupted": (dict(manifest_integrity_ok=False), "INCONCLUSIVE", "EVIDENCE_MANIFEST_INCOMPLETE"),
    "failed gate": (dict(gates=gates(**{"codegen-compile-reference": "FAILED"})), "FAILED", None),
    "outside scope": (dict(scope_findings=[{"reason_code": "PATH_OUTSIDE_SCOPE", "path": "lib/x.c"}]), "FAILED",
                      "PATH_OUTSIDE_SCOPE"),
    "protected path": (dict(scope_findings=[{"reason_code": "PROTECTED_PATH_CHANGED", "path": "tests/golden/a"}]),
                       "FAILED", "PROTECTED_PATH_CHANGED"),
    "qe test changed": (dict(scope_findings=[{"reason_code": "QE_TEST_MODIFIED", "path": "qe-tests/adc/t.m"}]),
                        "FAILED", "QE_TEST_MODIFIED"),
    "derived committed": (dict(scope_findings=[{"reason_code": "DERIVED_ARTEFACT_COMMITTED", "path": "slprj/x"}]),
                          "FAILED", "DERIVED_ARTEFACT_COMMITTED"),
    "blocked gate": (dict(gates=gates(**{"matlab-unittest": "BLOCKED"})), "BLOCKED", "MANDATORY_DEPENDENCY_UNAVAILABLE"),
    "unreviewed model": (dict(changed_models=["src/blocks/adc/adc.slx"]), "BLOCKED", "MODEL_DIFF_UNAVAILABLE"),
    "owner approval": (dict(owner_approval_valid=False), "BLOCKED", "APPROVAL_INVALID"),
    "blocker": (dict(blocker={"reason_code": "TOOLCHAIN_UNAVAILABLE", "reason_codes": ["TOOLCHAIN_UNAVAILABLE"]}),
                "BLOCKED", "TOOLCHAIN_UNAVAILABLE"),
    "inconclusive gate": (dict(gates=gates(**{"matlab-unittest": "INCONCLUSIVE"})), "INCONCLUSIVE", None),
    "missing gate": (dict(gates=gates()[:2]), "INCONCLUSIVE", "GATE_EVIDENCE_MISSING"),
    "no manifest": (dict(manifest_present=False), "INCONCLUSIVE", "EVIDENCE_MANIFEST_INCOMPLETE"),
    "manifest incomplete": (dict(manifest_complete=False, manifest_missing=["gate:x"]), "INCONCLUSIVE",
                            "EVIDENCE_MANIFEST_INCOMPLETE"),
    "not implemented evidence": (dict(manifest_complete=False, manifest_not_implemented=["plan"]), "INCONCLUSIVE",
                                 "NOT_IMPLEMENTED"),
    "stale manifest": (dict(manifest_added=["gates/x/attempt-009.yaml"]), "INCONCLUSIVE",
                       "EVIDENCE_MANIFEST_INCOMPLETE"),
    "open check": (dict(open_checks=["host_guards_and_enforcement_probes"]), "INCONCLUSIVE", "NOT_IMPLEMENTED"),
    "no workflow": (dict(workflow_exists=False), "INCONCLUSIVE", "EXECUTION_NOT_STARTED"),
}


@pytest.mark.parametrize("name", sorted(NEGATIONS))
def test_each_condition_prevents_pass(name: str) -> None:
    overrides, verdict, code = NEGATIONS[name]
    result = evaluate(good(**overrides))
    assert result.verdict == verdict
    if code:
        assert code in result.reason_codes
    assert "VERDICT_NOT_AUTHORITATIVE" not in result.reason_codes


def test_test_weakening_fails() -> None:
    weak = [replace(g, test_counts={"run": 5, "passed": 5, "failed": 0, "skipped": 0}) for g in gates()]
    result = evaluate(good(gates=weak))
    assert result.verdict == "FAILED" and "TEST_WEAKENING_SUSPECTED" in result.reason_codes
    assert result.human_review_required


def test_level_not_reached_is_insufficient() -> None:
    result = evaluate(good(contract=contract(verification={"required_level": "L4", "required_gates":
                                                           ["structural", "matlab-unittest", "codegen-compile-reference"]})))
    assert result.verdict == "INCONCLUSIVE" and result.highest_level_passed == "L3"


def test_failed_lower_level_caps_ladder() -> None:
    result = evaluate(good(gates=gates(**{"structural": "FAILED"})))
    assert result.highest_level_passed is None and result.verdict == "FAILED"
    assert result.failed_gates == ["structural"]


# --- approvals -----------------------------------------------------------------------------------

def human_review_contract() -> dict:
    data = contract()
    data["acceptance_criteria"].append({"id": "AC-003", "statement": "help text", "verification_method": "human_review"})
    return data


def test_human_review_without_approval_blocks() -> None:
    result = evaluate(good(contract=human_review_contract()))
    assert result.verdict == "BLOCKED" and "HUMAN_REVIEW_REQUIRED" in result.reason_codes
    assert result.criteria[2]["status"] == "awaiting_review"


def test_human_review_local_approval_unverifiable() -> None:
    result = evaluate(good(contract=human_review_contract(),
                           approvals=[ApprovalEvidence("human_review", "AC-003", "local", True)]))
    assert result.verdict == "BLOCKED" and "APPROVAL_UNVERIFIABLE" in result.reason_codes


def test_human_review_gitlab_approval_passes() -> None:
    result = evaluate(good(contract=human_review_contract(),
                           approvals=[ApprovalEvidence("human_review", "AC-003", "gitlab", True)]))
    assert result.verdict == "PASSED"


def test_model_review_with_gitlab_approval_passes() -> None:
    result = evaluate(good(changed_models=["m.slx"], approvals=[ApprovalEvidence("model_review", "m.slx", "gitlab", True)]))
    assert result.verdict == "PASSED"


def test_l5_requires_signoff() -> None:
    l5 = contract(verification={"required_level": "L5", "required_gates": ["structural", "hil"]})
    hil = gates()[:1] + [GateEvidence("hil", "L5", "PASSED", "hardware-lab", 1)]
    blocked = evaluate(good(contract=l5, gates=hil))
    assert blocked.verdict == "BLOCKED" and "APPROVAL_REQUIRED" in blocked.reason_codes
    local = evaluate(good(contract=l5, gates=hil, approvals=[ApprovalEvidence("signoff", "L5", "local", True)]))
    assert local.verdict == "BLOCKED" and "APPROVAL_UNVERIFIABLE" in local.reason_codes
    signed = evaluate(good(contract=l5, gates=hil, approvals=[ApprovalEvidence("signoff", "L5", "gitlab", True)]))
    assert signed.verdict == "PASSED"


def test_class_d_action_needs_gitlab_approval() -> None:
    data = contract(approvals={"required": [{"action": "flash-board", "class": "D"}]})
    assert evaluate(good(contract=data)).verdict == "BLOCKED"
    ok = evaluate(good(contract=data, approvals=[ApprovalEvidence("class_de", "flash-board", "gitlab", True)]))
    assert ok.verdict == "PASSED"


# --- AC-003 precedence --------------------------------------------------------------------------

def test_precedence_integrity_over_everything() -> None:
    result = evaluate(good(state_integrity_ok=False, gates=gates(structural="FAILED"),
                           owner_approval_valid=False, manifest_present=False))
    assert result.verdict == "INCONCLUSIVE"
    assert categories(result) == {"integrity", "failure", "blocked", "insufficient"}


def test_precedence_failure_over_blocked_and_insufficient() -> None:
    result = evaluate(good(gates=gates(structural="FAILED", **{"matlab-unittest": "BLOCKED"}), manifest_present=False))
    assert result.verdict == "FAILED"


def test_precedence_blocked_over_insufficient() -> None:
    result = evaluate(good(owner_approval_valid=False, manifest_present=False))
    assert result.verdict == "BLOCKED"


# --- AC-004 authority -----------------------------------------------------------------------------

def test_passed_is_advisory_without_verified_authority() -> None:
    result = evaluate(good())
    assert result.authority == "advisory" and result.reason_codes == ["VERDICT_NOT_AUTHORITATIVE"]


def test_ci_profile_alone_is_not_authoritative() -> None:
    ci = [replace(g, execution_profile="ci") for g in gates()]
    assert evaluate(good(gates=ci)).authority == "advisory"


def test_authoritative_needs_verified_ci_runs() -> None:
    ci = [replace(g, execution_profile="ci") for g in gates()]
    result = evaluate(good(gates=ci, authority_verified=True))
    assert result.authority == "authoritative" and result.reason_codes == []
    mixed = ci[:2] + [replace(ci[2], execution_profile="developer")]
    assert evaluate(good(gates=mixed, authority_verified=True)).authority == "advisory"
