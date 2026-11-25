"""Shared helpers: a synthetic product repository with approved FEATURE-001 (HK-004/HK-005 tests)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from et_harness.cli.main import cli
from et_harness.contracts.approval import approve
from et_harness.contracts.lint import LintContext
from et_harness.contracts.model import load_contract, parse_contract
from et_harness.orchestration.service import WorkflowService
from et_harness.orchestration.states import State

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "contracts" / "feature-001.yaml"
GATES = REPO / "tests" / "fixtures" / "gates"
LEVELS = {"matlab-unittest": "L1", "codegen-compile-reference": "L3"}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def install_gate(root: Path, gate_id: str, level: str, extra: str = "", baseline: str = "record",
                 preconditions: str = "") -> None:
    text = (GATES / "synthetic-gate.yaml.tmpl").read_text()
    for key, value in {"gate_id": gate_id, "level": level, "script": str(GATES / "synthetic_suite.py"),
                       "extra": extra}.items():
        text = text.replace("{" + key + "}", value)
    text = text.replace("baseline: record", f"baseline: {baseline}")
    if preconditions:
        text = text.replace("actions:", f"preconditions:\n{preconditions}\nactions:", 1)
    gates = root / ".agent-harness" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / f"{gate_id}.yaml").write_text(text)


def make_product(tmp_path: Path, *, lite: bool = False, max_cycles: int = 3, unit_extra: str = ', "--require", "src/blocks/adc/trigger.c"',
                 codegen_baseline: str = "record", codegen_extra: str = "", codegen_pre: str = "") -> Path:
    root = tmp_path / "product"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / ".gitignore").write_text("".join(f".agent-harness/{d}/\n" for d in
                                             ("state", "handoffs", "evidence", "audit", "cache")))
    (root / ".agent-harness").mkdir()
    (root / ".agent-harness" / "project.yaml").write_text(
        "api_version: embedded-targets.ai/v1\nkind: ProjectConfig\nproject: {id: p, name: P}\n"
        "harness: {version: 0.1.0}\n")
    install_gate(root, "matlab-unittest", "L1", unit_extra)
    install_gate(root, "codegen-compile-reference", "L3", codegen_extra, codegen_baseline, codegen_pre)
    (root / "src" / "blocks" / "adc").mkdir(parents=True)
    (root / "src" / "blocks" / "adc" / "adc.c").write_text("int adc;\n")
    data = load_contract(FIXTURE).data
    data["rework"]["maximum_cycles"] = max_cycles
    if lite:
        data["profile"] = "lite"
        data["acceptance_criteria"] = data["acceptance_criteria"][:2]
        data["verification"]["qe_test_design"] = "off"
    approved = approve(parse_contract(data), "jane.doe", context=LintContext(gate_levels=LEVELS))
    (root / "features" / "active").mkdir(parents=True)
    (root / "features" / "active" / "FEATURE-001.yaml").write_text(yaml.safe_dump(approved.data, sort_keys=False))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "baseline")
    return root


def walk(svc: WorkflowService, *steps: tuple[State, str]) -> dict:
    doc = svc.open().document
    engine = svc._engine(doc["workflow"]["id"])
    for target, reason in steps:
        doc = engine.transition(doc["workflow"]["revision"], target, reason)
    return doc


TO_VERIFYING_LITE = [(State.PLAN_REVIEW, "PLAN_SUBMITTED"), (State.DEVELOPING, "PLAN_ACCEPTED"),
                     (State.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"), (State.VERIFYING, "HANDOFF_ACCEPTED")]


def run_cli(root: Path, *args: str):
    result = CliRunner().invoke(cli, [*args, "--project-root", str(root), "--json"], catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


