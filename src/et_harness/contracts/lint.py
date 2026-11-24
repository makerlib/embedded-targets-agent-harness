"""Feature-contract lint (§9.4.1 rules, §13.5 lite eligibility, UC-22).

Lint findings are structured and reason-coded; lint never mutates a contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from et_harness.artefacts.classes import ArtefactClass, ArtefactClassifier
from et_harness.common.paths import glob_matches, validate_glob
from et_harness.common.reason_codes import ReasonCode
from et_harness.contracts.model import FeatureContract

LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")

# Built-in gate levels. Package-supplied gates are registered in HK-004; until
# then projects declare levels in project.yaml `gate_levels`.
BUILTIN_GATE_LEVELS: dict[str, str] = {"structural": "L0"}


@dataclass(frozen=True)
class LintFinding:
    reason_code: ReasonCode
    location: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"reason_code": self.reason_code.value, "location": self.location, "message": self.message}


@dataclass
class LintReport:
    contract_id: str
    findings: list[LintFinding] = field(default_factory=list)
    lite_eligible: bool | None = None

    @property
    def ok(self) -> bool:
        return not self.findings

    def reason_codes(self) -> list[ReasonCode]:
        out: list[ReasonCode] = []
        for f in self.findings:
            if f.reason_code not in out:
                out.append(f.reason_code)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "ok": self.ok,
            "lite_eligible": self.lite_eligible,
            "reason_codes": [c.value for c in self.reason_codes()],
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass(frozen=True)
class LintContext:
    """Project facts lint needs. Defaults are the kernel's built-ins."""

    gate_levels: dict[str, str] = field(default_factory=dict)
    public_api_paths: tuple[str, ...] = ()
    classifier: ArtefactClassifier = field(default_factory=ArtefactClassifier)
    lite_max_level: str = "L3"
    # Tracked repository files. When given, lite eligibility also fails if an
    # existing model file falls inside allowed_paths.
    repository_paths: tuple[str, ...] = ()

    def level_of(self, gate: str) -> str | None:
        return self.gate_levels.get(gate) or BUILTIN_GATE_LEVELS.get(gate)


def lint_contract(contract: FeatureContract, context: LintContext | None = None) -> LintReport:
    ctx = context or LintContext()
    report = LintReport(contract.id)
    add = lambda code, where, msg: report.findings.append(LintFinding(code, where, msg))  # noqa: E731

    required_level = contract.required_level
    required_gates = contract.required_gates
    req_index = LEVELS.index(required_level)

    # --- gates: known, and the required level is achievable -------------------
    known_levels: dict[str, str] = {}
    for gate in required_gates:
        level = ctx.level_of(gate)
        if level is None:
            add(ReasonCode.FEATURE_CONTRACT_INVALID, f"verification.required_gates/{gate}",
                f"gate '{gate}' has no known verification level")
        else:
            known_levels[gate] = level
    if known_levels and max(LEVELS.index(l) for l in known_levels.values()) < req_index:
        add(ReasonCode.ACCEPTANCE_CRITERION_UNVERIFIABLE, "verification.required_level",
            f"required level {required_level} is above every required gate's level")

    # --- acceptance criteria --------------------------------------------------
    for i, criterion in enumerate(contract.criteria):
        where = f"acceptance_criteria/{i} ({criterion['id']})"
        method = criterion.get("verification_method")
        gates = criterion.get("gates") or []
        if method is None:
            add(ReasonCode.ACCEPTANCE_CRITERION_UNVERIFIABLE, where, "criterion has no verification_method")
            continue
        if method == "human_review":
            if gates:
                add(ReasonCode.FEATURE_CONTRACT_INVALID, where, "human_review criteria must not list gates")
            continue
        if not gates:
            add(ReasonCode.ACCEPTANCE_CRITERION_UNVERIFIABLE, where, "gate criterion lists no gates")
            continue
        usable = [
            g for g in gates
            if g in required_gates and g in known_levels and LEVELS.index(known_levels[g]) <= req_index
        ]
        if not usable:
            add(ReasonCode.ACCEPTANCE_CRITERION_UNVERIFIABLE, where,
                "no listed gate is in required_gates at or below the required level")
        for g in gates:
            if g not in required_gates:
                add(ReasonCode.FEATURE_CONTRACT_INVALID, where, f"gate '{g}' is not in required_gates")

    # --- scope globs ------------------------------------------------------------
    for key in ("allowed_paths", "protected_paths", "qe_test_paths"):
        for j, glob in enumerate(contract.scope.get(key) or []):
            error = validate_glob(glob)
            if error:
                add(ReasonCode.FEATURE_CONTRACT_INVALID, f"scope.{key}/{j}", f"{glob!r}: {error}")

    # --- lite eligibility (§13.5) ---------------------------------------------
    reasons = lite_ineligibility(contract, ctx)
    report.lite_eligible = not reasons
    if contract.profile == "lite":
        for where, message in reasons:
            add(ReasonCode.PROFILE_ESCALATION_REQUIRED, where, message)
    return report


def lite_ineligibility(contract: FeatureContract, ctx: LintContext) -> list[tuple[str, str]]:
    reasons: list[tuple[str, str]] = []
    if LEVELS.index(contract.required_level) > LEVELS.index(ctx.lite_max_level):
        reasons.append(("verification.required_level",
                        f"lite allows at most {ctx.lite_max_level}; contract requires {contract.required_level}"))
    for i, approval in enumerate(contract.data["approvals"]["required"]):
        if approval["class"] in ("D", "E"):
            reasons.append((f"approvals.required/{i}", f"Class {approval['class']} action requires the full profile"))
    for j, glob in enumerate(contract.allowed_paths):
        model_patterns = ctx.classifier.patterns(ArtefactClass.MODEL)
        if any(glob_matches(m, glob) for m in model_patterns):
            reasons.append((f"scope.allowed_paths/{j}", f"{glob!r} targets model files"))
        models_in_scope = [p for p in ctx.repository_paths
                           if glob_matches(glob, p) and ctx.classifier.classify(p) is ArtefactClass.MODEL]
        if models_in_scope:
            reasons.append((f"scope.allowed_paths/{j}",
                            f"{glob!r} includes model files: {', '.join(sorted(models_in_scope)[:5])}"))
        for api in ctx.public_api_paths:
            if _overlaps(glob, api):
                reasons.append((f"scope.allowed_paths/{j}", f"{glob!r} overlaps public-API path {api!r}"))
    for i, criterion in enumerate(contract.criteria):
        if criterion.get("verification_method") != "gate":
            reasons.append((f"acceptance_criteria/{i}", "lite requires every criterion to use a gate"))
    return reasons


def _overlaps(a: str, b: str) -> bool:
    """Conservative overlap test between two globs: either could match a path the other names."""
    def sample(g: str) -> str:
        return g.replace("**", "x").replace("*", "x").replace("?", "x")
    return glob_matches(a, sample(b)) or glob_matches(b, sample(a)) or a == b
