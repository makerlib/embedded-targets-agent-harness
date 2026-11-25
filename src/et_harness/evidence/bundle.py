"""Evidence bundle collection and validation (§9.17, §21).

``collect`` snapshots the current inputs (contract, scope lock, effective
configuration, environment, approvals, audit log, repository diff) next to
the immutable gate attempts, then writes ``manifest.yaml`` with a SHA-256 for
every file and a manifest hash. Items owned by later features are listed as
``not_implemented`` and keep ``complete`` false: the bundle never claims to be
complete while spec-required evidence does not exist.

``validate`` re-hashes every file and reports modified, missing and added
files and a manifest hash mismatch.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from et_harness import API_VERSION, __version__
from et_harness.common.atomic import atomic_write_text
from et_harness.common.errors import HarnessError
from et_harness.common.hashing import digest
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document, validation_errors
from et_harness.common.yaml_io import dump_yaml, load_yaml_file
from et_harness.config.loader import load_configuration
from et_harness.evidence.environment import describe_environment
from et_harness.evidence.git_diff import working_tree_patch
from et_harness.gates.runner import GateRunner

LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
MANIFEST = "manifest.yaml"

# (item id, path, planned feature) for spec §21 items the kernel cannot produce yet.
LATER_ITEMS = (
    ("packages-lock", "packages.lock.yaml", "HK-009"),
    ("plan", "plan.yaml", "HK-008"),
    ("enforcement-report", "enforcement.yaml", "HK-010"),
    ("handoffs", "handoffs/", "HK-008"),
    ("model-diffs", "model-diffs/", "HK-008"),
    ("verdict", "verdict.yaml", "HK-005"),
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _files(bundle: Path) -> list[Path]:
    return sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != MANIFEST)


def _level_summary(latest: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    attempted = [a["level"] for a in latest.values()]
    highest_attempted = max(attempted, key=LEVELS.index) if attempted else None
    highest_passed = None
    for level in LEVELS:
        at_level = [a for a in latest.values() if a["level"] == level]
        if not at_level:
            continue
        if all(a["result"] == "PASSED" for a in at_level):
            highest_passed = level
        else:
            break
    return highest_attempted, highest_passed


def collect(service) -> dict[str, Any]:
    """Collect the bundle for the open workflow of a ``WorkflowService``."""
    opened = service.open()
    doc = opened.document
    wf = doc["workflow"]
    root: Path = service.root
    bundle = GateRunner.bundle_dir(root, doc)
    bundle.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []

    def put(item_id: str, rel: str, text: str) -> None:
        atomic_write_text(bundle / rel, text)
        items.append({"id": item_id, "path": rel, "status": "present", "planned_feature": None})

    env = describe_environment()
    put("environment", "environment.yaml", dump_yaml(env))
    put("effective-config", "effective-config.yaml", dump_yaml(load_configuration(root).effective.to_dict()))

    contract_path = root / doc["contract"]["path"]
    lock = None
    if service.lock_path.exists():
        lock = service._lock_store(wf["id"]).load()
        put("scope-lock", "scope-lock.yaml", dump_yaml(lock))
    else:
        items.append({"id": "scope-lock", "path": "scope-lock.yaml", "status": "missing",
                      "message": "contract not locked yet"})
    if contract_path.is_file():
        put("feature-contract", "feature-contract.yaml", contract_path.read_text(encoding="utf-8"))
    else:
        items.append({"id": "feature-contract", "path": "feature-contract.yaml", "status": "missing"})

    contract = (lock or {}).get("contract")
    if contract is None and contract_path.is_file():
        contract = load_yaml_file(contract_path)
    intake = (contract or {}).get("intake") or {}
    approval_events = [e for e in service.audit.entries() if e["event_type"] == "approval.recorded"
                       and e["feature_id"] == wf["feature_id"]]
    approvals = [{"kind": "feature-owner", "approved_by": intake.get("approved_by"),
                  "source": intake.get("approval_ref"), "approved_at": intake.get("approved_at"),
                  "content_hash": intake.get("approved_content_hash")}] + [
        {"kind": "audit-event", "approval_id": e["approval_id"], "result": e["result"],
         "source": e["details"].get("source")} for e in approval_events]
    put("approvals", "approvals.yaml", dump_yaml(approvals))

    service.audit.entries()  # verifies the chain before it is copied
    shutil.copyfile(service.audit.path, bundle / "audit.jsonl")
    items.append({"id": "audit-log", "path": "audit.jsonl", "status": "present", "planned_feature": None})

    base = doc["repository"]["baseline_commit"]
    if base:
        put("repository-diff", "repository-diff.patch", working_tree_patch(root, base))
    else:
        items.append({"id": "repository-diff", "path": "repository-diff.patch", "status": "missing",
                      "message": "no baseline commit yet"})

    # gates: latest counted attempt per required gate
    required = list(((contract or {}).get("verification") or {}).get("required_gates") or [])
    latest: dict[str, dict[str, Any]] = {}
    gates_out, failures = [], []
    test_results = bundle / "test-results"
    for gate_id in required:
        attempts = GateRunner.attempts(bundle, gate_id)
        for a in attempts:
            if a["result"] != "PASSED":
                failures.append({"gate_id": gate_id, "attempt": a["attempt"], "purpose": a["purpose"],
                                 "result": a["result"], "failure_signature": a["failure_signature"],
                                 "reason_codes": a["reason_codes"]})
            for art in a["artefacts"]:
                if art["evidence_id"] == "junit-results":
                    test_results.mkdir(exist_ok=True)
                    target = test_results / f"{gate_id}-attempt-{a['attempt']:03d}.xml"
                    if not target.exists():
                        shutil.copyfile(bundle / art["path"], target)
        verification = [a for a in attempts if a["purpose"] == "verification"
                        and a["rework_cycle"] == doc["rework"]["current_cycle"]]
        if verification:
            latest[gate_id] = verification[-1]
        gates_out.append({
            "gate_id": gate_id, "attempts": len(attempts),
            "latest_verification": ({k: verification[-1][k] for k in
                                     ("attempt", "result", "level", "execution_profile", "test_counts")}
                                    if verification else None),
        })
        items.append({"id": f"gate:{gate_id}", "path": f"gates/{gate_id}/",
                      "status": "present" if verification else "missing", "planned_feature": None,
                      "message": "" if verification else "no verification attempt in the current rework cycle"})
    for item_id, rel, planned in LATER_ITEMS:
        items.append({"id": item_id, "path": rel, "status": "not_implemented", "planned_feature": planned})
    if wf["profile"] == "full":
        items.append({"id": "qe-result", "path": "qe-result.yaml", "status": "not_implemented",
                      "planned_feature": "HK-012"})
    else:
        items.append({"id": "qe-result", "path": "qe-result.yaml", "status": "not_applicable",
                      "planned_feature": None, "message": "lite profile: the kernel runs the gates"})

    criteria = []
    for c in (contract or {}).get("acceptance_criteria") or []:
        criteria.append({"id": c["id"], "verification_method": c.get("verification_method"),
                         "gates": c.get("gates") or [],
                         "gate_results": {g: latest[g]["result"] if g in latest else None for g in c.get("gates") or []},
                         "status": "pending_evaluation"})
    highest_attempted, highest_passed = _level_summary(latest)
    profiles = {a["execution_profile"] for a in latest.values()}
    missing = [i["id"] for i in items if i["status"] == "missing"]
    not_impl = [i["id"] for i in items if i["status"] == "not_implemented"]
    files = [{"path": p.relative_to(bundle).as_posix(), "sha256": _sha(p), "bytes": p.stat().st_size}
             for p in _files(bundle)]
    manifest = {
        "api_version": API_VERSION, "kind": "EvidenceManifest",
        "evidence_bundle": {
            "feature_id": wf["feature_id"], "workflow_id": wf["id"], "profile": wf["profile"],
            "authority": "advisory",
            "execution_profile": profiles.pop() if len(profiles) == 1 else "developer",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "baseline_commit": base, "final_commit": doc["repository"]["candidate_commit"],
            "workflow_state": wf["current_state"],
        },
        "provenance": {"harness_version": __version__, "feature_contract_hash": doc["contract"]["hash"],
                       "package_lock_hash": None, "agent_instances": [], "independence": None,
                       "environment_fingerprint": env["fingerprint"], "matlab": None},
        "enforcement": {"report": None, "lowest_tier_for_writes": None},
        "verification": {"required_level": (contract or {}).get("verification", {}).get("required_level", "L0"),
                         "highest_level_attempted": highest_attempted, "highest_level_passed": highest_passed,
                         "mandatory_gates": required},
        "acceptance_criteria": criteria, "gates": gates_out, "artefacts": files, "failures": failures,
        "approvals": approvals, "items": items,
        "integrity": {"complete": not missing and not not_impl, "missing": missing,
                      "not_implemented": not_impl, "manifest_hash": None},
    }
    manifest["integrity"]["manifest_hash"] = digest(manifest)
    validate_document(manifest, "evidence-manifest", source="manifest", code=ReasonCode.INTERNAL_ERROR)
    atomic_write_text(bundle / MANIFEST, dump_yaml(manifest))
    return {"bundle": str(bundle), "manifest": manifest}


def validate(bundle: Path) -> dict[str, Any]:
    path = Path(bundle) / MANIFEST
    if not path.is_file():
        raise HarnessError(ReasonCode.EVIDENCE_MANIFEST_INCOMPLETE, f"no manifest at {path}")
    manifest = load_yaml_file(path)
    schema_errors = validation_errors(manifest, "evidence-manifest")
    recorded = (manifest.get("integrity") or {}).get("manifest_hash")
    check = dict(manifest)
    check["integrity"] = dict(manifest.get("integrity") or {}, manifest_hash=None)
    hash_ok = recorded == digest(check)
    listed = {e["path"]: e for e in manifest.get("artefacts") or []}
    modified, missing = [], []
    for rel, entry in listed.items():
        file = Path(bundle) / rel
        if not file.is_file():
            missing.append(rel)
        elif _sha(file) != entry["sha256"]:
            modified.append(rel)
    on_disk = {p.relative_to(bundle).as_posix() for p in _files(Path(bundle))}
    added = sorted(on_disk - set(listed))
    writable_attempts = sorted(
        p.relative_to(bundle).as_posix() for p in Path(bundle).glob("gates/*/attempt-*.yaml")
        if p.stat().st_mode & 0o222)
    integrity_ok = hash_ok and not modified and not missing and not schema_errors
    return {
        "bundle": str(bundle), "ok": integrity_ok and not added,
        "integrity_ok": integrity_ok, "manifest_hash_ok": hash_ok, "schema_errors": schema_errors,
        "modified": sorted(modified), "missing": sorted(missing), "added": added,
        "writable_attempt_files": writable_attempts,
        "complete": bool((manifest.get("integrity") or {}).get("complete")),
        "missing_items": (manifest.get("integrity") or {}).get("missing", []),
        "not_implemented_items": (manifest.get("integrity") or {}).get("not_implemented", []),
    }
