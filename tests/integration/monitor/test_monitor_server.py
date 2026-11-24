"""HK-015 AC-001 / AC-002 / AC-003: monitor server routes, read-only, stream."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from monitor_helpers import Monitor, start_monitor, stop_monitor, tree_digest

# --- AC-001: routes, methods, binding ---------------------------------------------

@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_serves_index(monitor: Monitor, path: str) -> None:
    status, body, headers = monitor.request("GET", path)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"Harness Monitor" in body


def test_head_returns_headers_only(monitor: Monitor) -> None:
    status, body, headers = monitor.request("HEAD", "/api/snapshot")
    assert status == 200 and body == b""
    assert headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("path", ["/", "/api/snapshot", "/api/stream"])
def test_mutating_methods_refused(monitor: Monitor, method: str, path: str) -> None:
    status, _, _ = monitor.request(method, path)
    assert status == 405


@pytest.mark.parametrize("path", [
    "/harness_monitor.py", "/sample-run.jsonl", "/audit/audit.jsonl", "/state/orchestration-state.json",
    "/../.agent-harness/audit/audit.jsonl", "/%2e%2e/evidence/verdict.yaml", "/api", "/api/other",
    "/index.html/../harness_monitor.py",
])
def test_only_three_routes(monitor: Monitor, path: str) -> None:
    status, _, _ = monitor.request("GET", path)
    assert status == 404


def test_binds_localhost_by_default(monitor: Monitor) -> None:
    import socket

    # Reachable on loopback ...
    socket.create_connection(("127.0.0.1", monitor.port), timeout=1).close()
    # ... and not on another interface address of this machine, if there is one.
    others = {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    others.discard("127.0.0.1")
    others = {a for a in others if not a.startswith("127.")}
    for address in others:
        with pytest.raises(OSError):
            socket.create_connection((address, monitor.port), timeout=1).close()


def test_warns_when_bound_beyond_localhost(harness_dir: Path) -> None:
    mon, _ = start_monitor(harness_dir, host="0.0.0.0")
    _, err = stop_monitor(mon)
    assert "Warning: serving beyond this machine" in err


def test_no_warning_on_localhost(harness_dir: Path) -> None:
    mon, _ = start_monitor(harness_dir)
    _, err = stop_monitor(mon)
    assert "Warning" not in err


# --- AC-002: read-only -------------------------------------------------------------

def test_snapshot_and_stream_leave_files_untouched(harness_dir: Path) -> None:
    product = harness_dir.parent
    before = tree_digest(product)
    mon, _ = start_monitor(harness_dir)
    try:
        snap = mon.snapshot()
        assert len(snap["events"]) == 68
        reader = mon.stream(0)
        reader.collect(lambda seen: sum(e == "audit" for e, _ in seen) == 68)
        reader.close()
        mon.request("HEAD", "/api/stream")
        for method in ("POST", "PUT", "DELETE"):
            mon.request(method, "/api/snapshot")
    finally:
        stop_monitor(mon)
    assert tree_digest(product) == before


def test_snapshot_shape(monitor: Monitor) -> None:
    snap = monitor.snapshot()
    assert snap["kind"] == "harness-monitor-snapshot"
    assert snap["audit_path"].endswith(os.path.join("audit", "audit.jsonl"))
    assert snap["state_path"].endswith(os.path.join("state", "orchestration-state.json"))
    assert snap["state"]["workflow"]["revision"] == 12
    assert snap["malformed"] == 0
    assert [e["sequence"] for e in snap["events"]] == list(range(1, 69))


def test_snapshot_without_files(tmp_path: Path) -> None:
    empty = tmp_path / ".agent-harness"
    empty.mkdir()
    mon, _ = start_monitor(empty)
    try:
        snap = mon.snapshot()
    finally:
        stop_monitor(mon)
    assert snap["events"] == [] and snap["audit_path"] is None and snap["state"] is None
    assert list(empty.iterdir()) == []


# --- AC-003: stream behaviour -------------------------------------------------------

def audit_lines(harness_dir: Path) -> list[str]:
    return (harness_dir / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()


def test_partial_line_held_until_complete(harness_dir: Path) -> None:
    lines = audit_lines(harness_dir)
    log = harness_dir / "audit" / "audit.jsonl"
    log.write_text(lines[0] + "\n" + lines[1][:40], encoding="utf-8")
    mon, _ = start_monitor(harness_dir)
    reader = mon.stream(0)
    try:
        seen = reader.collect(lambda s: any(e == "audit" for e, _ in s))
        assert [json.loads(d)["sequence"] for e, d in seen if e == "audit"] == [1]
        with pytest.raises(AssertionError):  # nothing more while the line is partial
            reader.collect(lambda s: any(e == "audit" for e, _ in s), timeout=1.5)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(lines[1][40:] + "\n")
        seen = reader.collect(lambda s: any(e == "audit" for e, _ in s))
        assert [json.loads(d)["sequence"] for e, d in seen if e == "audit"] == [2]
    finally:
        reader.close()
        stop_monitor(mon)


def test_truncation_emits_reset(harness_dir: Path) -> None:
    log = harness_dir / "audit" / "audit.jsonl"
    lines = audit_lines(harness_dir)
    mon, _ = start_monitor(harness_dir)
    reader = mon.stream(0)
    try:
        reader.collect(lambda s: sum(e == "audit" for e, _ in s) == 68)
        with open(log, "w", encoding="utf-8") as handle:  # same file, shorter content
            handle.write(lines[0] + "\n")
        seen = reader.collect(lambda s: any(e == "audit" for e, _ in s))
        assert seen[0][0] == "reset"
        assert json.loads([d for e, d in seen if e == "audit"][0])["sequence"] == 1
    finally:
        reader.close()
        stop_monitor(mon)


def test_replaced_file_emits_reset(harness_dir: Path) -> None:
    log = harness_dir / "audit" / "audit.jsonl"
    lines = audit_lines(harness_dir)
    mon, _ = start_monitor(harness_dir)
    reader = mon.stream(0)
    try:
        reader.collect(lambda s: sum(e == "audit" for e, _ in s) == 68)
        replacement = log.with_name("audit.new")
        replacement.write_text("\n".join(lines) + "\n", encoding="utf-8")  # same size, new file
        os.replace(replacement, log)
        seen = reader.collect(lambda s: sum(e == "audit" for e, _ in s) == 68)
        assert "reset" in [e for e, _ in seen]
    finally:
        reader.close()
        stop_monitor(mon)


def test_malformed_line_emits_warning(harness_dir: Path) -> None:
    log = harness_dir / "audit" / "audit.jsonl"
    lines = audit_lines(harness_dir)
    log.write_text(lines[0] + "\n{not json\n" + lines[1] + "\n", encoding="utf-8")
    mon, _ = start_monitor(harness_dir)
    reader = mon.stream(0)
    try:
        seen = reader.collect(lambda s: sum(e == "audit" for e, _ in s) == 2)
        kinds = [e for e, _ in seen if e in ("audit", "warning")]
        assert kinds == ["audit", "warning", "audit"]
    finally:
        reader.close()
        stop_monitor(mon)
    snap_mon, _ = start_monitor(harness_dir)
    try:
        assert snap_mon.snapshot()["malformed"] == 1
    finally:
        stop_monitor(snap_mon)


def test_state_change_emits_state_event(harness_dir: Path) -> None:
    state = harness_dir / "state" / "orchestration-state.json"
    mon, _ = start_monitor(harness_dir)
    reader = mon.stream(0)
    try:
        reader.collect(lambda s: sum(e == "audit" for e, _ in s) == 68)
        tmp = state.with_name("next.tmp")
        tmp.write_text(json.dumps({"workflow": {"current_state": "PASSED", "revision": 13}}), encoding="utf-8")
        os.replace(tmp, state)
        seen = reader.collect(lambda s: any(e == "state" for e, _ in s))
        payload = json.loads([d for e, d in seen if e == "state"][-1])
        assert payload["workflow"]["revision"] == 13
    finally:
        reader.close()
        stop_monitor(mon)


def test_stream_resumes_from_offset(monitor: Monitor) -> None:
    snap = monitor.snapshot()
    reader = monitor.stream(snap["offset"])
    try:
        with pytest.raises(AssertionError):
            reader.collect(lambda s: any(e == "audit" for e, _ in s), timeout=1.5)
    finally:
        reader.close()
