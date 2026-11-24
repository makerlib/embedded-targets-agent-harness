"""HK-002 AC-001..AC-005, AC-009: contract loading, hashing, lint, approval, lock, discovery."""

from __future__ import annotations

import copy
import math
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path

import pytest

from et_harness.artefacts.classes import ArtefactClassifier
from et_harness.common.errors import HarnessError
from et_harness.common.jcs import canonicalize, contract_digest, ecmascript_number
from et_harness.common.reason_codes import ReasonCode
from et_harness.contracts.approval import AGENT_INSTANCE_ENV, approval_status, approve
from et_harness.contracts.discovery import find_active_feature
from et_harness.contracts.lint import LintContext, lint_contract
from et_harness.contracts.lock import contract_diff, lock_contract, verify_lock
from et_harness.contracts.model import load_contract, load_contract_text, parse_contract

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"
GATES = {"matlab-unittest": "L1", "codegen-compile-reference": "L3"}
CTX = LintContext(gate_levels=GATES)
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def data() -> dict:
    return copy.deepcopy(load_contract(FIX / "feature-001.yaml").data)


def codes(report) -> set[str]:
    return {c.value for c in report.reason_codes()}


# --- AC-001 loading and schema ------------------------------------------------------

def test_valid_contract_loads(data: dict) -> None:
    contract = parse_contract(data)
    assert contract.id == "FEATURE-001" and contract.required_level == "L3"


@pytest.mark.parametrize("missing", ["scope", "verification", "intake", "acceptance_criteria"])
def test_missing_section_invalid(data: dict, missing: str) -> None:
    del data[missing]
    with pytest.raises(HarnessError) as info:
        parse_contract(data)
    assert info.value.code is ReasonCode.FEATURE_CONTRACT_INVALID


def test_unknown_key_invalid(data: dict) -> None:
    data["scope"]["writable_everything"] = True
    with pytest.raises(HarnessError) as info:
        parse_contract(data)
    assert info.value.code is ReasonCode.FEATURE_CONTRACT_INVALID


def test_unsupported_version(data: dict) -> None:
    data["api_version"] = "embedded-targets.ai/v2"
    with pytest.raises(HarnessError) as info:
        parse_contract(data)
    assert info.value.code is ReasonCode.SCHEMA_VERSION_UNSUPPORTED


def test_yaml_date_is_not_json(data: dict) -> None:
    text = (FIX / "feature-001.yaml").read_text().replace("release: R2026b", "release: 2026-11-24")
    with pytest.raises(HarnessError) as info:
        load_contract_text(text)
    assert info.value.code is ReasonCode.FEATURE_CONTRACT_INVALID


def test_duplicate_yaml_key_rejected() -> None:
    text = (FIX / "feature-001.yaml").read_text().replace("profile: full", "profile: full\nprofile: lite")
    with pytest.raises(HarnessError) as info:
        load_contract_text(text)
    assert info.value.code is ReasonCode.FEATURE_CONTRACT_INVALID


def test_duplicate_criterion_ids_rejected(data: dict) -> None:
    data["acceptance_criteria"][1]["id"] = "AC-001"
    with pytest.raises(HarnessError):
        parse_contract(data)


# --- AC-002 RFC 8785 ------------------------------------------------------------------

def test_hash_identical_for_reformatted_yaml() -> None:
    a = load_contract(FIX / "feature-001.yaml")
    b = load_contract(FIX / "feature-001-reordered.yaml")
    assert a.digest() == b.digest()


def test_hash_changes_with_content(data: dict) -> None:
    before = contract_digest(data)
    data["scope"]["allowed_paths"].append("src/extra/**")
    assert contract_digest(data) != before


