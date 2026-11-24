"""AC-006 / AC-007: atomic state store, revisions and integrity chain (§9.5, §16)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from et_harness.audit.log import AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.hashing import digest
from et_harness.common.reason_codes import ReasonCode
from et_harness.sessions.documents import new_orchestration_state
from et_harness.sessions.state_store import ROLLBACK_EVENT, WRITE_EVENT, StateStore


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    audit = AuditLog(tmp_path / "audit" / "audit.jsonl")
    return StateStore(tmp_path / "state" / "workflow.json", audit)


def fresh() -> dict:
    return new_orchestration_state(
        workflow_id="workflow-FEATURE-001-001", feature_id="FEATURE-001",
        contract_path="features/active/FEATURE-001.yaml",
    )


def to_initialising(doc: dict) -> None:
    doc["workflow"]["previous_state"] = doc["workflow"]["current_state"]
    doc["workflow"]["current_state"] = "INITIALISING"


# --- AC-006 ----------------------------------------------------------------------

def test_create_and_load_roundtrip(store: StateStore) -> None:
    created = store.create(fresh())
    assert created["workflow"]["revision"] == 0
    assert created["integrity"]["previous_state_hash"] is None
    assert store.load() == created


def test_create_twice_rejected(store: StateStore) -> None:
    store.create(fresh())
    with pytest.raises(HarnessError) as info:
        store.create(fresh())
    assert info.value.code is ReasonCode.WORKFLOW_REVISION_CONFLICT


def test_update_increments_revision_and_chains_hash(store: StateStore) -> None:
    created = store.create(fresh())
    updated = store.update(0, to_initialising)
    assert updated["workflow"]["revision"] == 1
    assert updated["integrity"]["previous_state_hash"] == digest(created)
    again = store.update(1, lambda d: None)
    assert again["workflow"]["revision"] == 2
    assert again["integrity"]["previous_state_hash"] == digest(updated)


def test_each_write_appends_state_hash_to_audit(store: StateStore) -> None:
    store.create(fresh())
    updated = store.update(0, to_initialising)
    writes = [e for e in store.audit.entries() if e["event_type"] == WRITE_EVENT]
    assert len(writes) == 2
    assert writes[-1]["details"]["state_hash"] == digest(updated)
    assert writes[-1]["details"]["previous_state_hash"] == updated["integrity"]["previous_state_hash"]
    assert writes[-1]["sequence"] == updated["integrity"]["audit_sequence"]
    assert writes[-1]["state"] == "INITIALISING"
    assert writes[-1]["workflow_id"] == "workflow-FEATURE-001-001"


def test_stale_revision_rejected_without_change(store: StateStore) -> None:
    store.create(fresh())
    store.update(0, to_initialising)
    before = store.path.read_bytes()
    audit_before = store.audit.path.read_bytes()
    with pytest.raises(HarnessError) as info:
        store.update(0, to_initialising)
    assert info.value.code is ReasonCode.WORKFLOW_REVISION_CONFLICT
    assert store.path.read_bytes() == before
    assert store.audit.path.read_bytes() == audit_before


def test_invalid_document_rejected_without_change(store: StateStore) -> None:
    store.create(fresh())
    before = store.path.read_bytes()

    def corrupt(doc: dict) -> None:
        doc["workflow"]["current_state"] = "NOT_A_STATE"

    with pytest.raises(HarnessError) as info:
        store.update(0, corrupt)
    assert info.value.code is ReasonCode.CONFIG_INVALID
    assert store.path.read_bytes() == before
    store.load()


def test_mutator_cannot_forge_revision_or_integrity(store: StateStore) -> None:
    store.create(fresh())

    def forge(doc: dict) -> None:
        doc["workflow"]["revision"] = 99
        doc["integrity"]["previous_state_hash"] = "sha256:" + "0" * 64

    updated = store.update(0, forge)
    assert updated["workflow"]["revision"] == 1
    assert updated["integrity"]["previous_state_hash"] != "sha256:" + "0" * 64


def test_crash_during_replace_leaves_prior_state_valid(store: StateStore, monkeypatch) -> None:
    created = store.create(fresh())

    def boom(src, dst):  # simulated power loss at the rename step
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.update(0, to_initialising)
    monkeypatch.undo()

    assert not [p for p in store.path.parent.iterdir() if p.name.endswith(".tmp")]
    recovered = store.load()
    assert recovered == created
    last = store.audit.last_entry()
    assert last["event_type"] == ROLLBACK_EVENT
    assert last["reason_codes"] == ["STATE_WRITE_INTERRUPTED"]
    # The store keeps working at the recovered revision.
    assert store.update(0, to_initialising)["workflow"]["revision"] == 1
    assert store.load()["workflow"]["current_state"] == "INITIALISING"


def test_crash_during_write_keeps_old_file_bytes(store: StateStore, monkeypatch) -> None:
    store.create(fresh())
    before = store.path.read_bytes()
    real_fsync = os.fsync

    def failing_fsync(fd):
        real_fsync(fd)
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        store.update(0, to_initialising)
    monkeypatch.undo()
    assert store.path.read_bytes() == before
    store.load()


def test_fresh_process_resumes_from_disk(tmp_path: Path) -> None:
    audit_path, state_path = tmp_path / "audit.jsonl", tmp_path / "state.json"
    StateStore(state_path, AuditLog(audit_path)).create(fresh())
    reopened = StateStore(state_path, AuditLog(audit_path))
    assert reopened.load()["workflow"]["revision"] == 0
    reopened.update(0, to_initialising)


def test_missing_state_reports_not_found(store: StateStore) -> None:
    with pytest.raises(HarnessError) as info:
        store.load()
    assert info.value.code is ReasonCode.STATE_NOT_FOUND


def test_independent_state_keys_share_one_audit_log(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    a = StateStore(tmp_path / "a.json", audit)
    b = StateStore(tmp_path / "b.json", audit)
    a.create(fresh())
    b.create(fresh())
    a.update(0, to_initialising)
    assert b.load()["workflow"]["revision"] == 0
    assert a.load()["workflow"]["revision"] == 1


# --- AC-007: tamper detection ------------------------------------------------------

def edit_state(store: StateStore, change) -> None:
    doc = json.loads(store.path.read_text())
    change(doc)
    store.path.write_text(json.dumps(doc))


def expect_tamper(store: StateStore) -> None:
    with pytest.raises(HarnessError) as info:
        store.load()
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED


def test_hand_edited_state_detected(store: StateStore) -> None:
    store.create(fresh())
    edit_state(store, lambda d: d["workflow"].update(current_state="PASSED"))
    expect_tamper(store)


def test_hand_edit_with_consistent_revision_still_detected(store: StateStore) -> None:
    store.create(fresh())
    store.update(0, to_initialising)

    def forge(d: dict) -> None:
        d["workflow"]["current_state"] = "VERIFYING"
        d["workflow"]["revision"] = 2

    edit_state(store, forge)
    expect_tamper(store)


def test_reformatting_alone_is_not_tamper(store: StateStore) -> None:
    created = store.create(fresh())
    store.path.write_text(json.dumps(created, indent=None))  # same data, different bytes
    assert store.load() == created


def test_state_rolled_back_to_older_revision_detected(store: StateStore) -> None:
    store.create(fresh())
    old_bytes = store.path.read_bytes()
    store.update(0, to_initialising)
    store.update(1, lambda d: None)
    store.path.write_bytes(old_bytes)
    expect_tamper(store)


def test_truncated_audit_tail_detected(store: StateStore) -> None:
    store.create(fresh())
    store.update(0, to_initialising)
    lines = store.audit.path.read_text().splitlines()
    store.audit.path.write_text(lines[0] + "\n")
    expect_tamper(store)


def test_edited_audit_detected_through_store(store: StateStore) -> None:
    store.create(fresh())
    text = store.audit.path.read_text().replace('"committed"', '"approved"')
    store.audit.path.write_text(text)
    expect_tamper(store)


def test_deleted_audit_log_detected(store: StateStore) -> None:
    store.create(fresh())
    store.audit.path.unlink()
    expect_tamper(store)


def test_deleted_state_file_detected(store: StateStore) -> None:
    store.create(fresh())
    store.path.unlink()
    expect_tamper(store)


def test_unreadable_state_detected(store: StateStore) -> None:
    store.create(fresh())
    store.path.write_text("{ not json")
    expect_tamper(store)


def test_update_refuses_on_tamper(store: StateStore) -> None:
    store.create(fresh())
    edit_state(store, lambda d: d["workflow"].update(current_state="PASSED"))
    with pytest.raises(HarnessError) as info:
        store.update(0, to_initialising)
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED
