"""AC-009: reason-code registry (§27) and structured errors."""

from __future__ import annotations

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import RESERVED_V1_2, ReasonCode, is_emittable

SPEC_27 = """
CONFIG_INVALID SCHEMA_VERSION_UNSUPPORTED FEATURE_CONTRACT_INVALID MULTIPLE_ACTIVE_FEATURES
NO_ACTIVE_FEATURE BASELINE_DIRTY BASELINE_FAILED CONTRACT_HASH_MISMATCH
CONTRACT_MUTATED_AFTER_LOCK PATH_OUTSIDE_SCOPE PROTECTED_PATH_CHANGED PACKAGE_NOT_FOUND
PACKAGE_VERSION_CONFLICT PACKAGE_DEPENDENCY_CYCLE PACKAGE_DIGEST_MISMATCH TOOL_NOT_REGISTERED
TOOL_PERMISSION_DENIED APPROVAL_REQUIRED APPROVAL_INVALID LEASE_ALREADY_HELD LEASE_STALE
WORKFLOW_REVISION_CONFLICT TRANSITION_NOT_ALLOWED HANDOFF_INVALID HANDOFF_DIFF_MISMATCH
PLAN_COVERAGE_INCOMPLETE GATE_PRECONDITION_FAILED GATE_TIMEOUT GATE_EVIDENCE_MISSING
RETRY_LIMIT_EXCEEDED REWORK_LIMIT_EXCEEDED EVIDENCE_MANIFEST_INCOMPLETE ENVIRONMENT_DRIFT
MANDATORY_DEPENDENCY_UNAVAILABLE EXECUTION_NOT_STARTED NOT_IMPLEMENTED OWNER_APPROVAL_MISSING
ACCEPTANCE_CRITERION_UNVERIFIABLE PROFILE_ESCALATION_REQUIRED ENFORCEMENT_TIER_INSUFFICIENT
HOOK_DECISION_TIMEOUT STATE_TAMPER_DETECTED PROJECTION_DRIFT QE_TEST_MODIFIED
BASELINE_TESTS_NOT_FAILING TEST_WEAKENING_SUSPECTED DERIVED_ARTEFACT_COMMITTED
MODEL_DIFF_UNAVAILABLE HUMAN_REVIEW_REQUIRED APPROVAL_UNVERIFIABLE VERDICT_NOT_AUTHORITATIVE
MATLAB_UNAVAILABLE MATLAB_PRODUCT_MISSING LICENCE_UNAVAILABLE TOOLCHAIN_UNAVAILABLE
""".split()


def test_every_spec_code_is_defined_with_stable_value() -> None:
    for name in SPEC_27:
        assert ReasonCode[name].value == name


def test_reserved_codes_defined_but_not_emittable() -> None:
    assert {c.value for c in RESERVED_V1_2} == {"PACKAGE_REVOKED", "SENSITIVE_ARTEFACT_QUARANTINED"}
    assert not any(is_emittable(c) for c in RESERVED_V1_2)
    assert is_emittable(ReasonCode.CONFIG_INVALID)


def test_values_are_unique() -> None:
    values = [c.value for c in ReasonCode]
    assert len(values) == len(set(values))


def test_error_carries_code_and_redacted_context() -> None:
    err = HarnessError(
        ReasonCode.TOOL_PERMISSION_DENIED,
        "denied with password=hunter22",
        {"cmd": "deploy --api-key=abc123def", "count": 3},
        details=["token: zzz999"],
    )
    data = err.to_dict()
    assert data["reason_code"] == "TOOL_PERMISSION_DENIED"
    flat = str(data)
    for secret in ("hunter22", "abc123def", "zzz999"):
        assert secret not in flat
    assert data["context"]["count"] == 3
    assert str(err).startswith("TOOL_PERMISSION_DENIED:")