# RFC 8785 Appendix B (IEEE-754 bits -> canonical text)
RFC8785_NUMBERS = {
    "0000000000000000": "0", "8000000000000000": "0", "0000000000000001": "5e-324",
    "8000000000000001": "-5e-324", "7fefffffffffffff": "1.7976931348623157e+308",
    "ffefffffffffffff": "-1.7976931348623157e+308", "4340000000000000": "9007199254740992",
    "c340000000000000": "-9007199254740992", "4430000000000000": "295147905179352830000",
    "44b52d02c7e14af5": "9.999999999999997e+22", "44b52d02c7e14af6": "1e+23",
    "44b52d02c7e14af7": "1.0000000000000001e+23", "444b1ae4d6e2ef4e": "999999999999999700000",
    "444b1ae4d6e2ef4f": "999999999999999900000", "444b1ae4d6e2ef50": "1e+21",
    "3eb0c6f7a0b5ed8c": "9.999999999999997e-7", "3eb0c6f7a0b5ed8d": "0.000001",
    "41b3de4355555553": "333333333.3333332", "41b3de4355555554": "333333333.33333325",
    "41b3de4355555555": "333333333.3333333", "41b3de4355555556": "333333333.3333334",
    "41b3de4355555557": "333333333.33333343", "becbf647612f3696": "-0.0000033333333333333333",
    "43143ff3c1cb0959": "1424953923781206.2",
}


@pytest.mark.parametrize("bits,expected", sorted(RFC8785_NUMBERS.items()))
def test_rfc8785_number_vectors(bits: str, expected: str) -> None:
    assert ecmascript_number(struct.unpack(">d", bytes.fromhex(bits))[0]) == expected


def test_rfc8785_sorting_and_escaping() -> None:
    # RFC 8785 §3.2.3 sorting example (subset) and control-character escaping.
    value = {"\u20ac": "Euro", "\r": "CR", "\ufb33": "Hebrew", "1": "One", "\U0001f600": "Smiley",
             "\u0080": "Control", "\u00f6": "Latin"}
    assert canonicalize(value).decode() == (
        '{"\\r":"CR","1":"One","\u0080":"Control","\u00f6":"Latin","\u20ac":"Euro",'
        '"\U0001f600":"Smiley","\ufb33":"Hebrew"}'
    )
    assert canonicalize("\u0001\t\"\\") == b'"\\u0001\\t\\"\\\\"'


@pytest.mark.parametrize("bad", [math.nan, math.inf, 2**53, "\ud800", {1: "x"}, {"d": datetime.now()}])
def test_non_json_values_rejected(bad) -> None:
    with pytest.raises(HarnessError) as info:
        canonicalize(bad)
    assert info.value.code is ReasonCode.FEATURE_CONTRACT_INVALID


# --- AC-003 lint -----------------------------------------------------------------------

def test_valid_contract_lints_clean(data: dict) -> None:
    report = lint_contract(parse_contract(data), CTX)
    assert report.ok, report.to_dict()


def test_criterion_without_method_unverifiable(data: dict) -> None:
    del data["acceptance_criteria"][0]["verification_method"]
    assert "ACCEPTANCE_CRITERION_UNVERIFIABLE" in codes(lint_contract(parse_contract(data), CTX))


def test_gate_criterion_without_gates_unverifiable(data: dict) -> None:
    data["acceptance_criteria"][0]["gates"] = []
    assert "ACCEPTANCE_CRITERION_UNVERIFIABLE" in codes(lint_contract(parse_contract(data), CTX))


def test_gate_not_in_required_gates(data: dict) -> None:
    data["acceptance_criteria"][0]["gates"] = ["other-gate"]
    report = lint_contract(parse_contract(data), CTX)
    assert {"ACCEPTANCE_CRITERION_UNVERIFIABLE", "FEATURE_CONTRACT_INVALID"} <= codes(report)


def test_gate_above_required_level_unverifiable(data: dict) -> None:
    data["verification"]["required_level"] = "L1"
    report = lint_contract(parse_contract(data), CTX)
    assert any(f.location.startswith("acceptance_criteria/1") for f in report.findings)


def test_required_level_not_achievable(data: dict) -> None:
    data["verification"]["required_level"] = "L4"
    report = lint_contract(parse_contract(data), CTX)
    assert any(f.location == "verification.required_level" for f in report.findings)


def test_unknown_gate_level_reported(data: dict) -> None:
    report = lint_contract(parse_contract(data), LintContext())
    assert "FEATURE_CONTRACT_INVALID" in codes(report)


@pytest.mark.parametrize("glob", ["../outside/**", "/abs/**", "src/**x/y", "src//a", "src/[ab/x"])
def test_invalid_scope_globs(data: dict, glob: str) -> None:
    data["scope"]["allowed_paths"].append(glob)
    report = lint_contract(parse_contract(data), CTX)
    assert any(f.location.startswith("scope.allowed_paths") for f in report.findings)


def lite(data: dict) -> dict:
    data["profile"] = "lite"
    data["acceptance_criteria"] = data["acceptance_criteria"][:2]
    data["verification"]["qe_test_design"] = "off"
    return data


