"""Deterministic evaluator rules (§9.18, §22).

``evaluate`` is a pure function of ``EvaluationInputs``: no I/O, no clock, no
randomness. Every rule adds findings in one of four categories, and the
verdict follows a fixed precedence:

1. ``integrity``    -> INCONCLUSIVE  (tamper, contract mismatch, corrupted evidence)
2. ``failure``      -> FAILED        (failed mandatory gate, unauthorised change, test weakening)
3. ``blocked``      -> BLOCKED       (unavailable dependency, approval or review)
4. ``insufficient`` -> INCONCLUSIVE  (missing or inconclusive evidence, open checks)
5. no findings      -> PASSED

A PASSED verdict is advisory, with VERDICT_NOT_AUTHORITATIVE, unless every
mandatory gate attempt ran under the ci or hardware-lab profile and the run's
authority was verified (HK-013).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from et_harness.common.reason_codes import ReasonCode as R

LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
CATEGORIES = ("integrity", "failure", "blocked", "insufficient")
VERDICT_FOR = {"integrity": "INCONCLUSIVE", "failure": "FAILED", "blocked": "BLOCKED",
               "insufficient": "INCONCLUSIVE"}
SCOPE_CODES = {R.PATH_OUTSIDE_SCOPE.value, R.PROTECTED_PATH_CHANGED.value, R.QE_TEST_MODIFIED.value,
               R.DERIVED_ARTEFACT_COMMITTED.value}
AUTHORITATIVE_PROFILES = ("ci", "hardware-lab")


@dataclass(frozen=True)
class GateEvidence:
    gate_id: str
    level: str
    result: str
    execution_profile: str
    attempt: int
    test_counts: dict[str, int] | None = None
    baseline_counts: dict[str, int] | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalEvidence:
    kind: str          # human_review | model_review | signoff | class_de
    subject: str       # criterion ID, model path, level or action
    source: str        # local | gitlab
    valid: bool


@dataclass
class EvaluationInputs:
    feature_id: str
    workflow_id: str
    profile: str
    contract: dict[str, Any]
    contract_hash_locked: str | None
    contract_hash_current: str | None
    workflow_exists: bool = True
    state_integrity_ok: bool = True
    rework_cycle: int = 0
    gates: list[GateEvidence] = field(default_factory=list)      # latest verification attempt per gate
    manifest_present: bool = False
    manifest_integrity_ok: bool = False
    manifest_complete: bool = False
    manifest_missing: list[str] = field(default_factory=list)
    manifest_not_implemented: list[str] = field(default_factory=list)
    manifest_added: list[str] = field(default_factory=list)
    scope_findings: list[dict[str, str]] = field(default_factory=list)
    changed_models: list[str] = field(default_factory=list)
    approvals: list[ApprovalEvidence] = field(default_factory=list)
    owner_approval_valid: bool = True
    owner_approval_message: str = ""
    blocker: dict[str, Any] | None = None
    open_checks: list[str] = field(default_factory=list)
    authority_verified: bool = False


@dataclass(frozen=True)
class Finding:
    rule: str
    category: str
    reason_code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "category": self.category, "reason_code": self.reason_code,
                "message": self.message}


@dataclass
class EvaluationResult:
    verdict: str
    authority: str
    execution_profile: str
    reason_codes: list[str]
    findings: list[Finding]
    criteria: list[dict[str, Any]]
    required_level: str
    highest_level_attempted: str | None
    highest_level_passed: str | None
    mandatory_gates_passed: bool
    failed_gates: list[str]
    human_review_required: bool
    human_review_reason: str | None


def _level_ladder(gates: list[GateEvidence]) -> tuple[str | None, str | None]:
    if not gates:
        return None, None
    attempted = max((g.level for g in gates), key=LEVELS.index)
    passed = None
    for level in LEVELS:
        at = [g for g in gates if g.level == level]
        if not at:
            continue
        if all(g.result == "PASSED" for g in at):
            passed = level
        else:
            break
    return attempted, passed


def _approved(inputs: EvaluationInputs, kind: str, subject: str) -> tuple[bool, str]:
    """Return (ok, reason_code_if_not) for an approval that must be GitLab-verified (§9.13)."""
    matches = [a for a in inputs.approvals if a.kind == kind and a.subject == subject and a.valid]
    if not matches:
        return False, R.HUMAN_REVIEW_REQUIRED.value if kind in ("human_review", "model_review") else R.APPROVAL_REQUIRED.value
    if not any(a.source == "gitlab" for a in matches):
        return False, R.APPROVAL_UNVERIFIABLE.value
    return True, ""


def evaluate(inputs: EvaluationInputs) -> EvaluationResult:
    findings: list[Finding] = []

    def add(rule: str, category: str, code: R | str, message: str) -> None:
        findings.append(Finding(rule, category, code.value if isinstance(code, R) else code, message))

    contract = inputs.contract
    verification = contract.get("verification") or {}
    required_level = verification.get("required_level", "L0")
    mandatory = list(verification.get("required_gates") or [])

    # --- integrity --------------------------------------------------------------------
    if not inputs.workflow_exists:
        add("workflow", "insufficient", R.EXECUTION_NOT_STARTED,
            "no kernel workflow, state chain or immutable gate attempts exist for this record")
    elif not inputs.state_integrity_ok:
        add("state-integrity", "integrity", R.STATE_TAMPER_DETECTED, "state or audit integrity chain is broken")
    if inputs.contract_hash_locked is None:
        add("contract-hash", "integrity", R.CONTRACT_HASH_MISMATCH, "contract was never locked")
    elif inputs.contract_hash_current != inputs.contract_hash_locked:
        add("contract-hash", "integrity", R.CONTRACT_HASH_MISMATCH,
            f"locked {inputs.contract_hash_locked} but current {inputs.contract_hash_current}")
    if inputs.manifest_present and not inputs.manifest_integrity_ok:
        add("evidence-integrity", "integrity", R.EVIDENCE_MANIFEST_INCOMPLETE,
            "evidence files do not match the manifest hashes")

    # --- failures ---------------------------------------------------------------------
    for f in inputs.scope_findings:
        code = f.get("reason_code", R.PATH_OUTSIDE_SCOPE.value)
        add("scope", "failure", code if code in SCOPE_CODES else R.PATH_OUTSIDE_SCOPE, f"{code}: {f.get('path')}")
    latest = {g.gate_id: g for g in inputs.gates}
    failed_gates = []
    for gate_id in mandatory:
        gate = latest.get(gate_id)
        if gate is None:
            add(f"gate:{gate_id}", "insufficient", R.GATE_EVIDENCE_MISSING,
                f"{gate_id} has no verification attempt in rework cycle {inputs.rework_cycle}")
        elif gate.result == "FAILED":
            failed_gates.append(gate_id)
            # §27 has no "mandatory gate failed" code (spec finding for 1.1.2): the finding
            # carries the attempt's own codes, if any, and the verdict lists failed_gates.
            add(f"gate:{gate_id}", "failure", gate.reason_codes[0] if gate.reason_codes else "",
                f"{gate_id} attempt {gate.attempt} FAILED")
        elif gate.result == "BLOCKED":
            add(f"gate:{gate_id}", "blocked", gate.reason_codes[0] if gate.reason_codes
                else R.MANDATORY_DEPENDENCY_UNAVAILABLE, f"{gate_id} attempt {gate.attempt} BLOCKED")
        elif gate.result == "INCONCLUSIVE":
            add(f"gate:{gate_id}", "insufficient", gate.reason_codes[0] if gate.reason_codes
                else R.GATE_EVIDENCE_MISSING, f"{gate_id} attempt {gate.attempt} INCONCLUSIVE")
        if gate is not None and gate.test_counts and gate.baseline_counts \
                and gate.test_counts["run"] < gate.baseline_counts["run"]:
            add(f"test-weakening:{gate_id}", "failure", R.TEST_WEAKENING_SUSPECTED,
                f"{gate_id} ran {gate.test_counts['run']} tests, baseline {gate.baseline_counts['run']}")

    # --- criteria ---------------------------------------------------------------------
    criteria = []
    for criterion in contract.get("acceptance_criteria") or []:
        method = criterion.get("verification_method")
        entry = {"id": criterion["id"], "verification_method": method, "status": "pending", "gates": {}}
        if method == "gate":
            results = {g: (latest[g].result if g in latest else None) for g in criterion.get("gates") or []}
            entry["gates"] = results
            if results and all(r == "PASSED" for r in results.values()):
                entry["status"] = "passed"
            elif any(r == "FAILED" for r in results.values()):
                entry["status"] = "failed"
        elif method == "human_review":
            ok, code = _approved(inputs, "human_review", criterion["id"])
            entry["status"] = "passed" if ok else "awaiting_review"
            if not ok:
                add(f"criterion:{criterion['id']}", "blocked", code,
                    f"{criterion['id']} needs a GitLab-verified human_review approval")
        else:
            add(f"criterion:{criterion['id']}", "insufficient", R.ACCEPTANCE_CRITERION_UNVERIFIABLE,
                f"{criterion['id']} has no verification method")
        criteria.append(entry)

    # --- reviews and approvals ----------------------------------------------------------
    for model in inputs.changed_models:
        ok, code = _approved(inputs, "model_review", model)
        if not ok:
            add(f"model:{model}", "blocked", code if code != R.HUMAN_REVIEW_REQUIRED.value else R.MODEL_DIFF_UNAVAILABLE,
                f"changed model {model} has no verified model diff review")
    if LEVELS.index(required_level) >= LEVELS.index("L5"):
        ok, code = _approved(inputs, "signoff", required_level)
        if not ok:
            add("signoff", "blocked", R.APPROVAL_UNVERIFIABLE if code != R.APPROVAL_REQUIRED.value else code,
                f"{required_level} requires a GitLab-verified human sign-off")
    for item in (contract.get("approvals") or {}).get("required") or []:
        ok, code = _approved(inputs, "class_de", item["action"])
        if not ok:
            add(f"approval:{item['action']}", "blocked", code,
                f"Class {item['class']} action {item['action']} needs a GitLab-verified approval")
    if not inputs.owner_approval_valid:
        add("owner-approval", "blocked", R.APPROVAL_INVALID, inputs.owner_approval_message or "owner approval invalid")
    if inputs.blocker:
        for code in inputs.blocker.get("reason_codes") or [inputs.blocker.get("reason_code")]:
            add("blocker", "blocked", code, inputs.blocker.get("message") or "unresolved blocker")

    # --- evidence sufficiency -------------------------------------------------------------
    if not inputs.manifest_present:
        add("evidence-manifest", "insufficient", R.EVIDENCE_MANIFEST_INCOMPLETE, "no evidence manifest; run collect")
    elif inputs.manifest_added:
        add("evidence-manifest", "insufficient", R.EVIDENCE_MANIFEST_INCOMPLETE,
            f"files added after collection: {', '.join(inputs.manifest_added[:5])}; re-run collect")
    elif not inputs.manifest_complete:
        if inputs.manifest_missing:
            add("evidence-manifest", "insufficient", R.EVIDENCE_MANIFEST_INCOMPLETE,
                f"missing evidence: {', '.join(inputs.manifest_missing)}")
        if inputs.manifest_not_implemented:
            add("evidence-manifest", "insufficient", R.NOT_IMPLEMENTED,
                f"evidence not producible yet: {', '.join(inputs.manifest_not_implemented)}")
    for check in inputs.open_checks:
        add(f"check:{check}", "insufficient", R.NOT_IMPLEMENTED, f"initialisation check {check} is open")

    attempted, passed = _level_ladder([g for g in inputs.gates if g.gate_id in mandatory])
    if passed is None or LEVELS.index(passed) < LEVELS.index(required_level):
        add("verification-level", "insufficient", R.GATE_EVIDENCE_MISSING,
            f"highest level passed {passed or 'none'}, required {required_level}")

    # --- verdict --------------------------------------------------------------------------
    present = {f.category for f in findings}
    category = next((c for c in CATEGORIES if c in present), None)
    verdict = VERDICT_FOR[category] if category else "PASSED"
    profiles = {latest[g].execution_profile for g in mandatory if g in latest}
    authoritative = (inputs.authority_verified and bool(profiles)
                     and profiles <= set(AUTHORITATIVE_PROFILES) and len(latest) >= len(mandatory))
    authority = "authoritative" if authoritative else "advisory"
    codes = [f.reason_code for f in findings if f.reason_code]
    if verdict == "PASSED" and not authoritative:
        codes.append(R.VERDICT_NOT_AUTHORITATIVE.value)
    review_codes = {R.HUMAN_REVIEW_REQUIRED.value, R.TEST_WEAKENING_SUSPECTED.value, R.MODEL_DIFF_UNAVAILABLE.value}
    review = [f for f in findings if f.reason_code in review_codes]
    return EvaluationResult(
        verdict=verdict, authority=authority,
        execution_profile=profiles.pop() if len(profiles) == 1 else "developer",
        reason_codes=list(dict.fromkeys(codes)), findings=findings, criteria=criteria,
        required_level=required_level, highest_level_attempted=attempted, highest_level_passed=passed,
        mandatory_gates_passed=bool(mandatory) and all(latest.get(g) and latest[g].result == "PASSED" for g in mandatory),
        failed_gates=failed_gates, human_review_required=bool(review),
        human_review_reason="; ".join(f.message for f in review) or None,
    )
