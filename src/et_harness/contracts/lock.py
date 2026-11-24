"""Contract and scope lock, mutation detection and contract diff (§9.4, UC-04)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from et_harness import API_VERSION
from et_harness.artefacts.classes import ArtefactClassifier
from et_harness.common.errors import HarnessError
from et_harness.common.paths import validate_glob
from et_harness.common.reason_codes import ReasonCode
from et_harness.contracts.approval import approval_status
from et_harness.contracts.lint import LintContext, lint_contract
from et_harness.contracts.model import FeatureContract


@dataclass(frozen=True)
class ContractChange:
    path: str
    before: Any
    after: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "before": self.before, "after": self.after}


def contract_diff(before: Any, after: Any, path: str = "") -> list[ContractChange]:
    """Field-level differences between two JSON values (lists compared by index)."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[ContractChange] = []
        for key in sorted(set(before) | set(after)):
            sub = f"{path}/{key}"
            if key not in before:
                changes.append(ContractChange(sub, None, after[key]))
            elif key not in after:
                changes.append(ContractChange(sub, before[key], None))
            else:
                changes.extend(contract_diff(before[key], after[key], sub))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for i in range(max(len(before), len(after))):
            sub = f"{path}/{i}"
            if i >= len(before):
                changes.append(ContractChange(sub, None, after[i]))
            elif i >= len(after):
                changes.append(ContractChange(sub, before[i], None))
            else:
                changes.extend(contract_diff(before[i], after[i], sub))
        return changes
    return [] if before == after else [ContractChange(path or "/", before, after)]


def _normalise_globs(globs: list[str]) -> list[str]:
    return sorted({g.replace("\\", "/").strip() for g in globs})


def lock_contract(
    contract: FeatureContract,
    *,
    baseline_commit: str,
    classifier: ArtefactClassifier | None = None,
    project_classes: dict[str, list[str]] | None = None,
    context: LintContext | None = None,
) -> dict[str, Any]:
    """Return a ScopeLock record. Refuses unapproved or failing contracts."""
    status = approval_status(contract)
    if not status.valid:
        raise HarnessError(status.reason_code or ReasonCode.OWNER_APPROVAL_MISSING,
                           f"cannot lock {contract.id}: {status.message}")
    report = lint_contract(contract, context)
    if not report.ok:
        raise HarnessError(report.reason_codes()[0], f"cannot lock {contract.id}: contract fails lint",
                           {"findings": [f.to_dict() for f in report.findings]})
    for glob in contract.allowed_paths + contract.protected_paths + contract.qe_test_paths:
        if validate_glob(glob):
            raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID, f"invalid scope glob {glob!r}")
    classifier = classifier or ArtefactClassifier.build(
        protected_paths=contract.protected_paths,
        qe_test_paths=contract.qe_test_paths,
        project_classes=project_classes,
    )
    return {
        "api_version": API_VERSION,
        "kind": "ScopeLock",
        "feature_id": contract.id,
        "contract_path": contract.source,
        "contract_hash": contract.digest(),
        "content_hash": contract.content_digest(),
        "approval": {"approved_by": contract.intake["approved_by"],
                     "source": contract.intake.get("approval_ref") or "local"},
        "baseline_commit": baseline_commit,
        "profile": contract.profile,
        "scope": {
            "allowed_paths": _normalise_globs(contract.allowed_paths),
            "protected_paths": _normalise_globs(contract.protected_paths),
            "qe_test_paths": _normalise_globs(contract.qe_test_paths),
        },
        "artefact_classes": classifier.to_dict(),
        "contract": contract.data,
    }


def verify_lock(lock: dict[str, Any], current: FeatureContract) -> list[ContractChange]:
    """Raise CONTRACT_MUTATED_AFTER_LOCK if the contract differs from the locked one."""
    if current.digest() == lock["contract_hash"]:
        return []
    changes = contract_diff(lock["contract"], current.data)
    raise HarnessError(
        ReasonCode.CONTRACT_MUTATED_AFTER_LOCK,
        f"{current.id} changed after lock ({len(changes)} field(s))",
        {"locked_hash": lock["contract_hash"], "current_hash": current.digest(),
         "changes": [c.to_dict() for c in changes[:50]]},
        details=[f"{c.path}: {c.before!r} -> {c.after!r}" for c in changes[:50]],
    )
