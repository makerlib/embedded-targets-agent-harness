"""HK-003 AC-001..AC-004, AC-008: transition table, ownership, engine (§13, §15, §16)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from et_harness.audit.log import AuditLog
from et_harness.audit.vocabulary import TRANSITION_REASONS
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validation_errors
from et_harness.orchestration.engine import WorkflowEngine
from et_harness.orchestration.states import EDGES, FINAL, OWNERS_FULL, OWNERS_LITE, State, check_transition, owner
from et_harness.sessions.documents import new_orchestration_state
from et_harness.sessions.state_store import StateStore

S = State

# §15, transcribed independently: (from, to, reason).
SPEC_15 = {
    ("CREATED", "INITIALISING", "START_REQUESTED"), ("CREATED", "BLOCKED", "BLOCKER_RAISED"),
    ("INITIALISING", "PLANNING", "INITIALISATION_PASSED"), ("INITIALISING", "BLOCKED", "BLOCKER_RAISED"),
    ("INITIALISING", "FAILED", "BASELINE_FAILURE_ESTABLISHED"), ("PLANNING", "PLAN_REVIEW", "PLAN_SUBMITTED"),
    ("PLANNING", "BLOCKED", "BLOCKER_RAISED"), ("PLAN_REVIEW", "TEST_DESIGN", "PLAN_ACCEPTED"),
    ("PLAN_REVIEW", "DEVELOPING", "PLAN_ACCEPTED"), ("PLAN_REVIEW", "PLANNING", "PLAN_REJECTED"),
    ("PLAN_REVIEW", "BLOCKED", "BLOCKER_RAISED"), ("TEST_DESIGN", "DEVELOPING", "TESTS_ACCEPTED"),
    ("TEST_DESIGN", "BLOCKED", "BLOCKER_RAISED"), ("DEVELOPING", "DEVELOPMENT_REVIEW", "HANDOFF_SUBMITTED"),
    ("DEVELOPING", "BLOCKED", "BLOCKER_RAISED"), ("DEVELOPING", "FAILED", "NON_RECOVERABLE_FAILURE"),
    ("DEVELOPMENT_REVIEW", "VERIFYING", "HANDOFF_ACCEPTED"), ("DEVELOPMENT_REVIEW", "DEVELOPING", "HANDOFF_REJECTED"),
    ("DEVELOPMENT_REVIEW", "BLOCKED", "BLOCKER_RAISED"), ("VERIFYING", "EVALUATING", "QE_RESULT_SUBMITTED"),
    ("VERIFYING", "EVALUATING", "GATE_RUN_RECORDED"), ("VERIFYING", "BLOCKED", "BLOCKER_RAISED"),
    ("EVALUATING", "PASSED", "COMPLETION_RULES_SATISFIED"), ("EVALUATING", "REWORK_PLANNING", "REWORK_ALLOWED"),
    ("EVALUATING", "FAILED", "FAILURE_ESTABLISHED"), ("EVALUATING", "BLOCKED", "BLOCKER_RAISED"),
    ("EVALUATING", "INCONCLUSIVE", "EVIDENCE_INSUFFICIENT"), ("REWORK_PLANNING", "DEVELOPING", "REWORK_ACCEPTED"),
    ("REWORK_PLANNING", "BLOCKED", "BLOCKER_RAISED"), ("REWORK_PLANNING", "FAILED", "FAILURE_ESTABLISHED"),
    ("BLOCKED", "FAILED", "FEATURE_ABANDONED"),
    ("INCONCLUSIVE", "BLOCKED", "BLOCKER_RAISED"),  # tamper rule
}


def test_edge_table_equals_section_15() -> None:
    assert {(e.source.value, e.target.value, e.reason) for e in EDGES} == SPEC_15


def test_every_edge_reason_is_legal_in_the_vocabulary() -> None:
    for e in EDGES:
        assert (e.source.value, e.target.value) in TRANSITION_REASONS[e.reason], e


def test_tamper_rule_covers_every_non_final_state() -> None:
    blockable = {e.source for e in EDGES if e.target is S.BLOCKED}
    assert blockable | {S.BLOCKED} == set(State) - FINAL


ALL_PAIRS = [(a, b) for a in State for b in State]


@pytest.mark.parametrize("source,target", ALL_PAIRS, ids=[f"{a.value}->{b.value}" for a, b in ALL_PAIRS])
def test_only_table_edges_allowed(source: State, target: State) -> None:
    reasons = [e.reason for e in EDGES if e.source is source and e.target is target]
    for reason in set(TRANSITION_REASONS) - set(reasons) - {"BLOCKER_RESOLVED", "ADDITIONAL_EVIDENCE_AUTHORISED"}:
        with pytest.raises(HarnessError) as info:
            check_transition(source, target, reason, profile="full", qe_test_design="required", tamper=True)
        assert info.value.code is ReasonCode.TRANSITION_NOT_ALLOWED


@pytest.mark.parametrize("profile,design,target,ok", [
    ("full", "required", S.TEST_DESIGN, True), ("full", "optional", S.TEST_DESIGN, True),
    ("full", "off", S.TEST_DESIGN, False), ("lite", "off", S.TEST_DESIGN, False),
    ("full", "required", S.DEVELOPING, False), ("full", "off", S.DEVELOPING, True),
    ("lite", "off", S.DEVELOPING, True),
])
def test_plan_review_routing(profile: str, design: str, target: State, ok: bool) -> None:
    if ok:
        check_transition(S.PLAN_REVIEW, target, "PLAN_ACCEPTED", profile=profile, qe_test_design=design)
    else:
        with pytest.raises(HarnessError):
            check_transition(S.PLAN_REVIEW, target, "PLAN_ACCEPTED", profile=profile, qe_test_design=design)


def test_verifying_reason_depends_on_profile() -> None:
    check_transition(S.VERIFYING, S.EVALUATING, "QE_RESULT_SUBMITTED", profile="full", qe_test_design="off")
    check_transition(S.VERIFYING, S.EVALUATING, "GATE_RUN_RECORDED", profile="lite", qe_test_design="off")
    with pytest.raises(HarnessError):
        check_transition(S.VERIFYING, S.EVALUATING, "GATE_RUN_RECORDED", profile="full", qe_test_design="off")


def test_development_cannot_go_directly_to_verifying() -> None:
    with pytest.raises(HarnessError):
        check_transition(S.DEVELOPING, S.VERIFYING, "HANDOFF_ACCEPTED", profile="full", qe_test_design="off")


def test_inconclusive_blocks_only_on_tamper() -> None:
    with pytest.raises(HarnessError):
        check_transition(S.INCONCLUSIVE, S.BLOCKED, "BLOCKER_RAISED", profile="full", qe_test_design="off")
    check_transition(S.INCONCLUSIVE, S.BLOCKED, "BLOCKER_RAISED", profile="full", qe_test_design="off", tamper=True)


# --- AC-002 ownership ---------------------------------------------------------------------

def test_ownership_tables_match_section_13_2() -> None:
    assert {s.value: o for s, o in OWNERS_FULL.items()} == {
        "CREATED": "kernel", "INITIALISING": "kernel", "PLANNING": "planner", "PLAN_REVIEW": "kernel",
        "TEST_DESIGN": "qe", "DEVELOPING": "development", "DEVELOPMENT_REVIEW": "kernel", "VERIFYING": "qe",
        "EVALUATING": "evaluator", "REWORK_PLANNING": "planner", "PASSED": "kernel", "FAILED": "kernel",
        "BLOCKED": "kernel", "INCONCLUSIVE": "kernel"}
    assert OWNERS_LITE[S.PLANNING] == OWNERS_LITE[S.VERIFYING] == OWNERS_LITE[S.REWORK_PLANNING] == "kernel"
    assert OWNERS_LITE[S.DEVELOPING] == "development"
    with pytest.raises(HarnessError):
        owner(S.TEST_DESIGN, "lite")


# --- engine -----------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path: Path) -> WorkflowEngine:
    audit = AuditLog(tmp_path / "audit.jsonl")
    store = StateStore(tmp_path / "orchestration-state.json", audit, state_key="workflow-F-001")
    doc = new_orchestration_state(workflow_id="workflow-F-001", feature_id="F-001",
                                  contract_path="features/active/F-001.yaml")
    doc["workflow"]["qe_test_design"] = "required"
    store.create(doc)
    return WorkflowEngine(store, audit, clock=lambda: "2026-11-24T00:00:00Z")


def walk(engine: WorkflowEngine, *steps: tuple[State, str]) -> dict:
    doc = engine.store.load()
    for target, reason in steps:
        doc = engine.transition(doc["workflow"]["revision"], target, reason)
    return doc


def test_transition_increments_revision_and_records_history(engine: WorkflowEngine) -> None:
    doc = walk(engine, (S.INITIALISING, "START_REQUESTED"), (S.PLANNING, "INITIALISATION_PASSED"))
    assert doc["workflow"]["revision"] == 2
    assert doc["workflow"]["previous_state"] == "INITIALISING"
    assert doc["history"] == [
        {"sequence": 1, "from": "CREATED", "to": "INITIALISING", "reason": "START_REQUESTED", "revision": 1,
         "owner": "kernel", "at": "2026-11-24T00:00:00Z"},
        {"sequence": 2, "from": "INITIALISING", "to": "PLANNING", "reason": "INITIALISATION_PASSED",
         "revision": 2, "owner": "planner", "at": "2026-11-24T00:00:00Z"},
    ]
    assert doc["ownership"]["persona"] == "planner"


def test_transition_audit_events_are_schema_valid(engine: WorkflowEngine) -> None:
    walk(engine, (S.INITIALISING, "START_REQUESTED"))
    engine.transition(1, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["MANDATORY_DEPENDENCY_UNAVAILABLE"])
    engine.transition(2, S.INITIALISING, "BLOCKER_RESOLVED")
    entries = engine.audit.entries()
    kinds = [e["event_type"] for e in entries]
    assert kinds.count("state.transition") == 3
    assert "blocker.raised" in kinds and "blocker.resolved" in kinds
    for entry in entries:
        assert validation_errors(entry, "audit-record") == [], entry


def test_illegal_transition_changes_nothing(engine: WorkflowEngine) -> None:
    state_before = engine.store.path.read_bytes()
    audit_before = engine.audit.path.read_bytes()
    with pytest.raises(HarnessError) as info:
        engine.transition(0, S.DEVELOPING, "PLAN_ACCEPTED")
    assert info.value.code is ReasonCode.TRANSITION_NOT_ALLOWED
    assert engine.store.path.read_bytes() == state_before
    assert engine.audit.path.read_bytes() == audit_before


def test_stale_revision_rejected(engine: WorkflowEngine) -> None:
    walk(engine, (S.INITIALISING, "START_REQUESTED"))
    with pytest.raises(HarnessError) as info:
        engine.transition(0, S.PLANNING, "INITIALISATION_PASSED")
    assert info.value.code is ReasonCode.WORKFLOW_REVISION_CONFLICT


def test_wrong_reason_rejected(engine: WorkflowEngine) -> None:
    with pytest.raises(HarnessError):
        engine.transition(0, S.INITIALISING, "PLAN_SUBMITTED")


@pytest.mark.parametrize("final", [S.PASSED, S.FAILED])
def test_final_states_reject_everything(engine: WorkflowEngine, final: State) -> None:
    path = [(S.INITIALISING, "START_REQUESTED"), (S.PLANNING, "INITIALISATION_PASSED"),
            (S.PLAN_REVIEW, "PLAN_SUBMITTED"), (S.TEST_DESIGN, "PLAN_ACCEPTED"),
            (S.DEVELOPING, "TESTS_ACCEPTED"), (S.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"),
            (S.VERIFYING, "HANDOFF_ACCEPTED"), (S.EVALUATING, "QE_RESULT_SUBMITTED")]
    path.append((S.PASSED, "COMPLETION_RULES_SATISFIED") if final is S.PASSED else (S.FAILED, "FAILURE_ESTABLISHED"))
    doc = walk(engine, *path)
    for target in State:
        for reason in TRANSITION_REASONS:
            with pytest.raises(HarnessError):
                engine.transition(doc["workflow"]["revision"], target, reason,
                                  reason_codes=["STATE_TAMPER_DETECTED"], tamper=True)


def test_block_requires_reason_code(engine: WorkflowEngine) -> None:
    with pytest.raises(HarnessError):
        engine.transition(0, S.BLOCKED, "BLOCKER_RAISED")
    with pytest.raises(HarnessError):
        engine.transition(0, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["NOT_A_CODE"])
    with pytest.raises(HarnessError):
        engine.transition(0, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["PACKAGE_REVOKED"])  # reserved


def test_block_and_resume_to_recorded_state(engine: WorkflowEngine) -> None:
    doc = walk(engine, (S.INITIALISING, "START_REQUESTED"), (S.PLANNING, "INITIALISATION_PASSED"))
    doc = engine.transition(2, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["TOOL_NOT_REGISTERED"], message="m")
    assert doc["blocker"]["resume_state"] == "PLANNING"
    assert doc["blocker"]["reason_codes"] == ["TOOL_NOT_REGISTERED"]
    with pytest.raises(HarnessError):
        engine.transition(3, S.DEVELOPING, "BLOCKER_RESOLVED")
    doc = engine.transition(3, S.PLANNING, "BLOCKER_RESOLVED")
    assert doc["blocker"] is None and doc["workflow"]["current_state"] == "PLANNING"


def test_block_with_explicit_resume_state(engine: WorkflowEngine) -> None:
    walk(engine, (S.INITIALISING, "START_REQUESTED"), (S.PLANNING, "INITIALISATION_PASSED"),
         (S.PLAN_REVIEW, "PLAN_SUBMITTED"), (S.TEST_DESIGN, "PLAN_ACCEPTED"), (S.DEVELOPING, "TESTS_ACCEPTED"),
         (S.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"))
    doc = engine.transition(6, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["PROFILE_ESCALATION_REQUIRED"],
                            resume_state=S.DEVELOPING)
    assert engine.transition(7, S.DEVELOPING, "BLOCKER_RESOLVED")["workflow"]["current_state"] == "DEVELOPING"


def test_abandon_from_blocked(engine: WorkflowEngine) -> None:
    engine.transition(0, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["MANDATORY_DEPENDENCY_UNAVAILABLE"])
    assert engine.transition(1, S.FAILED, "FEATURE_ABANDONED")["workflow"]["current_state"] == "FAILED"


def test_inconclusive_resumes_to_verifying(engine: WorkflowEngine) -> None:
    doc = walk(engine, (S.INITIALISING, "START_REQUESTED"), (S.PLANNING, "INITIALISATION_PASSED"),
               (S.PLAN_REVIEW, "PLAN_SUBMITTED"), (S.TEST_DESIGN, "PLAN_ACCEPTED"), (S.DEVELOPING, "TESTS_ACCEPTED"),
               (S.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"), (S.VERIFYING, "HANDOFF_ACCEPTED"),
               (S.EVALUATING, "QE_RESULT_SUBMITTED"), (S.INCONCLUSIVE, "EVIDENCE_INSUFFICIENT"))
    assert doc["workflow"]["resumable_state"] == "VERIFYING"
    with pytest.raises(HarnessError):
        engine.transition(9, S.PASSED, "ADDITIONAL_EVIDENCE_AUTHORISED")
    assert engine.transition(9, S.VERIFYING, "ADDITIONAL_EVIDENCE_AUTHORISED")["workflow"]["current_state"] == "VERIFYING"


def test_rework_cycle_counter(engine: WorkflowEngine) -> None:
    doc = walk(engine, (S.INITIALISING, "START_REQUESTED"), (S.PLANNING, "INITIALISATION_PASSED"),
               (S.PLAN_REVIEW, "PLAN_SUBMITTED"), (S.TEST_DESIGN, "PLAN_ACCEPTED"), (S.DEVELOPING, "TESTS_ACCEPTED"),
               (S.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED"), (S.VERIFYING, "HANDOFF_ACCEPTED"),
               (S.EVALUATING, "QE_RESULT_SUBMITTED"), (S.REWORK_PLANNING, "REWORK_ALLOWED"))
    assert doc["rework"]["current_cycle"] == 1


def test_replace_blocker_keeps_state(engine: WorkflowEngine) -> None:
    engine.transition(0, S.BLOCKED, "BLOCKER_RAISED", reason_codes=["TOOL_NOT_REGISTERED"])
    doc = engine.replace_blocker(1, ["STATE_TAMPER_DETECTED"], "tamper")
    assert doc["workflow"]["current_state"] == "BLOCKED" and doc["workflow"]["revision"] == 2
    assert doc["blocker"]["reason_codes"] == ["STATE_TAMPER_DETECTED", "TOOL_NOT_REGISTERED"]


# --- AC-008 restart, crash, sample replay -------------------------------------------------------

def test_fresh_engine_continues_from_disk(engine: WorkflowEngine, tmp_path: Path) -> None:
    walk(engine, (S.INITIALISING, "START_REQUESTED"))
    audit = AuditLog(tmp_path / "audit.jsonl")
    fresh = WorkflowEngine(StateStore(tmp_path / "orchestration-state.json", audit, state_key="workflow-F-001"), audit)
    assert fresh.transition(1, S.PLANNING, "INITIALISATION_PASSED")["workflow"]["revision"] == 2


def test_crash_during_transition_leaves_prior_state(engine: WorkflowEngine, monkeypatch) -> None:
    walk(engine, (S.INITIALISING, "START_REQUESTED"))

    def boom(*_):
        raise OSError("power loss")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        engine.transition(1, S.PLANNING, "INITIALISATION_PASSED")
    monkeypatch.undo()
    doc = engine.store.load()
    assert doc["workflow"]["current_state"] == "INITIALISING" and doc["workflow"]["revision"] == 1
    assert engine.transition(1, S.PLANNING, "INITIALISATION_PASSED")["workflow"]["revision"] == 2


def test_sample_run_transitions_replay(engine: WorkflowEngine) -> None:
    sample = Path(__file__).resolve().parents[2] / "tools" / "monitor" / "sample-run.jsonl"
    records = [json.loads(l) for l in sample.read_text().splitlines() if l.strip()]
    doc = engine.store.load()
    for r in records:
        if r["event_type"] == "state.transition":
            d = r["details"]
            assert doc["workflow"]["current_state"] == d["from"]
            doc = engine.transition(doc["workflow"]["revision"], State(d["to"]), d["reason"])
    assert doc["workflow"]["current_state"] == "PASSED"
    assert doc["rework"]["current_cycle"] == 1
