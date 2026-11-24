"""`harness workflow create | start | status | history | block | resume | cancel` (§26, §31)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from et_harness.cli.output import CommandResult, ExitCode
from et_harness.common.reason_codes import ReasonCode


def _service(project_root: Path | None):
    from et_harness.cli.feature import resolve_root
    from et_harness.orchestration.service import WorkflowService

    return WorkflowService(resolve_root(project_root))


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    wf = doc["workflow"]
    return {"workflow_id": wf["id"], "feature_id": wf["feature_id"], "state": wf["current_state"],
            "revision": wf["revision"], "resumable_state": wf["resumable_state"], "blocker": doc["blocker"],
            "checks": (doc.get("initialisation") or {}).get("checks")}


def _result(name: str, doc: dict[str, Any], message: str) -> CommandResult:
    data = _summary(doc)
    if data["state"] == "BLOCKED":
        codes = [ReasonCode(c) for c in (doc["blocker"] or {}).get("reason_codes", [])]
        lines = [f"  blocker: {doc['blocker'].get('message', '')}",
                 f"  resume state: {data['resumable_state']}",
                 "  Next: resolve the blocker, then `harness workflow resume`."]
        return CommandResult(name, False, f"{data['workflow_id']} is BLOCKED", data, codes,
                             ExitCode.BLOCKED, text_lines=lines)
    lines = []
    for check in data["checks"] or []:
        if check["status"] != "passed":
            lines.append(f"  {check['status'].upper()}: {check['id']} ({check.get('planned_feature') or check.get('reason_code')})")
    return CommandResult(name, True, message, data, text_lines=lines)


def register(cli: click.Group, harness_command: Any) -> None:
    group = click.Group("workflow", help="Workflow lifecycle commands.")
    cli.add_command(group)
    root_opt = click.option("--project-root", type=click.Path(file_okay=False, path_type=Path), default=None)

    @group.command("create")
    @root_opt
    @harness_command("workflow create")
    def create(project_root: Path | None) -> CommandResult:
        doc = _service(project_root).create()
        return CommandResult("workflow create", True, f"created {doc['workflow']['id']} (CREATED)",
                             _summary(doc), text_lines=["Next: `harness workflow start`."])

    @group.command("start")
    @root_opt
    @harness_command("workflow start")
    def start(project_root: Path | None) -> CommandResult:
        doc = _service(project_root).start()
        return _result("workflow start", doc, f"{doc['workflow']['id']} is {doc['workflow']['current_state']}")

    @group.command("status")
    @root_opt
    @harness_command("workflow status")
    def status(project_root: Path | None) -> CommandResult:
        s = _service(project_root).status()
        lines = [
            f"Workflow: {s['workflow_id']}", f"Feature: {s['feature_id']}", f"Profile: {s['profile']}",
            f"State: {s['state']}", f"Revision: {s['revision']}", f"Owner: {s['owner']}",
            "Contract: " + (f"locked, hash {s['contract']['hash']}" if s["contract"]["locked"] else "not locked yet"),
            f"Baseline commit: {s['baseline_commit'] or '-'}",
        ]
        if s["blocker"]:
            lines.append(f"Blocker: {', '.join(s['blocker'].get('reason_codes') or [s['blocker']['reason_code']])}"
                         f" - {s['blocker'].get('message', '')}")
        for check in s["open_checks"]:
            lines.append(f"Open check: {check['id']} {check['status']}"
                         + (f" (planned {check['planned_feature']})" if check.get("planned_feature") else ""))
        for finding in s["findings"]:
            lines.append(f"FINDING: {finding.get('reason_code')}: {finding.get('message')}")
        lines += [f"Required next action: {s['next_action']}", f"Verdict authority: {s['verdict_authority']}"]
        codes = [ReasonCode(f["reason_code"]) for f in s["findings"] if f.get("reason_code")]
        ok = not codes
        return CommandResult("workflow status", ok, f"{s['workflow_id']}: {s['state']}", s, codes,
                             ExitCode.OK if ok else ExitCode.REJECTED, text_lines=lines)

    @group.command("history")
    @root_opt
    @harness_command("workflow history")
    def history(project_root: Path | None) -> CommandResult:
        h = _service(project_root).history()
        lines = [f"  {e['sequence']:>3}  r{e.get('revision', '?'):<3} {e['from']} -> {e['to']}  ({e['reason']})"
                 for e in h["history"]]
        return CommandResult("workflow history", True, f"{h['workflow_id']}: {len(h['history'])} transition(s)",
                             h, text_lines=lines)

    @group.command("block")
    @click.option("--reason", "reasons", multiple=True, required=True, help="§27 reason code (repeatable).")
    @click.option("--message", default="", help="What is blocking and how to resolve it.")
    @click.option("--resume-state", default=None, help="State to resume to (default: the current state).")
    @root_opt
    @harness_command("workflow block")
    def block(reasons: tuple[str, ...], message: str, resume_state: str | None,
              project_root: Path | None) -> CommandResult:
        doc = _service(project_root).block(list(reasons), message, resume_state)
        return _result("workflow block", doc, "blocked")

    @group.command("resume")
    @click.option("--maintainer-recovery", is_flag=True, help="Confirm maintainer recovery after a tamper finding.")
    @click.option("--authorise-additional-evidence", is_flag=True, help="Resume an INCONCLUSIVE workflow.")
    @click.option("--message", default="")
    @root_opt
    @harness_command("workflow resume")
    def resume(maintainer_recovery: bool, authorise_additional_evidence: bool, message: str,
               project_root: Path | None) -> CommandResult:
        doc = _service(project_root).resume(maintainer_recovery=maintainer_recovery,
                                            authorise_additional_evidence=authorise_additional_evidence,
                                            message=message)
        return _result("workflow resume", doc, f"resumed at {doc['workflow']['current_state']}")

    @group.command("cancel")
    @click.option("--message", required=True, help="Why the feature is abandoned.")
    @root_opt
    @harness_command("workflow cancel")
    def cancel(message: str, project_root: Path | None) -> CommandResult:
        doc = _service(project_root).cancel(message)
        return CommandResult("workflow cancel", True, f"{doc['workflow']['id']} abandoned (FAILED)", _summary(doc))
