"""Workflow lifecycle for one product repository (UC-02, UC-03, UC-04, UC-17, §14.1-§14.2).

Every public method first opens the workflow, which verifies the state file
against the audit log. On a tamper finding it restores the last-known-good
state, quarantines the edited file and moves the workflow to BLOCKED with
``STATE_TAMPER_DETECTED`` (§15 last row). If no verified copy exists it stops
without writing anything.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from et_harness import API_VERSION
from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.paths import glob_matches
from et_harness.common.reason_codes import ReasonCode
from et_harness.config.loader import load_configuration
from et_harness.contracts.approval import approval_status
from et_harness.contracts.context import project_context
from et_harness.contracts.discovery import find_active_feature
from et_harness.contracts.lint import lint_contract
from et_harness.contracts.lock import lock_contract, verify_lock
from et_harness.contracts.model import FeatureContract, load_contract
from et_harness.orchestration.engine import WorkflowEngine
from et_harness.orchestration.states import FINAL, State, owner
from et_harness.sessions.documents import new_orchestration_state
from et_harness.sessions.state_store import StateStore

# Local working data written by the kernel itself; never counts as a dirty tree.
LOCAL_DATA_GLOBS = (".agent-harness/state/**", ".agent-harness/handoffs/**", ".agent-harness/evidence/**",
                    ".agent-harness/audit/**", ".agent-harness/cache/**")

NOT_YET = (
    ("required_packages_resolved", "HK-009", "package resolution and lock verification"),
    ("baseline_passed", "HK-004", "baseline gate run and baseline test counts"),
    ("host_guards_and_enforcement_probes", "HK-010", "host detection, guard installation and tier probes"),
)

NEXT_ACTION = {
    State.CREATED: "Run `harness workflow start` to initialise and lock the contract.",
    State.INITIALISING: "Initialisation is running; re-run `harness workflow start` if it was interrupted.",
    State.PLANNING: "Planner produces the plan (Planner persona and plan review arrive in HK-007/HK-008).",
    State.PLAN_REVIEW: "Kernel validates the submitted plan (HK-008).",
    State.TEST_DESIGN: "QE writes acceptance tests in qe_test_paths (HK-008).",
    State.DEVELOPING: "Development implements within allowed_paths, then submits a handoff (HK-008).",
    State.DEVELOPMENT_REVIEW: "Kernel validates the Development handoff (HK-008).",
    State.VERIFYING: "QE (full) or the kernel (lite) runs the required gates (HK-004).",
    State.EVALUATING: "The deterministic evaluator issues the verdict (HK-005).",
    State.REWORK_PLANNING: "Planner writes a narrow rework contract (HK-012).",
    State.PASSED: "Closed. Push the branch; CI issues the authoritative verdict (HK-013).",
    State.FAILED: "Closed. Start a new workflow from a revised contract if needed.",
    State.BLOCKED: "Resolve the blocker, then run `harness workflow resume` (or `cancel` to abandon).",
    State.INCONCLUSIVE: "Authorise additional evidence with `harness workflow resume --authorise-additional-evidence`.",
}


@dataclass
class Check:
    id: str
    status: str  # passed | failed | not_implemented
    reason_code: ReasonCode | None = None
    message: str = ""
    planned_feature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status,
                "reason_code": self.reason_code.value if self.reason_code else None,
                "message": self.message, "planned_feature": self.planned_feature}


@dataclass
class OpenResult:
    document: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)


class WorkflowService:
    def __init__(self, root: Path, *, git=None) -> None:
        self.root = Path(root).resolve()
        self.harness = self.root / ".agent-harness"
        self.state_dir = self.harness / "state"
        self.state_path = self.state_dir / "orchestration-state.json"
        self.lock_path = self.state_dir / "scope-lock.json"
        self.audit = AuditLog(self.harness / "audit" / "audit.jsonl")
        self._git = git

    # --- plumbing -------------------------------------------------------------------
    @property
    def git(self):
        if self._git is None:
            from et_harness.repository.adapters.git_cli import GitCli

            self._git = GitCli(self.root)
        return self._git

    def _workflow_key(self) -> str | None:
        for path in (self.state_path.with_name("orchestration-state.lkg.json"), self.state_path):
            try:
                return json.loads(path.read_text(encoding="utf-8"))["workflow"]["id"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def _store(self, key: str) -> StateStore:
        return StateStore(self.state_path, self.audit, state_key=key)

    def _lock_store(self, key: str) -> StateStore:
        return StateStore(self.lock_path, self.audit, schema="scope-lock",
                          state_key=f"{key}/scope-lock", revision_path=("revision",))

    def _engine(self, key: str) -> WorkflowEngine:
        return WorkflowEngine(self._store(key), self.audit)

    def exists(self) -> bool:
        return self.state_path.exists() or self.state_path.with_name("orchestration-state.lkg.json").exists()

    def open(self) -> OpenResult:
        key = self._workflow_key()
        if key is None:
            if self.exists():
                raise HarnessError(ReasonCode.STATE_TAMPER_DETECTED, "state files are unreadable")
            raise HarnessError(ReasonCode.STATE_NOT_FOUND, "no workflow; run `harness workflow create`")
        store = self._store(key)
        try:
            doc = store.load()
        except HarnessError as exc:
            if exc.code is not ReasonCode.STATE_TAMPER_DETECTED:
                raise
            return self._handle_tamper(key, exc)
        result = OpenResult(doc)
        self._check_contract_unchanged(key, result)
        return result

    def _handle_tamper(self, key: str, original: HarnessError) -> OpenResult:
        store = self._store(key)
        try:
            doc, quarantined = store.recover()
        except HarnessError:
            raise original from None  # nothing verified: stop without writing
        finding = {"reason_code": ReasonCode.STATE_TAMPER_DETECTED.value,
                   "message": f"state file failed verification ({original.message}); "
                              "restored the last-known-good state",
                   "quarantined": str(quarantined) if quarantined else None}
        state = State(doc["workflow"]["current_state"])
        engine = self._engine(key)
        message = "Kernel state was edited outside the kernel; maintainer recovery required."
        if state is State.BLOCKED:
            doc = engine.replace_blocker(doc["workflow"]["revision"], [ReasonCode.STATE_TAMPER_DETECTED], message)
        elif state not in FINAL:
            doc = engine.transition(doc["workflow"]["revision"], State.BLOCKED, "BLOCKER_RAISED",
                                    reason_codes=[ReasonCode.STATE_TAMPER_DETECTED], message=message,
                                    tamper=True)
        return OpenResult(doc, [finding])

    def _check_contract_unchanged(self, key: str, result: OpenResult) -> None:
        doc = result.document
        state = State(doc["workflow"]["current_state"])
        if state in FINAL or state is State.BLOCKED or not self.lock_path.exists():
            return
        lock = self._lock_store(key).load()
        contract_path = self.root / doc["contract"]["path"]
        try:
            verify_lock(lock, load_contract(contract_path))
        except HarnessError as exc:
            if exc.code not in (ReasonCode.CONTRACT_MUTATED_AFTER_LOCK, ReasonCode.FEATURE_CONTRACT_INVALID):
                raise
            result.document = self._engine(key).transition(
                doc["workflow"]["revision"], State.BLOCKED, "BLOCKER_RAISED",
                reason_codes=[ReasonCode.CONTRACT_MUTATED_AFTER_LOCK], message=exc.message)
            result.findings.append(exc.to_dict())

    # --- create ---------------------------------------------------------------------
    def create(self) -> dict[str, Any]:
        contract = find_active_feature(self.root / "features")
        context, _ = project_context(self.root, contract)
        report = lint_contract(contract, context)
        if not report.ok:
            raise HarnessError(report.reason_codes()[0], f"{contract.id} fails lint",
                               {"findings": [f.to_dict() for f in report.findings]},
                               details=[f"{f.reason_code.value} {f.location}: {f.message}" for f in report.findings])
        status = approval_status(contract)
        if not status.valid:
            raise HarnessError(status.reason_code or ReasonCode.OWNER_APPROVAL_MISSING,
                               f"{contract.id}: {status.message}")
        if self.exists():
            current = self.open().document
            if State(current["workflow"]["current_state"]) not in FINAL:
                raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                                   f"workflow {current['workflow']['id']} is still open "
                                   f"({current['workflow']['current_state']}); only one may run")
            self._archive(current["workflow"]["id"])
        number = 1 + sum(1 for e in self.audit.entries()
                         if e["event_type"] == "workflow.created" and e["feature_id"] == contract.id)
        workflow_id = f"workflow-{contract.id}-{number:03d}"
        config = load_configuration(self.root).effective
        doc = new_orchestration_state(
            workflow_id=workflow_id, feature_id=contract.id,
            contract_path=Path(contract.source).resolve().relative_to(self.root).as_posix(),
            profile=contract.profile,
            maximum_cycles=min(contract.data["rework"]["maximum_cycles"], config.value("rework.maximum_cycles")),
        )
        doc["workflow"]["qe_test_design"] = contract.data["verification"].get("qe_test_design", "off")
        doc["ownership"]["persona"] = owner(State.CREATED, contract.profile)
        created = self._store(workflow_id).create(doc)
        family = (contract.data["metadata"].get("target_context") or {}).get("family")
        details = {"title": contract.data["metadata"]["title"], "profile": contract.profile,
                   "required_level": contract.required_level,
                   "maximum_rework_cycles": created["rework"]["maximum_cycles"]}
        if family and family != "none":
            details["target"] = family
        self.audit.append("workflow.created", workflow_id=workflow_id, feature_id=contract.id,
                          persona="kernel", state="CREATED", result="ok", details=details)
        return created

    def _archive(self, workflow_id: str) -> None:
        target = self.state_dir / "archive" / workflow_id
        target.mkdir(parents=True, exist_ok=True)
        for path in self.state_dir.glob("*.json"):
            shutil.move(str(path), str(target / path.name))

    # --- start: CREATED -> INITIALISING -> PLANNING | BLOCKED -----------------------
    def start(self) -> dict[str, Any]:
        doc = self.open().document
        state = State(doc["workflow"]["current_state"])
        key = doc["workflow"]["id"]
        engine = self._engine(key)
        if state is State.CREATED:
            doc = engine.transition(doc["workflow"]["revision"], State.INITIALISING, "START_REQUESTED")
        elif state is not State.INITIALISING:
            raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                               f"workflow is {state.value}; start applies to CREATED or INITIALISING")
        return self._initialise(doc)

    def _initialise(self, doc: dict[str, Any]) -> dict[str, Any]:
        key = doc["workflow"]["id"]
        checks: list[Check] = []
        contract: FeatureContract | None = None
        lock: dict[str, Any] | None = None
        baseline = None
        try:
            contract = load_contract(self.root / doc["contract"]["path"])
            checks.append(Check("contract_valid", "passed"))
        except HarnessError as exc:
            checks.append(Check("contract_valid", "failed", exc.code, exc.message))

        # repository_acceptable: clean baseline (kernel's local data excluded)
        try:
            from et_harness.repository.service import RepositoryService

            repo = RepositoryService(self.git)
            status = repo.status()
            dirty = [c for c in status.changes if not any(glob_matches(g, c.path) for g in LOCAL_DATA_GLOBS)]
            if status.head_commit is None:
                checks.append(Check("repository_acceptable", "failed", ReasonCode.BASELINE_FAILED,
                                    "repository has no commits"))
            elif dirty:
                checks.append(Check("repository_acceptable", "failed", ReasonCode.BASELINE_DIRTY,
                                    f"{len(dirty)} uncommitted change(s), e.g. {dirty[0].path}; "
                                    "commit the contract and work tree first"))
            else:
                baseline = status
                checks.append(Check("repository_acceptable", "passed",
                                    message=f"baseline {status.head_commit[:12]} on {status.branch or 'detached HEAD'}"))
        except HarnessError as exc:
            checks.append(Check("repository_acceptable", "failed", exc.code, exc.message))

        # effective_configuration_valid
        config = None
        try:
            config = load_configuration(self.root).effective
            if config.conflicts:
                checks.append(Check("effective_configuration_valid", "failed", ReasonCode.CONFIG_INVALID,
                                    f"{len(config.conflicts)} setting(s) try to weaken a higher layer"))
            else:
                checks.append(Check("effective_configuration_valid", "passed"))
        except HarnessError as exc:
            checks.append(Check("effective_configuration_valid", "failed", exc.code, exc.message))

        if contract is not None:
            status_ = approval_status(contract)
            checks.append(Check("owner_approval_valid", "passed" if status_.valid else "failed",
                                status_.reason_code, status_.message))
            context, project = project_context(self.root, contract)
            report = lint_contract(contract, context)
            if report.ok:
                checks.append(Check("profile_eligible", "passed",
                                    message=f"{contract.profile} profile; lint clean"))
            else:
                checks.append(Check("profile_eligible", "failed", report.reason_codes()[0],
                                    "; ".join(f"{f.location}: {f.message}" for f in report.findings[:3])))

        # enforcement_meets_policy: no probes yet (HK-010), so ENFORCED cannot be shown.
        if config is not None and config.value("enforcement.minimum_write_tier") == "ENFORCED":
            checks.append(Check("enforcement_meets_policy", "failed", ReasonCode.ENFORCEMENT_TIER_INSUFFICIENT,
                                "policy requires ENFORCED writes; no host guard is installed or probed yet"))
        for check_id, planned, what in NOT_YET:
            checks.append(Check(check_id, "not_implemented", ReasonCode.NOT_IMPLEMENTED,
                                f"{what} is not built yet; not claimed", planned))

        failed = [c for c in checks if c.status == "failed"]
        if not failed and contract is not None and baseline is not None:
            try:
                lock = lock_contract(contract, baseline_commit=baseline.head_commit, context=context,
                                     project_classes=(project or {}).get("artefact_classes"))
                checks.append(Check("contract_and_scope_locked", "passed",
                                    message=f"contract hash {lock['contract_hash']}"))
            except HarnessError as exc:
                failed.append(Check("contract_and_scope_locked", "failed", exc.code, exc.message))
                checks.append(failed[-1])

        engine = self._engine(key)
        revision = doc["workflow"]["revision"]
        record = [c.to_dict() for c in checks]
        if failed:
            codes = list(dict.fromkeys(c.reason_code for c in failed if c.reason_code))
            return engine.transition(
                revision, State.BLOCKED, "BLOCKER_RAISED", reason_codes=codes or [ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE],
                message="; ".join(f"{c.id}: {c.message}" for c in failed), resume_state=State.INITIALISING,
                extra=lambda d: d.update(initialisation={"checks": record, "baseline_fingerprint": None,
                                                          "scope_lock_hash": None}))

        lock_record = {"revision": 0, "workflow_id": key, **{k: v for k, v in lock.items()},
                       "integrity": {"previous_state_hash": None, "audit_sequence": 1}}
        lock_record["api_version"] = API_VERSION
        lock_record["contract_path"] = doc["contract"]["path"]
        lock_store = self._lock_store(key)
        if lock_store.exists():
            existing = lock_store.load()
            lock_store.update(existing["revision"], lambda d: {**lock_record, "revision": existing["revision"],
                                                                "integrity": existing["integrity"]})
        else:
            lock_store.create(lock_record)

        def extra(d: dict[str, Any]) -> None:
            d["contract"]["hash"] = lock["contract_hash"]
            d["repository"]["baseline_commit"] = baseline.head_commit
            d["initialisation"] = {"checks": record, "baseline_fingerprint": None,
                                   "scope_lock_hash": lock["contract_hash"]}

        return engine.transition(revision, State.PLANNING, "INITIALISATION_PASSED", extra=extra)

    # --- status, history, block, resume, cancel --------------------------------------
    def status(self) -> dict[str, Any]:
        opened = self.open()
        doc = opened.document
        wf = doc["workflow"]
        state = State(wf["current_state"])
        checks = (doc.get("initialisation") or {}).get("checks") or []
        return {
            "workflow_id": wf["id"], "feature_id": wf["feature_id"], "profile": wf["profile"],
            "state": state.value, "revision": wf["revision"], "owner": owner(state, wf["profile"]),
            "previous_state": wf["previous_state"], "resumable_state": wf["resumable_state"],
            "contract": {"path": doc["contract"]["path"], "hash": doc["contract"]["hash"],
                         "locked": bool(doc["contract"]["hash"])},
            "baseline_commit": doc["repository"]["baseline_commit"],
            "blocker": doc["blocker"],
            "open_checks": [c for c in checks if c["status"] != "passed"],
            "rework": doc["rework"],
            "next_action": NEXT_ACTION[state],
            "verdict_authority": "advisory (the authoritative verdict comes from CI)",
            "findings": opened.findings,
        }

    def history(self) -> dict[str, Any]:
        opened = self.open()
        return {"workflow_id": opened.document["workflow"]["id"], "history": opened.document["history"],
                "findings": opened.findings}

    def block(self, reason_codes: list[str], message: str, resume_state: str | None = None) -> dict[str, Any]:
        doc = self.open().document
        engine = self._engine(doc["workflow"]["id"])
        if doc["workflow"]["current_state"] == State.BLOCKED.value:
            return engine.replace_blocker(doc["workflow"]["revision"], reason_codes, message)
        return engine.transition(doc["workflow"]["revision"], State.BLOCKED, "BLOCKER_RAISED",
                                 reason_codes=reason_codes, message=message,
                                 resume_state=State(resume_state) if resume_state else None)

    def resume(self, *, maintainer_recovery: bool = False, authorise_additional_evidence: bool = False,
               message: str = "") -> dict[str, Any]:
        opened = self.open()
        doc = opened.document
        wf = doc["workflow"]
        state = State(wf["current_state"])
        engine = self._engine(wf["id"])
        if state is State.BLOCKED:
            codes = set((doc["blocker"] or {}).get("reason_codes") or [])
            if opened.findings:
                raise HarnessError(ReasonCode.STATE_TAMPER_DETECTED,
                                   "tamper was detected by this command; review before resuming")
            if ReasonCode.STATE_TAMPER_DETECTED.value in codes and not maintainer_recovery:
                raise HarnessError(ReasonCode.STATE_TAMPER_DETECTED,
                                   "resuming after a tamper finding requires --maintainer-recovery")
            if ReasonCode.CONTRACT_MUTATED_AFTER_LOCK.value in codes and self.lock_path.exists():
                verify_lock(self._lock_store(wf["id"]).load(), load_contract(self.root / doc["contract"]["path"]))
            target = State(wf["resumable_state"])
            doc = engine.transition(wf["revision"], target, "BLOCKER_RESOLVED",
                                    message=message or ("maintainer recovery" if maintainer_recovery else ""))
            if target is State.INITIALISING:
                return self._initialise(doc)
            return doc
        if state is State.INCONCLUSIVE:
            if not authorise_additional_evidence:
                raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                                   "INCONCLUSIVE resumes only with --authorise-additional-evidence")
            return engine.transition(wf["revision"], State(wf["resumable_state"]),
                                     "ADDITIONAL_EVIDENCE_AUTHORISED", message=message)
        raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED, f"workflow is {state.value}; nothing to resume")

    def cancel(self, message: str) -> dict[str, Any]:
        doc = self.open().document
        wf = doc["workflow"]
        if wf["current_state"] != State.BLOCKED.value:
            raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                               "only a BLOCKED workflow can be abandoned (§15); block it first with a reason code")
        return self._engine(wf["id"]).transition(wf["revision"], State.FAILED, "FEATURE_ABANDONED",
                                                 message=message)
