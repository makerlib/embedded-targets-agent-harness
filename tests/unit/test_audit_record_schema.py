"""HK-015 AC-004 / AC-005: audit record schema, vocabulary and sample run (§9.19)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from et_harness.audit.log import AuditLog
from et_harness.audit.vocabulary import (
    EVENTS,
    TRANSITION_REASONS,
    build_schema,
    emittable_reason_codes,
    transition_is_allowed,
)
from et_harness.common.reason_codes import RESERVED_V1_2, ReasonCode
from et_harness.common.schemas import validation_errors
from et_harness.sessions.documents import new_orchestration_state
from et_harness.sessions.state_store import StateStore

REPO = Path(__file__).resolve().parents[2]
SCHEMA_FILE = REPO / "schemas" / "audit-record.schema.json"
SAMPLE = REPO / "tools" / "monitor" / "sample-run.jsonl"
INDEX = REPO / "tools" / "monitor" / "index.html"

# §9.19.2 table, transcribed independently of the vocabulary module.
SPEC_EVENT_TYPES = {
    "workflow.created", "state.write", "state.write_rolled_back", "state.transition",
    "lease.acquired", "lease.released", "lease.revoked", "tool.decision", "tool.executed",
    "gate.attempt", "handoff.submitted", "handoff.accepted", "handoff.rejected",
    "checkpoint.recorded", "scope.violation", "enforcement.probe", "package.resolved",
    "approval.recorded", "blocker.raised", "blocker.resolved", "evaluation.result",
    "verdict.issued",
}


def sample_records() -> list[dict]:
    return [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- schema file ---------------------------------------------------------------

def test_schema_file_matches_vocabulary_module() -> None:
    assert json.loads(SCHEMA_FILE.read_text()) == build_schema()


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(json.loads(SCHEMA_FILE.read_text()))


def test_vocabulary_matches_spec_table() -> None:
    assert set(EVENTS) == SPEC_EVENT_TYPES


def test_schema_reason_codes_are_section_27_codes() -> None:
    assert set(emittable_reason_codes()) == {c.value for c in ReasonCode} - {c.value for c in RESERVED_V1_2}


def test_unknown_event_type_rejected() -> None:
    record = sample_records()[0] | {"event_type": "agent.chatted"}
    assert validation_errors(record, "audit-record")


def test_missing_required_details_key_rejected() -> None:
    record = next(r for r in sample_records() if r["event_type"] == "gate.attempt")
    del record["details"]["execution_profile"]
    assert any("execution_profile" in e for e in validation_errors(record, "audit-record"))


def test_transition_reason_in_reason_codes_rejected() -> None:
    record = next(r for r in sample_records() if r["event_type"] == "state.transition")
    record["reason_codes"] = ["START_REQUESTED"]
    assert validation_errors(record, "audit-record")


def test_reserved_code_rejected() -> None:
    record = sample_records()[0] | {"reason_codes": ["PACKAGE_REVOKED"]}
    assert validation_errors(record, "audit-record")


def test_transition_reasons_only_cover_legal_section_15_edges() -> None:
    legal_targets = {
        "CREATED": {"INITIALISING", "BLOCKED"},
        "INITIALISING": {"PLANNING", "BLOCKED", "FAILED"},
        "PLANNING": {"PLAN_REVIEW", "BLOCKED"},
        "PLAN_REVIEW": {"TEST_DESIGN", "DEVELOPING", "PLANNING", "BLOCKED"},
        "TEST_DESIGN": {"DEVELOPING", "BLOCKED"},
        "DEVELOPING": {"DEVELOPMENT_REVIEW", "BLOCKED", "FAILED"},
        "DEVELOPMENT_REVIEW": {"VERIFYING", "DEVELOPING", "BLOCKED"},
        "VERIFYING": {"EVALUATING", "BLOCKED"},
        "EVALUATING": {"PASSED", "FAILED", "BLOCKED", "INCONCLUSIVE", "REWORK_PLANNING"},
        "REWORK_PLANNING": {"DEVELOPING", "BLOCKED", "FAILED"},
    }
    for reason, pairs in TRANSITION_REASONS.items():
        for src, dst in pairs:
            if src in ("BLOCKED", "INCONCLUSIVE"):
                continue  # resume edges go to the recorded resumable state
            if dst == "BLOCKED":
                continue  # tamper rule: any state except PASSED and FAILED may block
            assert dst in legal_targets[src], (reason, src, dst)


# --- sample run (AC-005) ----------------------------------------------------------

def test_sample_records_validate() -> None:
    for record in sample_records():
        assert validation_errors(record, "audit-record") == [], record["sequence"]


def test_sample_hash_chain_and_sequence_verify() -> None:
    entries = AuditLog(SAMPLE).entries()  # raises STATE_TAMPER_DETECTED on any break
    assert [e["sequence"] for e in entries] == list(range(1, len(entries) + 1))
    assert len(entries) == 68


def test_sample_reason_codes_are_section_27_only() -> None:
    allowed = set(emittable_reason_codes())
    for record in sample_records():
        assert set(record["reason_codes"]) <= allowed


def test_sample_transitions_use_legal_reasons() -> None:
    transitions = [r for r in sample_records() if r["event_type"] == "state.transition"]
    assert transitions
    for record in transitions:
        d = record["details"]
        assert transition_is_allowed(d["reason"], d["from"], d["to"]), d
        assert record["state"] == d["to"]


def test_sample_ends_with_advisory_passed() -> None:
    verdict = next(r for r in sample_records() if r["event_type"] == "verdict.issued")
    assert verdict["result"] == "PASSED"
    assert verdict["details"]["authority"] == "advisory"
    assert "VERDICT_NOT_AUTHORITATIVE" in verdict["reason_codes"]


def test_page_embeds_the_same_sample() -> None:
    html = INDEX.read_text(encoding="utf-8")
    match = re.search(r'<script id="sample-run" type="application/x-ndjson">\n(.*?)</script>', html, re.S)
    assert match is not None
    assert match.group(1) == SAMPLE.read_text(encoding="utf-8")


# --- kernel conformance (AC-004) -------------------------------------------------

def test_kernel_state_events_validate(tmp_path: Path, monkeypatch) -> None:
    import os

    audit = AuditLog(tmp_path / "audit.jsonl")
    store = StateStore(tmp_path / "orchestration-state.json", audit)
    store.create(new_orchestration_state(workflow_id="workflow-X-001", feature_id="X",
                                         contract_path="features/active/X.yaml"))

    def to_init(doc: dict) -> None:
        doc["workflow"]["current_state"] = "INITIALISING"

    store.update(0, to_init)

    def boom(*_):
        raise OSError("crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.update(1, lambda d: None)
    monkeypatch.undo()
    store.load()  # records state.write_rolled_back

    entries = audit.entries()
    assert {e["event_type"] for e in entries} == {"state.write", "state.write_rolled_back"}
    for entry in entries:
        assert validation_errors(entry, "audit-record") == [], entry
