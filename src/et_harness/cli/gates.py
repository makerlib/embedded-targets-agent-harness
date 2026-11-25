"""`harness gate run`, `harness verify`, `harness evidence collect|validate` (§26)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from et_harness.cli.output import CommandResult, ExitCode
from et_harness.common.reason_codes import ReasonCode

PROFILE = click.option("--execution-profile", type=click.Choice(["developer", "qe", "ci", "hardware-lab"]),
                       default="developer", show_default=True)
ROOT = click.option("--project-root", type=click.Path(file_okay=False, path_type=Path), default=None)


def _service(project_root: Path | None):
    from et_harness.cli.feature import resolve_root
    from et_harness.orchestration.service import WorkflowService

    return WorkflowService(resolve_root(project_root))


def _codes(values) -> list[ReasonCode]:
    return [ReasonCode(v) for v in dict.fromkeys(values)]


def register(cli: click.Group, harness_command: Any) -> None:
    gate = click.Group("gate", help="Verification gate commands.")
    evidence = click.Group("evidence", help="Evidence bundle commands.")
    cli.add_command(gate)
    cli.add_command(evidence)

    @gate.command("run")
    @click.argument("gate_id")
    @PROFILE
    @click.option("--retry-reason", default=None, help="Required for a further attempt in the same cycle.")
    @ROOT
    @harness_command("gate run")
    def gate_run(gate_id: str, execution_profile: str, retry_reason: str | None,
                 project_root: Path | None) -> CommandResult:
        attempt = _service(project_root).run_gate(gate_id, execution_profile=execution_profile,
                                                  retry_reason=retry_reason)
        lines = [f"  {c['id']}: {c['status']} ({c.get('message', '')})" for c in attempt["conditions"]]
        lines += [f"  precondition {p['id']}: {p['status']} ({p['message']})" for p in attempt.get("preconditions", [])]
        if attempt["test_counts"]:
            lines.append(f"  tests: {attempt['test_counts']}")
        lines.append(f"  authority: {attempt['authority']} ({attempt['purpose']})")
        ok = attempt["result"] == "PASSED"
        exit_code = ExitCode.OK if ok else (ExitCode.BLOCKED if attempt["result"] == "BLOCKED" else ExitCode.REJECTED)
        return CommandResult("gate run", ok, f"{gate_id} attempt {attempt['attempt']}: {attempt['result']}",
                             attempt, _codes(attempt["reason_codes"]), exit_code, text_lines=lines)

    @cli.command("verify")
    @PROFILE
    @click.option("--retry-reason", default=None)
    @click.option("--resume", "resume_run", default=None, help="Resume an interrupted run by ID.")
    @ROOT
    @harness_command("verify")
    def verify(execution_profile: str, retry_reason: str | None, resume_run: str | None,
               project_root: Path | None) -> CommandResult:
        summary = _service(project_root).verify(execution_profile=execution_profile, retry_reason=retry_reason,
                                                resume_run=resume_run)
        lines = [f"  {g}: attempt {r['attempt']} {r['result']}" + (f" {r['test_counts']}" if r["test_counts"] else "")
                 for g, r in summary["gates"].items()]
        if summary["transition"]:
            lines.append(f"  workflow: {summary['transition']}")
        lines.append("  authority: advisory (CI issues the authoritative verdict)")
        all_passed = summary["gates"] and all(r["result"] == "PASSED" for r in summary["gates"].values())
        codes = _codes(c for r in summary["gates"].values() for c in r["reason_codes"])
        codes += [c for c in _codes(summary["reason_codes"]) if c not in codes]
        exit_code = ExitCode.OK if all_passed and summary["run_status"] == "completed" else ExitCode.REJECTED
        return CommandResult("verify", exit_code == ExitCode.OK,
                             f"{summary['run_id']}: {summary['run_status']}", summary, codes, exit_code,
                             text_lines=lines)

    @evidence.command("collect")
    @ROOT
    @harness_command("evidence collect")
    def evidence_collect(project_root: Path | None) -> CommandResult:
        from et_harness.evidence.bundle import collect

        out = collect(_service(project_root))
        integrity = out["manifest"]["integrity"]
        lines = [f"  bundle: {out['bundle']}", f"  files: {len(out['manifest']['artefacts'])}",
                 f"  missing: {', '.join(integrity['missing']) or 'none'}",
                 f"  not implemented yet: {', '.join(integrity['not_implemented']) or 'none'}",
                 f"  complete: {integrity['complete']}", "  authority: advisory"]
        return CommandResult("evidence collect", True,
                             f"collected; complete={integrity['complete']}",
                             {"bundle": out["bundle"], "integrity": integrity}, text_lines=lines)

    @evidence.command("validate")
    @ROOT
    @harness_command("evidence validate")
    def evidence_validate(project_root: Path | None) -> CommandResult:
        from et_harness.evidence.bundle import validate
        from et_harness.gates.runner import GateRunner

        service = _service(project_root)
        report = validate(GateRunner.bundle_dir(service.root, service.open().document))
        lines = [f"  {k}: {report[k]}" for k in ("manifest_hash_ok", "modified", "missing", "added",
                                                 "complete", "missing_items", "not_implemented_items")]
        if not report["integrity_ok"]:
            return CommandResult("evidence validate", False, "evidence integrity failure", report,
                                 [ReasonCode.EVIDENCE_MANIFEST_INCOMPLETE], ExitCode.REJECTED, text_lines=lines)
        if report["added"]:
            return CommandResult("evidence validate", False, "files added since the manifest; re-run collect",
                                 report, [ReasonCode.EVIDENCE_MANIFEST_INCOMPLETE], ExitCode.REJECTED, text_lines=lines)
        return CommandResult("evidence validate", True, f"hashes verified; complete={report['complete']}",
                             report, text_lines=lines)