def test_lite_contract_eligible(data: dict) -> None:
    report = lint_contract(parse_contract(lite(data)), CTX)
    assert report.ok and report.lite_eligible


def test_lite_rejects_l4(data: dict) -> None:
    d = lite(data)
    d["verification"]["required_level"] = "L4"
    report = lint_contract(parse_contract(d), LintContext(gate_levels=GATES | {"sil": "L4"}))
    assert "PROFILE_ESCALATION_REQUIRED" in codes(report)


def test_lite_rejects_human_review(data: dict) -> None:
    d = lite(data)
    d["acceptance_criteria"].append({"id": "AC-003", "statement": "x", "verification_method": "human_review"})
    assert "PROFILE_ESCALATION_REQUIRED" in codes(lint_contract(parse_contract(d), CTX))


def test_lite_rejects_class_d_approval(data: dict) -> None:
    d = lite(data)
    d["approvals"]["required"] = [{"action": "flash-board", "class": "D"}]
    assert "PROFILE_ESCALATION_REQUIRED" in codes(lint_contract(parse_contract(d), CTX))


def test_lite_rejects_explicit_model_glob(data: dict) -> None:
    d = lite(data)
    d["scope"]["allowed_paths"].append("models/**/*.slx")
    assert "PROFILE_ESCALATION_REQUIRED" in codes(lint_contract(parse_contract(d), CTX))


def test_lite_rejects_existing_model_in_scope(data: dict) -> None:
    d = lite(data)
    ctx = LintContext(gate_levels=GATES, repository_paths=("src/blocks/adc/adc.slx", "README.md"))
    assert "PROFILE_ESCALATION_REQUIRED" in codes(lint_contract(parse_contract(d), ctx))


def test_lite_rejects_public_api(data: dict) -> None:
    d = lite(data)
    ctx = LintContext(gate_levels=GATES, public_api_paths=("src/blocks/adc/api/**",))
    assert "PROFILE_ESCALATION_REQUIRED" in codes(lint_contract(parse_contract(d), ctx))


def test_full_profile_reports_eligibility_without_error(data: dict) -> None:
    report = lint_contract(parse_contract(data), CTX)
    assert report.ok and report.lite_eligible is False  # human_review criterion


# --- AC-004 approval -----------------------------------------------------------------

def test_approve_binds_content_hash(data: dict) -> None:
    approved = approve(parse_contract(data), "jane.doe", context=CTX, now=NOW)
    assert approved.intake["approved_by"] == "jane.doe"
    assert approved.intake["approval_ref"] == "local"
    assert approved.intake["approved_at"] == "2026-11-24T12:00:00Z"
    assert approval_status(approved).valid


def test_unapproved_status(data: dict) -> None:
    status = approval_status(parse_contract(data))
    assert not status.valid and status.reason_code is ReasonCode.OWNER_APPROVAL_MISSING


@pytest.mark.parametrize("who", ["intake-001", "dev-002", "QE-7", "planner-1"])
def test_agent_identities_refused(data: dict, who: str) -> None:
    with pytest.raises(HarnessError) as info:
        approve(parse_contract(data), who, context=CTX)
    assert info.value.code is ReasonCode.APPROVAL_INVALID


def test_drafter_cannot_approve(data: dict) -> None:
    data["intake"]["drafted_by"] = "jane.doe"
    with pytest.raises(HarnessError) as info:
        approve(parse_contract(data), "jane.doe", context=CTX)
    assert info.value.code is ReasonCode.APPROVAL_INVALID


def test_approval_refused_inside_agent_session(data: dict, monkeypatch) -> None:
    monkeypatch.setenv(AGENT_INSTANCE_ENV, "dev-001")
    with pytest.raises(HarnessError):
        approve(parse_contract(data), "jane.doe", context=CTX)


def test_failing_lint_cannot_be_approved(data: dict) -> None:
    del data["acceptance_criteria"][0]["verification_method"]
    with pytest.raises(HarnessError) as info:
        approve(parse_contract(data), "jane.doe", context=CTX)
    assert info.value.code is ReasonCode.FEATURE_CONTRACT_INVALID


