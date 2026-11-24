"""`harness feature create | lint | approve` (§26, UC-22)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import click

from et_harness.cli.output import CommandResult, ExitCode
from et_harness.common.atomic import atomic_write_text
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.yaml_io import dump_yaml, load_yaml_text
from et_harness.contracts.discovery import FEATURE_DIRS
from et_harness.contracts.model import FeatureContract, load_contract, load_contract_text

FEATURE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*-[0-9]+$")


def template_dir() -> Path:
    override = os.environ.get("ET_HARNESS_TEMPLATE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "templates"


def resolve_root(project_root: Path | None) -> Path:
    if project_root is not None:
        return project_root.resolve()
    from et_harness.repository.adapters.git_cli import discover_root

    return discover_root(Path.cwd())


# --- create ---------------------------------------------------------------------

def create_contract(root: Path, feature_id: str, title: str, owner: str, profile: str,
                    drafted_by: str) -> Path:
    if not FEATURE_ID.match(feature_id):
        raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID,
                           f"feature ID {feature_id!r} must look like FEATURE-001")
    for sub in FEATURE_DIRS:
        existing = root / "features" / sub / f"{feature_id}.yaml"
        if existing.exists():
            raise HarnessError(ReasonCode.FEATURE_CONTRACT_INVALID,
                               f"{feature_id} already exists at {existing}")
    text = (template_dir() / "feature" / "feature.yaml").read_text(encoding="utf-8")
    values = {
        "feature_id": feature_id, "title": title, "owner": owner, "profile": profile,
        "qe_test_design": "off" if profile == "lite" else "optional", "drafted_by": drafted_by,
    }
    for key, value in values.items():
        text = text.replace(f'"{{{key}}}"', json.dumps(value, ensure_ascii=False))
    load_contract_text(text, source=f"{feature_id} (template)")
    target = root / "features" / "backlog" / f"{feature_id}.yaml"
    atomic_write_text(target, text)
    return target


# --- approve: rewrite only the intake block where possible ---------------------

def render_with_approval(original_text: str, approved: FeatureContract) -> str:
    """Return the file text with the intake approval fields updated.

    Keeps comments and formatting when the intake block is a simple two-space
    mapping; otherwise falls back to a full YAML dump. Either way the result is
    re-parsed and must equal the approved data exactly.
    """
    intake = approved.data["intake"]
    updates = {k: intake.get(k) for k in ("approved_by", "approval_ref", "approved_at", "approved_content_hash")}
    lines = original_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"^intake:\s*(#.*)?$", l))
    except StopIteration:
        start = None
    if start is not None:
        end = start + 1
        while end < len(lines) and (lines[end].startswith("  ") or not lines[end].strip()):
            end += 1
        block = lines[start + 1:end]
        remaining = dict(updates)
        new_block = []
        for line in block:
            m = re.match(r"^  ([a-z_]+):", line)
            if m and m.group(1) in remaining:
                key = m.group(1)
                new_block.append(f"  {key}: {json.dumps(remaining.pop(key))}")
            else:
                new_block.append(line)
        while new_block and not new_block[-1].strip():
            new_block.pop()
        new_block.extend(f"  {k}: {json.dumps(v)}" for k, v in remaining.items())
        text = "\n".join(lines[:start + 1] + new_block + lines[end:]) + "\n"
        try:
            if load_yaml_text(text) == approved.data:
                return text
        except HarnessError:
            pass
    return dump_yaml(approved.data)


def audit_approval(root: Path, contract: FeatureContract, result: str, approver: str,
                   reason: ReasonCode | None) -> str | None:
    from et_harness.audit.log import AuditLog

    approval_id = f"approval-{contract.id}-owner-{contract.content_digest()[7:19]}"
    AuditLog(root / ".agent-harness" / "audit" / "audit.jsonl").append(
        "approval.recorded",
        feature_id=contract.id,
        persona=None,
        approval_id=approval_id,
        result=result,
        reason_codes=[reason] if reason else [],
        details={"source": "local", "action": "feature-owner-approval", "approver": approver},
    )
    return approval_id


# --- click commands ----------------------------------------------------------------

def register(cli: click.Group, harness_command: Any) -> None:
    group = click.Group("feature", help="Feature contract commands.")
    cli.add_command(group)

    @group.command("create")
    @click.argument("feature_id")
    @click.option("--title", required=True)
    @click.option("--owner", required=True, help="Accountable feature owner (a human).")
    @click.option("--profile", type=click.Choice(["full", "lite"]), default="full", show_default=True)
    @click.option("--drafted-by", default=None, help="Who drafted it (default: the owner).")
    @click.option("--project-root", type=click.Path(file_okay=False, path_type=Path), default=None)
    @harness_command("feature create")
    def feature_create(feature_id: str, title: str, owner: str, profile: str,
                       drafted_by: str | None, project_root: Path | None) -> CommandResult:
        root = resolve_root(project_root)
        target = create_contract(root, feature_id, title, owner, profile, drafted_by or owner)
        return CommandResult("feature create", True, f"created {target.relative_to(root)}",
                             {"path": str(target), "feature_id": feature_id},
                             text_lines=["Next: edit the contract, then `harness feature lint` it."])

    @group.command("lint")
    @click.argument("path", type=click.Path(dir_okay=False, exists=True, path_type=Path))
    @click.option("--project-root", type=click.Path(file_okay=False, path_type=Path), default=None)
    @harness_command("feature lint")
    def feature_lint(path: Path, project_root: Path | None) -> CommandResult:
        from et_harness.contracts.approval import approval_status
        from et_harness.contracts.context import project_context
        from et_harness.contracts.lint import lint_contract

        contract = load_contract(path)
        root = resolve_root(project_root)
        context, _ = project_context(root, contract)
        report = lint_contract(contract, context)
        status = approval_status(contract)
        data = report.to_dict() | {"contract_hash": contract.digest(),
                                   "content_hash": contract.content_digest(),
                                   "approval": status.to_dict()}
        lines = [f"  {f.reason_code.value} {f.location}: {f.message}" for f in report.findings]
        lines.append(f"  lite eligible: {'yes' if report.lite_eligible else 'no'}")
        lines.append(f"  approval: {status.message}")
        if report.ok:
            return CommandResult("feature lint", True, f"{contract.id}: lint clean", data, text_lines=lines)
        return CommandResult("feature lint", False, f"{contract.id}: {len(report.findings)} finding(s)",
                             data, report.reason_codes(), ExitCode.REJECTED, text_lines=lines)

    @group.command("approve")
    @click.argument("path", type=click.Path(dir_okay=False, exists=True, path_type=Path))
    @click.option("--approver", required=True, help="Your identity as the human feature owner.")
    @click.option("--project-root", type=click.Path(file_okay=False, path_type=Path), default=None)
    @harness_command("feature approve")
    def feature_approve(path: Path, approver: str, project_root: Path | None) -> CommandResult:
        from et_harness.contracts.approval import approve
        from et_harness.contracts.context import project_context

        contract = load_contract(path)
        root = resolve_root(project_root)
        context, _ = project_context(root, contract)
        try:
            approved = approve(contract, approver, context=context)
        except HarnessError as exc:
            if exc.code is ReasonCode.APPROVAL_INVALID:
                audit_approval(root, contract, "REJECTED", approver, exc.code)
            raise
        original = path.read_text(encoding="utf-8")
        atomic_write_text(path, render_with_approval(original, approved))
        approval_id = audit_approval(root, approved, "VALID", approver, None)
        return CommandResult(
            "feature approve", True,
            f"{approved.id} approved by {approver} (local source: advisory runs only)",
            {"feature_id": approved.id, "approval_id": approval_id, "approved_by": approver,
             "source": "local", "content_hash": approved.intake["approved_content_hash"],
             "contract_hash": approved.digest()},
            text_lines=["Authoritative CI runs need a GitLab-verified approval (HK-013)."],
        )
