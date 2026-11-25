"""Gather evaluator inputs from disk, record verdicts and transition (§14.9, §22).

The evaluator is read-only towards product files and evidence. It writes:

* ``evaluations/evaluation-NNN.yaml`` (write-once) for every evaluation;
* ``verdict.yaml`` (write-once) when the workflow reaches PASSED or FAILED;
* the state transition, through the kernel's workflow engine.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from et_harness import API_VERSION
from et_harness.artefacts.classes import ArtefactClassifier
from et_harness.artefacts.scope import ScopeMode, Writer, validate_changes
from et_harness.common.errors import HarnessError
from et_harness.common.hashing import digest
from et_harness.common.paths import glob_matches
from et_harness.common.reason_codes import ReasonCode, is_emittable
from et_harness.common.schemas import validate_document
from et_harness.common.yaml_io import dump_yaml, load_yaml_file, load_yaml_text
from et_harness.contracts.approval import approval_status
from et_harness.contracts.model import load_contract, parse_contract
from et_harness.evaluator.rules import EvaluationInputs, EvaluationResult, GateEvidence, evaluate
from et_harness.gates.runner import GateRunner

RULES_VERSION = "1.0.0"
NEXT = {"PASSED": ("PASSED", "COMPLETION_RULES_SATISFIED"), "BLOCKED": ("BLOCKED", "BLOCKER_RAISED"),
        "INCONCLUSIVE": ("INCONCLUSIVE", "EVIDENCE_INSUFFICIENT")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emittable(codes: list[str]) -> list[str]:
    out = []
    for code in codes:
        try:
            rc = ReasonCode(code)
        except ValueError:
            continue
        if is_emittable(rc) and rc.value not in out:
            out.append(rc.value)
    return out


def build_inputs(service) -> tuple[EvaluationInputs, dict[str, Any], Path]:
    from et_harness.evidence.bundle import validate
    from et_harness.orchestration.service import LOCAL_DATA_GLOBS
    from et_harness.repository.service import RepositoryService

    doc = service.open().document
    wf = doc["workflow"]
    bundle = GateRunner.bundle_dir(service.root, doc)
    lock = service._lock_store(wf["id"]).load() if service.lock_path.exists() else None
    contract_data = (lock or {}).get("contract")
    current_hash = None
    owner_ok, owner_msg = True, ""
    try:
        current = load_contract(service.root / doc["contract"]["path"])
        current_hash = current.digest()
        contract_data = contract_data or current.data
        status = approval_status(current)
        owner_ok, owner_msg = status.valid, status.message
    except HarnessError as exc:
        owner_ok, owner_msg = False, exc.message
    contract_data = contract_data or {}
    cycle = doc["rework"]["current_cycle"]
    baseline_counts = (doc.get("initialisation") or {}).get("baseline_test_counts") or {}
    gates = []
    for gate_id in (contract_data.get("verification") or {}).get("required_gates") or []:
        attempts = [a for a in GateRunner.attempts(bundle, gate_id)
                    if a["purpose"] == "verification" and a["rework_cycle"] == cycle]
        if attempts:
            a = attempts[-1]
            gates.append(GateEvidence(gate_id, a["level"], a["result"], a["execution_profile"], a["attempt"],
                                      a["test_counts"], baseline_counts.get(gate_id), tuple(a["reason_codes"])))
    manifest = {"present": False, "integrity_ok": False, "complete": False, "missing": [], "not_implemented": [],
                "added": []}
    if (bundle / "manifest.yaml").is_file():
        report = validate(bundle)
        manifest = {"present": True, "integrity_ok": report["integrity_ok"], "complete": report["complete"],
                    "missing": report["missing_items"], "not_implemented": report["not_implemented_items"],
                    "added": [a for a in report["added"] if not a.startswith("evaluations/") and a != "verdict.yaml"]}
    scope_findings, models = [], []
    base = doc["repository"]["baseline_commit"]
    if base and lock:
        changes = RepositoryService(service.git).changes_since(base)
        changes = [c for c in changes if not any(glob_matches(g, c.path) for g in LOCAL_DATA_GLOBS)]
        classifier = ArtefactClassifier(**{k: tuple(v) for k, v in lock["artefact_classes"].items()})
        mode = ScopeMode.CANDIDATE if doc["repository"]["candidate_commit"] else ScopeMode.WORK
        report = validate_changes(changes, allowed_paths=lock["scope"]["allowed_paths"], classifier=classifier,
                                  writer=Writer.DEVELOPMENT, mode=mode)
        scope_findings = [{"reason_code": f.reason_code.value, "path": f.path} for f in report.findings]
        models = report.changed_models
    open_checks = [c["id"] for c in (doc.get("initialisation") or {}).get("checks") or []
                   if c["status"] == "not_implemented"]
    inputs = EvaluationInputs(
        feature_id=wf["feature_id"], workflow_id=wf["id"], profile=wf["profile"], contract=contract_data,
        contract_hash_locked=(lock or {}).get("contract_hash") or doc["contract"]["hash"],
        contract_hash_current=current_hash, workflow_exists=True, state_integrity_ok=True,
        rework_cycle=cycle, gates=gates, manifest_present=manifest["present"],
        manifest_integrity_ok=manifest["integrity_ok"], manifest_complete=manifest["complete"],
        manifest_missing=manifest["missing"], manifest_not_implemented=manifest["not_implemented"],
        manifest_added=manifest["added"], scope_findings=scope_findings, changed_models=models,
        approvals=[], owner_approval_valid=owner_ok, owner_approval_message=owner_msg,
        blocker=doc["blocker"], open_checks=open_checks, authority_verified=False,
    )
    return inputs, doc, bundle


def verdict_document(inputs: EvaluationInputs, result: EvaluationResult, *, number: int, evaluated_at: str,
                     next_state: str, rework_allowed: bool) -> dict[str, Any]:
    scope_codes = [f for f in inputs.scope_findings]
    return {
        "api_version": API_VERSION, "kind": "FeatureVerdict",
        "feature_id": inputs.feature_id, "workflow_id": inputs.workflow_id, "profile": inputs.profile,
        "authority": result.authority, "execution_profile": result.execution_profile, "verdict": result.verdict,
        "evaluation": {"number": number, "evaluated_at": evaluated_at, "rework_cycle": inputs.rework_cycle,
                       "rework_allowed": rework_allowed, "next_state": next_state,
                       "inputs_digest": digest(asdict(inputs)), "rules_version": RULES_VERSION},
        "acceptance_criteria": result.criteria,
        "verification": {"required_level": result.required_level,
                         "highest_level_attempted": result.highest_level_attempted,
                         "highest_level_passed": result.highest_level_passed,
                         "mandatory_gates_passed": result.mandatory_gates_passed,
                         "failed_gates": result.failed_gates},
        "scope": {"contract_hash_matches": (inputs.contract_hash_locked is not None
                                            and inputs.contract_hash_current == inputs.contract_hash_locked),
                  "unauthorised_changes": [f["path"] for f in scope_codes if f["reason_code"] != "PROTECTED_PATH_CHANGED"],
                  "protected_path_changes": [f["path"] for f in scope_codes if f["reason_code"] == "PROTECTED_PATH_CHANGED"]},
        "package_integrity": {"valid": None, "invalid_packages": []},
        "evidence": {"manifest_complete": inputs.manifest_complete, "missing_artefacts": inputs.manifest_missing,
                     "not_implemented": inputs.manifest_not_implemented},
        "enforcement": {"lowest_tier_for_writes": None},
        "reason_codes": result.reason_codes,
        "findings": [f.to_dict() for f in result.findings],
        "human_review": {"required": result.human_review_required, "reason": result.human_review_reason},
    }


def evaluate_workflow(service, *, preview: bool = False) -> dict[str, Any]:
    from et_harness.orchestration.states import State

    inputs, doc, bundle = build_inputs(service)
    wf = doc["workflow"]
    if not preview and wf["current_state"] != State.EVALUATING.value:
        raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                           f"evaluate runs in EVALUATING, not {wf['current_state']}; use --preview to inspect")
    result = evaluate(inputs)
    rework_allowed = result.verdict == "FAILED" and doc["rework"]["current_cycle"] < doc["rework"]["maximum_cycles"]
    if result.verdict == "FAILED":
        next_state, reason = ("REWORK_PLANNING", "REWORK_ALLOWED") if rework_allowed else ("FAILED", "FAILURE_ESTABLISHED")
    else:
        next_state, reason = NEXT[result.verdict]
    evaluations = bundle / "evaluations"
    number = 1 + (len(list(evaluations.glob("evaluation-*.yaml"))) if evaluations.is_dir() else 0)
    verdict = verdict_document(inputs, result, number=number, evaluated_at=_now(),
                               next_state="preview" if preview else next_state, rework_allowed=rework_allowed)
    validate_document(verdict, "verdict", source="verdict", code=ReasonCode.INTERNAL_ERROR)
    if preview:
        return {"verdict": verdict, "written": [], "transition": None}

    evaluations.mkdir(parents=True, exist_ok=True)
    written = []
    record = evaluations / f"evaluation-{number:03d}.yaml"
    GateRunner._write_once(record, dump_yaml(verdict))
    written.append(record.relative_to(bundle).as_posix())
    if next_state in ("PASSED", "FAILED"):
        final = bundle / "verdict.yaml"
        GateRunner._write_once(final, dump_yaml(verdict))
        written.append("verdict.yaml")
    codes = _emittable(result.reason_codes)
    common = dict(workflow_id=wf["id"], feature_id=wf["feature_id"], persona="evaluator",
                  state="EVALUATING", result=result.verdict, evidence=written)
    if next_state == "REWORK_PLANNING":
        service.audit.append("evaluation.result", reason_codes=codes, details={
            "failed_gates": result.failed_gates, "rework_allowed": True,
            "summary": f"{result.verdict}: rework cycle {doc['rework']['current_cycle'] + 1} of "
                       f"{doc['rework']['maximum_cycles']} allowed"}, **common)
    else:
        service.audit.append("verdict.issued", reason_codes=codes, details={
            "authority": result.authority, "execution_profile": result.execution_profile,
            "required_level": result.required_level, "highest_level_passed": result.highest_level_passed,
            "summary": "; ".join(f.message for f in result.findings[:3]) or "all completion rules satisfied"},
            **common)
    engine = service._engine(wf["id"])
    kwargs: dict[str, Any] = {}
    if next_state == "BLOCKED":
        kwargs = {"reason_codes": codes or [ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE.value],
                  "message": "; ".join(f.message for f in result.findings if f.category == "blocked")[:500],
                  "resume_state": State.EVALUATING}
    new = engine.transition(wf["revision"], State(next_state), reason, **kwargs)
    return {"verdict": verdict, "written": written,
            "transition": f"EVALUATING -> {new['workflow']['current_state']} ({reason})"}


# --- bootstrap records (§30 bootstrapping rule) -----------------------------------------------

def _contract_at(repo_root: Path, commit: str, path: str):
    text = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=repo_root, check=True, capture_output=True,
                          text=True).stdout
    return parse_contract(load_yaml_text(text, source=f"{commit}:{path}"), source=f"{commit}:{path}")


def evaluate_bootstrap_records(records_path: Path, repo_root: Path) -> list[dict[str, Any]]:
    """Re-run the evaluator over transcribed bootstrap records at L0/L1, without upgrading them."""
    data = load_yaml_file(records_path)
    out = []
    for rec in data["records"]:
        current = load_contract(repo_root / rec["contract"])
        locked = _contract_at(repo_root, rec["lock_commit"], rec["contract_at_lock"])
        status = approval_status(current)
        gates = [GateEvidence(g["gate_id"], g["level"], g["result"], "developer", 1,
                              {"run": g["tests"], "passed": g["tests"] - g.get("failed", 0),
                               "failed": g.get("failed", 0), "skipped": 0}, None, ())
                 for g in rec["gates"]]
        inputs = EvaluationInputs(
            feature_id=rec["feature"], workflow_id=f"bootstrap-{rec['feature']}", profile=current.profile,
            contract=current.data, contract_hash_locked=locked.digest(), contract_hash_current=current.digest(),
            workflow_exists=False, gates=gates, manifest_present=False,
            owner_approval_valid=status.valid, owner_approval_message=status.message,
        )
        result = evaluate(inputs)
        out.append({"feature": rec["feature"], "verdict": result.verdict, "authority": result.authority,
                    "highest_level_passed": result.highest_level_passed,
                    "reason_codes": result.reason_codes,
                    "findings": [f.to_dict() for f in result.findings],
                    "contract_hash": current.digest(), "contract_unchanged_since_lock": locked.digest() == current.digest(),
                    "evidence_note": rec["evidence_note"]})
    return out


def render_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Re-evaluation of the bootstrap records",
        "",
        "Spec v1.1.1 §30 (bootstrapping rule): once HK-005 passes its tests, re-run the",
        "evaluator over the HK-001 to HK-004 records at L0 and L1 so every feature is",
        "judged by the same rules. HK-015 was also built by the bootstrap method, so it",
        "is included. Generated by `harness evaluate --bootstrap-records",
        "docs/bootstrap-evidence/bootstrap-records.yaml --report",
        "docs/bootstrap-evidence/re-evaluation.md`. The inputs are the gate results",
        "transcribed from each evidence note and the contracts as committed; no record",
        "is upgraded.",
        "",
        "| Feature | Verdict | Authority | Highest level passed | Contract unchanged since lock | Reason codes |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['feature']} | {r['verdict']} | {r['authority']} | {r['highest_level_passed']} | "
                     f"{'yes' if r['contract_unchanged_since_lock'] else 'NO'} | {', '.join(r['reason_codes'])} |")
    lines += ["", "## Findings per record", ""]
    for r in results:
        lines.append(f"### {r['feature']} ({r['verdict']})")
        lines.append("")
        for f in r["findings"]:
            code = f" `{f['reason_code']}`" if f["reason_code"] else ""
            lines.append(f"- {f['category']}{code}: {f['message']}")
        lines.append(f"- Evidence note: `{r['evidence_note']}`")
        lines.append("")
    lines += [
        "## Reading these verdicts",
        "",
        "None of the bootstrap records can be PASSED under the kernel's rules, and",
        "that is the expected, honest outcome:",
        "",
        "- **No kernel evidence.** Their gates ran as pytest outside the kernel, so",
        "  there is no workflow, integrity chain, immutable gate attempt or evidence",
        "  manifest (`EXECUTION_NOT_STARTED`, `EVIDENCE_MANIFEST_INCOMPLETE`).",
        "- **Unbound approvals.** HK-001, HK-002 and HK-015 were approved in chat",
        "  before content-bound approvals existed, so the kernel cannot verify their",
        "  approvals (`APPROVAL_INVALID`, which outranks insufficient evidence and",
        "  gives BLOCKED).",
        "",
        "Their advisory PASSED in the evidence notes rests on the bootstrapping rule",
        "and human review, not on the evaluator.",
        "",
    ]
    return "\n".join(lines)
