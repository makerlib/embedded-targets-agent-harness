"""Feature contract loading, validation and hashing (§9.4, §9.4.1)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.jcs import contract_digest, ensure_json_data_model
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document
from et_harness.common.yaml_io import load_yaml_file, load_yaml_text

APPROVAL_FIELDS = ("approved_by", "approval_ref", "approved_at", "approved_content_hash")


@dataclass(frozen=True)
class FeatureContract:
    """A validated contract document and where it came from."""

    data: dict[str, Any]
    source: str

    @property
    def id(self) -> str:
        return self.data["metadata"]["id"]

    @property
    def profile(self) -> str:
        return self.data["profile"]

    @property
    def scope(self) -> dict[str, Any]:
        return self.data["scope"]

    @property
    def allowed_paths(self) -> list[str]:
        return list(self.scope["allowed_paths"])

    @property
    def protected_paths(self) -> list[str]:
        return list(self.scope.get("protected_paths") or [])

    @property
    def qe_test_paths(self) -> list[str]:
        return list(self.scope.get("qe_test_paths") or [])

    @property
    def required_level(self) -> str:
        return self.data["verification"]["required_level"]

    @property
    def required_gates(self) -> list[str]:
        return list(self.data["verification"]["required_gates"])

    @property
    def criteria(self) -> list[dict[str, Any]]:
        return list(self.data["acceptance_criteria"])

    @property
    def intake(self) -> dict[str, Any]:
        return self.data["intake"]

    def digest(self) -> str:
        """Hash of the whole contract, including the approval (the locked hash)."""
        return contract_digest(self.data)

    def content_digest(self) -> str:
        """Hash of the contract with approval fields cleared.

        An approval binds to this value, so any later edit to the substance of
        the contract invalidates the approval.
        """
        return contract_digest(strip_approval(self.data))


def strip_approval(data: dict[str, Any]) -> dict[str, Any]:
    """Canonical pre-approval form: required approval keys null, optional ones absent.

    Both an unapproved contract and the same contract after approval reduce to
    the same value, so the approval's content hash is reproducible.
    """
    stripped = copy.deepcopy(data)
    intake = stripped.get("intake")
    if isinstance(intake, dict):
        for name in APPROVAL_FIELDS:
            intake.pop(name, None)
        intake["approved_by"] = None
        intake["approval_ref"] = None
    return stripped


def parse_contract(data: dict[str, Any], *, source: str = "<contract>") -> FeatureContract:
    ensure_json_data_model(data)
    validate_document(data, "feature", source=source, code=ReasonCode.FEATURE_CONTRACT_INVALID)
    ids = [c["id"] for c in data["acceptance_criteria"]]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID,
                           f"{source}: duplicate acceptance criterion IDs {duplicates}",
                           {"source": source})
    return FeatureContract(data, source)


def load_contract(path: Path) -> FeatureContract:
    try:
        data = load_yaml_file(Path(path))
    except HarnessError as exc:
        raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID, exc.message, exc.context,
                           details=exc.details) from exc
    return parse_contract(data, source=str(path))


def load_contract_text(text: str, *, source: str = "<string>") -> FeatureContract:
    try:
        data = load_yaml_text(text, source=source)
    except HarnessError as exc:
        raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID, exc.message, exc.context,
                           details=exc.details) from exc
    return parse_contract(data, source=source)
