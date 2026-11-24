"""HK-015 AC-006: simulator output and live replay through the stream."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from et_harness.audit.log import AuditLog
from et_harness.common.schemas import validate_document, validation_errors
from monitor_helpers import SAMPLE, SIMULATOR, start_monitor, stop_monitor

FAST = ["--speed", "100000", "--min-gap", "0", "--max-gap", "0", "--quiet"]


def simulate(harness_dir: Path, *extra: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SIMULATOR), "--harness-dir", str(harness_dir), *extra],
                          capture_output=True, text=True, timeout=120, cwd=cwd)


def test_writes_only_inside_harness_dir(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    target = work / "scratch" / ".agent-harness"
    result = simulate(target, *FAST, cwd=work)
    assert result.returncode == 0, result.stderr
    written = sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file())
    assert written == [
        str(Path("scratch/.agent-harness/audit/audit.jsonl")),
        str(Path("scratch/.agent-harness/state/orchestration-state.json")),
    ]


def test_output_chain_verifies_after_timestamp_shift(tmp_path: Path) -> None:
    target = tmp_path / ".agent-harness"
    assert simulate(target, *FAST).returncode == 0
    entries = AuditLog(target / "audit" / "audit.jsonl").entries()  # raises on a broken chain
    sample = [json.loads(l) for l in SAMPLE.read_text().splitlines() if l.strip()]
    assert len(entries) == len(sample)
    assert entries[0]["timestamp"] != sample[0]["timestamp"]
    for entry in entries:
        assert validation_errors(entry, "audit-record") == []


def test_state_file_validates_and_tracks_the_run(tmp_path: Path) -> None:
    target = tmp_path / ".agent-harness"
    assert simulate(target, *FAST).returncode == 0
    state = json.loads((target / "state" / "orchestration-state.json").read_text())
    validate_document(state, "orchestration-state")
    sample = [json.loads(l) for l in SAMPLE.read_text().splitlines() if l.strip()]
    transitions = [r for r in sample if r["event_type"] == "state.transition"]
    assert state["workflow"]["current_state"] == "PASSED"
    assert [h["reason"] for h in state["history"]] == [t["details"]["reason"] for t in transitions]
    assert state["rework"]["current_cycle"] == 1
    assert state["integrity"]["audit_sequence"] == transitions[-1]["sequence"]
    assert state["integrity"]["previous_state_hash"].startswith("sha256:")


def test_refuses_to_overwrite_a_real_log(tmp_path: Path) -> None:
    target = tmp_path / ".agent-harness"
    (target / "audit").mkdir(parents=True)
    log = target / "audit" / "audit.jsonl"
    log.write_text('{"real": true}\n')
    result = simulate(target, *FAST)
    assert result.returncode == 3
    assert log.read_text() == '{"real": true}\n'
    assert not (target / "state").exists()
    assert simulate(target, *FAST, "--force").returncode == 0


def test_live_replay_delivers_every_event_in_order(tmp_path: Path) -> None:
    target = tmp_path / ".agent-harness"
    target.mkdir()
    mon, _ = start_monitor(target)
    reader = None
    try:
        reader = mon.stream(0)
        sim = subprocess.Popen(
            [sys.executable, str(SIMULATOR), "--harness-dir", str(target),
             "--speed", "100000", "--min-gap", "0.01", "--max-gap", "0.01", "--quiet"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        seen = reader.collect(
            lambda s: sum(e == "audit" for e, _ in s) == 68
            and any(e == "state" and json.loads(d)["workflow"]["current_state"] == "PASSED" for e, d in s),
            timeout=60,
        )
        assert sim.wait(timeout=60) == 0
        sequences = [json.loads(d)["sequence"] for e, d in seen if e == "audit"]
        assert sequences == list(range(1, 69))
        final_state = json.loads((target / "state" / "orchestration-state.json").read_text())
        last_streamed = [json.loads(d) for e, d in seen if e == "state"][-1]
        assert last_streamed["workflow"]["revision"] == final_state["workflow"]["revision"]
        assert "warning" not in [e for e, _ in seen]
    finally:
        if reader:
            reader.close()
        stop_monitor(mon)
