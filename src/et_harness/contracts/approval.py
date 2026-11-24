"""Local feature-owner approval (§9.4.1, §9.13, UC-22).

A local approval is a record in the contract's ``intake`` block. Its approver
is NOT authenticated (§9.13): it is valid only for advisory runs. GitLab
verification for authoritative runs arrives in HK-013.

The approval binds to the contract's content hash (approval fields cleared),
so any later edit to the contract invalidates it.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.contracts.lint import LintContext, lint_contract
from et_harness.contracts.model import FeatureContract, parse_contract

# Set by the kernel in every agent session it launches (HK-007). An approval
# requested from inside such a session is refused.
AGENT_INSTANCE_ENV = "ET_HARNESS_AGENT_INSTANCE"
AGENT_IDENTITY = re.compile(r"^(intake|planner|planning|dev|development|qe|evaluator|agent)-\d+$", re.I)
APPROVER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


@dataclass(frozen=True)
class ApprovalStatus:
    valid: bool
    reason_code: ReasonCode | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid,
                "reason_code": self.reason_code.value if self.reason_code else None,
                "message": self.message}


def check_approver(approver: str, contract: FeatureContract) -> None:
    if not APPROVER_PATTERN.match(approver or ""):
        raise HarnessError(ReasonCode.APPROVAL_INVALID, f"invalid approver identity {approver!r}")
    if AGENT_IDENTITY.match(approver):
        raise HarnessError(ReasonCode.APPROVAL_INVALID,
                           f"{approver!r} is an agent-instance identity; a human must approve")
    if os.environ.get(AGENT_INSTANCE_ENV):
        raise HarnessError(ReasonCode.APPROVAL_INVALID,
                           "approval requested from inside an agent session "
                           f"({AGENT_INSTANCE_ENV}={os.environ[AGENT_INSTANCE_ENV]})")
    if approver == contract.intake.get("drafted_by"):
        raise HarnessError(ReasonCode.APPROVAL_INVALID,
                           "the approver drafted this contract; another person must approve")


def approve(
    contract: FeatureContract,
    approver: str,
    *,
    context: LintContext | None = None,
    now: datetime | None = None,
) -> FeatureContract:
    """Return a new contract carrying a local owner approval."""
    report = lint_contract(contract, context)
    if not report.ok:
        raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID,
                           f"{contract.id} fails lint; fix it before approval",
                           {"findings": [f.to_dict() for f in report.findings]},
                           details=[f"{f.reason_code.value} {f.location}: {f.message}" for f in report.findings])
    check_approver(approver, contract)
    data = copy.deepcopy(contract.data)
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    data["intake"].update(
        approved_by=approver,
        approval_ref="local",
        approved_at=when.isoformat(timespec="seconds").replace("+00:00", "Z"),
        approved_content_hash=contract.content_digest(),
    )
    return parse_contract(data, source=contract.source)


def approval_status(contract: FeatureContract) -> ApprovalStatus:
    intake = contract.intake
    approver = intake.get("approved_by")
    if not approver:
        return ApprovalStatus(False, ReasonCode.OWNER_APPROVAL_MISSING, "contract has no owner approval")
    if AGENT_IDENTITY.match(approver) or approver == intake.get("drafted_by"):
        return ApprovalStatus(False, ReasonCode.APPROVAL_INVALID,
                              f"approver {approver!r} is an agent or the drafter")
    bound = intake.get("approved_content_hash")
    if bound is None:
        return ApprovalStatus(False, ReasonCode.APPROVAL_INVALID,
                              "approval is not bound to the contract content (approved_content_hash missing)")
    if bound != contract.content_digest():
        return ApprovalStatus(False, ReasonCode.APPROVAL_INVALID,
                              "contract changed after approval; approve it again")
    source = intake.get("approval_ref") or "local"
    return ApprovalStatus(True, None, f"approved by {approver} (source {source}, advisory only)")
