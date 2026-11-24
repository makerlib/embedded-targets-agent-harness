"""AC-008: append-only, hash-chained audit log with redaction (§9.19)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from et_harness.audit.log import SPEC_FIELDS, AuditLog
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit" / "audit.jsonl", clock=lambda: "2026-11-24T00:00:00Z")


def test_entry_carries_all_spec_fields(log: AuditLog) -> None:
    entry = log.append("tool.invoke", workflow_id="wf-1", tool="repository.read",
                       reason_codes=[ReasonCode.TOOL_PERMISSION_DENIED])
    for name in SPEC_FIELDS:
        assert name in entry
    assert entry["sequence"] == 1
    assert entry["reason_codes"] == ["TOOL_PERMISSION_DENIED"]
    assert entry["evidence"] == []
    assert entry["timestamp"] == "2026-11-24T00:00:00Z"


def test_entries_are_json_lines_and_chained(log: AuditLog) -> None:
    first = log.append("a")
    second = log.append("b")
    lines = log.path.read_text().splitlines()
    assert [json.loads(l)["event_type"] for l in lines] == ["a", "b"]
    assert second["previous_entry_hash"] == first["entry_hash"]
    assert [e["sequence"] for e in log.entries()] == [1, 2]


def test_append_only_preserves_earlier_bytes(log: AuditLog) -> None:
    log.append("a")
    before = log.path.read_bytes()
    log.append("b")
    assert log.path.read_bytes().startswith(before)


def test_edited_entry_detected(log: AuditLog) -> None:
    log.append("a", result="ok")
    log.append("b")
    text = log.path.read_text().replace('"result":"ok"', '"result":"forged"')
    log.path.write_text(text)
    with pytest.raises(HarnessError) as info:
        log.entries()
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED


def test_removed_middle_entry_detected(log: AuditLog) -> None:
    for name in "abc":
        log.append(name)
    lines = log.path.read_text().splitlines()
    log.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(HarnessError) as info:
        log.entries()
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED


def test_garbage_line_detected(log: AuditLog) -> None:
    log.append("a")
    with open(log.path, "a") as handle:
        handle.write("not json\n")
    with pytest.raises(HarnessError) as info:
        log.entries()
    assert info.value.code is ReasonCode.STATE_TAMPER_DETECTED


def test_secrets_are_redacted(log: AuditLog) -> None:
    entry = log.append("tool.output", details={"stdout": "login token=s3cr3tvalue ok",
                                               "header": "Authorization: Bearer abcdefghijklmnopqrstu"})
    raw = log.path.read_text()
    assert "s3cr3tvalue" not in raw
    assert "abcdefghijklmnopqrstu" not in raw
    assert "token=[REDACTED]" in entry["details"]["stdout"]
    log.entries()  # chain still verifies after redaction


def test_custom_redaction_patterns(tmp_path: Path) -> None:
    from et_harness.common.redaction import Redactor

    log = AuditLog(tmp_path / "a.jsonl", redactor=Redactor([r"PROJ-\d{4}"]))
    log.append("x", details={"msg": "customer PROJ-1234"})
    assert "PROJ-1234" not in log.path.read_text()


def test_unknown_field_rejected(log: AuditLog) -> None:
    with pytest.raises(HarnessError):
        log.append("x", colour="blue")


def test_expected_sequence_guard(log: AuditLog) -> None:
    log.append("a")
    with pytest.raises(HarnessError) as info:
        log.append("b", expected_sequence=1)
    assert info.value.code is ReasonCode.WORKFLOW_REVISION_CONFLICT


def test_empty_log(log: AuditLog) -> None:
    assert log.entries() == []
    assert log.last_entry() is None
    assert log.next_sequence() == 1
