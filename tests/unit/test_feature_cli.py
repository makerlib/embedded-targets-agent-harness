"""HK-002 AC-010: `harness feature create | lint | approve` and the audit event."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from et_harness.audit.log import AuditLog
from et_harness.cli.main import cli
from et_harness.cli.output import ExitCode
from et_harness.common.schemas import validation_errors
from et_harness.contracts.approval import approval_status
from et_harness.contracts.model import load_contract

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"
PROJECT = """api_version: embedded-targets.ai/v1
kind: ProjectConfig
project: {id: product, name: Product}
harness: {version: 0.1.0}
gate_levels: {matlab-unittest: L1, codegen-compile-reference: L3}
"""


@pytest.fixture
def root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ET_HARNESS_ORG_POLICY", raising=False)
    monkeypatch.delenv("ET_HARNESS_USER_CONFIG", raising=False)
    monkeypatch.delenv("ET_HARNESS_AGENT_INSTANCE", raising=False)
    r = tmp_path / "product"
    (r / ".agent-harness").mkdir(parents=True)
    (r / ".agent-harness" / "project.yaml").write_text(PROJECT)
    (r / "features" / "backlog").mkdir(parents=True)
    shutil.copy(FIX / "feature-001.yaml", r / "features" / "backlog" / "FEATURE-001.yaml")
    return r


def run(*args: str):
    result = CliRunner().invoke(cli, list(args) + ["--json"], catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


def contract(root: Path) -> Path:
    return root / "features" / "backlog" / "FEATURE-001.yaml"


def test_create_writes_lintable_contract(root: Path) -> None:
    code, out = run("feature", "create", "FEATURE-002", "--title", 'Say "hi"', "--owner", "jane",
                    "--project-root", str(root))
    assert code == 0, out
    path = Path(out["data"]["path"])
    assert path == root / "features" / "backlog" / "FEATURE-002.yaml"
    created = load_contract(path)
    assert created.data["metadata"]["title"] == 'Say "hi"'
    code, out = run("feature", "lint", str(path), "--project-root", str(root))
    assert code == 0, out


def test_create_refuses_duplicates_and_bad_ids(root: Path) -> None:
    code, out = run("feature", "create", "FEATURE-001", "--title", "x", "--owner", "jane", "--project-root", str(root))
    assert code == ExitCode.REJECTED and out["reason_codes"] == ["FEATURE_CONTRACT_INVALID"]
    code, _ = run("feature", "create", "not an id", "--title", "x", "--owner", "jane", "--project-root", str(root))
    assert code == ExitCode.REJECTED


def test_lint_clean_and_reports_hashes(root: Path) -> None:
    code, out = run("feature", "lint", str(contract(root)), "--project-root", str(root))
    assert code == 0
    assert out["data"]["contract_hash"].startswith("sha256:")
    assert out["data"]["approval"]["reason_code"] == "OWNER_APPROVAL_MISSING"


def test_lint_failure_exit_code(root: Path) -> None:
    text = contract(root).read_text().replace("    verification_method: human_review\n", "")
    contract(root).write_text(text)
    code, out = run("feature", "lint", str(contract(root)), "--project-root", str(root))
    assert code == ExitCode.REJECTED
    assert "ACCEPTANCE_CRITERION_UNVERIFIABLE" in out["reason_codes"]


def test_lint_invalid_contract(root: Path) -> None:
    contract(root).write_text("api_version: embedded-targets.ai/v1\nkind: FeatureContract\n")
    code, out = run("feature", "lint", str(contract(root)), "--project-root", str(root))
    assert code == ExitCode.REJECTED and out["reason_codes"] == ["FEATURE_CONTRACT_INVALID"]


def test_approve_records_bound_approval_and_keeps_comments(root: Path) -> None:
    path = contract(root)
    path.write_text("# Owner notes stay here.\n" + path.read_text())
    code, out = run("feature", "approve", str(path), "--approver", "jane.doe", "--project-root", str(root))
    assert code == 0, out
    assert path.read_text().startswith("# Owner notes stay here.\n")
    approved = load_contract(path)
    assert approval_status(approved).valid
    assert out["data"]["contract_hash"] == approved.digest()


def test_approve_audits_valid_event(root: Path) -> None:
    run("feature", "approve", str(contract(root)), "--approver", "jane.doe", "--project-root", str(root))
    entries = AuditLog(root / ".agent-harness" / "audit" / "audit.jsonl").entries()
    assert [(e["event_type"], e["result"]) for e in entries] == [("approval.recorded", "VALID")]
    assert validation_errors(entries[0], "audit-record") == []


def test_agent_approval_refused_and_audited(root: Path) -> None:
    before = contract(root).read_bytes()
    code, out = run("feature", "approve", str(contract(root)), "--approver", "dev-001", "--project-root", str(root))
    assert code == ExitCode.REJECTED and out["reason_codes"] == ["APPROVAL_INVALID"]
    assert contract(root).read_bytes() == before
    entries = AuditLog(root / ".agent-harness" / "audit" / "audit.jsonl").entries()
    assert entries[0]["result"] == "REJECTED" and entries[0]["reason_codes"] == ["APPROVAL_INVALID"]
    assert validation_errors(entries[0], "audit-record") == []


def test_approve_inside_agent_session_refused(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("ET_HARNESS_AGENT_INSTANCE", "dev-001")
    code, _ = run("feature", "approve", str(contract(root)), "--approver", "jane.doe", "--project-root", str(root))
    assert code == ExitCode.REJECTED


def test_edit_after_cli_approval_shows_in_lint(root: Path) -> None:
    run("feature", "approve", str(contract(root)), "--approver", "jane.doe", "--project-root", str(root))
    path = contract(root)
    path.write_text(path.read_text().replace("tests/adc/**", "tests/**"))
    code, out = run("feature", "lint", str(path), "--project-root", str(root))
    assert out["data"]["approval"]["valid"] is False
    assert "changed after approval" in out["data"]["approval"]["message"]


def test_draft_still_not_implemented() -> None:
    result = CliRunner().invoke(cli, ["feature", "draft", "--json"])
    assert result.exit_code == ExitCode.NOT_IMPLEMENTED
    assert json.loads(result.output)["data"]["planned_feature"] == "HK-007"
