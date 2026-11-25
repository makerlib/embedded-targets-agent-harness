"""`harness.schema-validator`: the built-in structural (L0) check (§12 L0, §20).

Validates, in-process: the workflow's feature contract (schema and lint), the
project configuration, the scope lock if one exists, and any extra files the
gate names with their schema. Writes ``structural-result.json``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.schemas import validation_errors
from et_harness.common.yaml_io import load_yaml_file
from et_harness.tools.base import ToolContext, ToolDescriptor, ToolResult


class SchemaValidatorTool:
    def describe(self) -> ToolDescriptor:
        return ToolDescriptor("harness.schema-validator", "1.0.0", "A", "process",
                              "Validates contract, project configuration and scope lock.")

    def validate_inputs(self, inputs: dict[str, Any]) -> list[str]:
        files = inputs.get("files", [])
        if not isinstance(files, list) or not all(
                isinstance(f, dict) and set(f) == {"path", "schema"} for f in files):
            return ["inputs.files must be a list of {path, schema}"]
        unknown = set(inputs) - {"files"}
        return [f"unknown inputs {sorted(unknown)}"] if unknown else []

    def execute(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult:
        from et_harness.contracts.context import project_context
        from et_harness.contracts.lint import lint_contract
        from et_harness.contracts.model import load_contract

        start = time.monotonic()
        checks: list[dict[str, Any]] = []

        def add(check_id: str, errors: list[str]) -> None:
            checks.append({"id": check_id, "ok": not errors, "errors": errors})

        root = context.repo_root
        workflow = context.workflow or {}
        contract_rel = (workflow.get("contract") or {}).get("path")
        if contract_rel:
            try:
                contract = load_contract(root / contract_rel)
                lint_ctx, _ = project_context(root, contract)
                report = lint_contract(contract, lint_ctx)
                add("feature-contract", [f"{f.reason_code.value} {f.location}: {f.message}" for f in report.findings])
            except HarnessError as exc:
                add("feature-contract", [exc.message, *exc.details])
        project = root / ".agent-harness" / "project.yaml"
        if project.is_file():
            try:
                add("project-config", validation_errors(load_yaml_file(project), "project-config"))
            except HarnessError as exc:
                add("project-config", [exc.message])
        lock = root / ".agent-harness" / "state" / "scope-lock.json"
        if lock.is_file():
            try:
                add("scope-lock", validation_errors(json.loads(lock.read_text(encoding="utf-8")), "scope-lock"))
            except ValueError as exc:
                add("scope-lock", [f"unreadable: {exc}"])
        for entry in inputs.get("files", []):
            path = root / entry["path"]
            try:
                document = (json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json"
                            else load_yaml_file(path))
                add(f"file:{entry['path']}", validation_errors(document, entry["schema"]))
            except (OSError, ValueError, HarnessError) as exc:
                add(f"file:{entry['path']}", [str(exc)])

        context.attempt_dir.mkdir(parents=True, exist_ok=True)
        out = context.attempt_dir / "structural-result.json"
        ok = all(c["ok"] for c in checks)
        out.write_text(json.dumps({"ok": ok, "checks": checks}, indent=2) + "\n", encoding="utf-8")
        return ToolResult(exit_code=0 if ok else 1, duration_ms=int((time.monotonic() - start) * 1000),
                          evidence={"structural-result": out})