def test_edit_after_approval_invalidates(data: dict) -> None:
    approved = approve(parse_contract(data), "jane.doe", context=CTX)
    edited = copy.deepcopy(approved.data)
    edited["scope"]["allowed_paths"].append("src/**")
    status = approval_status(parse_contract(edited))
    assert not status.valid and status.reason_code is ReasonCode.APPROVAL_INVALID


def test_hand_written_approval_without_binding_invalid(data: dict) -> None:
    data["intake"]["approved_by"] = "jane.doe"
    assert not approval_status(parse_contract(data)).valid


def test_lock_requires_approval(data: dict) -> None:
    with pytest.raises(HarnessError) as info:
        lock_contract(parse_contract(data), baseline_commit="abc123", context=CTX)
    assert info.value.code is ReasonCode.OWNER_APPROVAL_MISSING


# --- AC-005 lock and mutation --------------------------------------------------------

def test_lock_record(data: dict) -> None:
    approved = approve(parse_contract(data), "jane.doe", context=CTX)
    lock = lock_contract(approved, baseline_commit="abc123", context=CTX)
    assert lock["contract_hash"] == approved.digest()
    assert lock["baseline_commit"] == "abc123"
    assert lock["scope"]["protected_paths"] == ["src/blocks/adc/legacy/**", "tests/golden/**"]
    assert "qe-tests/adc/**" in lock["artefact_classes"]["qe_test"]
    assert verify_lock(lock, approved) == []


def test_mutation_after_lock_detected(data: dict) -> None:
    approved = approve(parse_contract(data), "jane.doe", context=CTX)
    lock = lock_contract(approved, baseline_commit="abc123", context=CTX)
    mutated = copy.deepcopy(approved.data)
    mutated["verification"]["required_gates"].remove("codegen-compile-reference")
    with pytest.raises(HarnessError) as info:
        verify_lock(lock, parse_contract(mutated))
    assert info.value.code is ReasonCode.CONTRACT_MUTATED_AFTER_LOCK
    paths = [c["path"] for c in info.value.context["changes"]]
    assert any(p.startswith("/verification/required_gates") for p in paths)


def test_reformatting_after_lock_is_not_mutation(data: dict) -> None:
    approved = approve(parse_contract(data), "jane.doe", context=CTX)
    lock = lock_contract(approved, baseline_commit="abc123", context=CTX)
    import yaml

    reloaded = load_contract_text(yaml.safe_dump(approved.data, sort_keys=True, default_flow_style=True))
    assert verify_lock(lock, reloaded) == []


def test_contract_diff_fields() -> None:
    diff = contract_diff({"a": 1, "b": [1, 2], "c": {"d": 1}}, {"a": 2, "b": [1], "c": {"d": 1, "e": 3}})
    assert [(c.path, c.before, c.after) for c in diff] == [
        ("/a", 1, 2), ("/b/1", 2, None), ("/c/e", None, 3)]


# --- AC-009 discovery ------------------------------------------------------------------

def test_active_feature_discovery(tmp_path: Path) -> None:
    active = tmp_path / "features" / "active"
    active.mkdir(parents=True)
    with pytest.raises(HarnessError) as info:
        find_active_feature(tmp_path / "features")
    assert info.value.code is ReasonCode.NO_ACTIVE_FEATURE
    shutil.copy(FIX / "feature-001.yaml", active / "FEATURE-001.yaml")
    (active / "FEATURE-001.plan.yaml").write_text("kind: FeaturePlan\n")
    assert find_active_feature(tmp_path / "features").id == "FEATURE-001"
    shutil.copy(FIX / "feature-001.yaml", active / "FEATURE-002.yaml")
    with pytest.raises(HarnessError) as info:
        find_active_feature(tmp_path / "features")
    assert info.value.code is ReasonCode.MULTIPLE_ACTIVE_FEATURES


def test_missing_active_directory(tmp_path: Path) -> None:
    with pytest.raises(HarnessError) as info:
        find_active_feature(tmp_path / "features")
    assert info.value.code is ReasonCode.NO_ACTIVE_FEATURE


def test_classifier_defaults_from_lock_are_reusable(data: dict) -> None:
    approved = approve(parse_contract(data), "jane.doe", context=CTX)
    lock = lock_contract(approved, baseline_commit="abc", context=CTX)
    rebuilt = ArtefactClassifier(**{k: tuple(v) for k, v in lock["artefact_classes"].items()})
    assert rebuilt.classify("qe-tests/adc/t.m").value == "qe_test"
