"""Helpers for monitor integration tests: start the server, read its SSE stream.

Fixtures are re-exported by conftest.py in this directory.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MONITOR_DIR = REPO / "tools" / "monitor"
SERVER = MONITOR_DIR / "harness_monitor.py"
SIMULATOR = MONITOR_DIR / "simulate_run.py"
SAMPLE = MONITOR_DIR / "sample-run.jsonl"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tree_digest(root: Path) -> dict[str, str]:
    """Map every file under root to its SHA-256 (detects edits and new files)."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@dataclass
class Monitor:
    port: int
    process: subprocess.Popen

    def request(self, method: str, path: str) -> tuple[int, bytes, dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            body = response.read()
            return response.status, body, dict(response.getheaders())
        finally:
            conn.close()

    def snapshot(self) -> dict:
        status, body, _ = self.request("GET", "/api/snapshot")
        assert status == 200
        return json.loads(body)

    def stream(self, offset: int = 0) -> "SseReader":
        return SseReader(self.port, offset)


class SseReader:
    """Minimal Server-Sent Events client with a deadline."""

    def __init__(self, port: int, offset: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.sendall(
            f"GET /api/stream?offset={offset} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            "Accept: text/event-stream\r\n\r\n".encode()
        )
        self.buffer = b""
        self._headers_done = False

    def _fill(self, deadline: float) -> bool:
        self.sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError:
            return False
        except socket.timeout:
            return False
        if not chunk:
            raise ConnectionError("stream closed")
        self.buffer += chunk
        return True

    def events(self, timeout: float) -> Iterator[tuple[str, str]]:
        """Yield (event, data) pairs until ``timeout`` seconds pass without completion."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._headers_done:
                if b"\r\n\r\n" in self.buffer:
                    self.buffer = self.buffer.split(b"\r\n\r\n", 1)[1]
                    self._headers_done = True
                    continue
            else:
                while b"\n\n" in self.buffer:
                    block, self.buffer = self.buffer.split(b"\n\n", 1)
                    event, data = "message", []
                    for line in block.decode("utf-8").splitlines():
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            data.append(line[6:])
                    if block.startswith(b":"):
                        continue
                    yield event, "\n".join(data)
            self._fill(deadline)

    def collect(self, until, timeout: float = 15.0) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for item in self.events(timeout):
            seen.append(item)
            if until(seen):
                return seen
        raise AssertionError(f"condition not met within {timeout}s; got {[e for e, _ in seen]}")

    def close(self) -> None:
        self.sock.close()


def start_monitor(harness_dir: Path, *extra: str, host: str | None = None) -> tuple[Monitor, str]:
    port = free_port()
    args = [sys.executable, str(SERVER), "--harness-dir", str(harness_dir), "--port", str(port), *extra]
    if host:
        args += ["--host", host]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               cwd=harness_dir.parent)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return Monitor(port, process), ""
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.05)
    process.kill()
    out, err = process.communicate()
    raise AssertionError(f"monitor did not start: {out}\n{err}")


def stop_monitor(monitor: Monitor) -> tuple[str, str]:
    monitor.process.terminate()
    try:
        return monitor.process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        monitor.process.kill()
        return monitor.process.communicate()


@pytest.fixture
def harness_dir(tmp_path: Path) -> Path:
    """A product .agent-harness with the sample audit log and a state file."""
    root = tmp_path / "product" / ".agent-harness"
    (root / "audit").mkdir(parents=True)
    (root / "state").mkdir()
    shutil.copy(SAMPLE, root / "audit" / "audit.jsonl")
    (root / "state" / "orchestration-state.json").write_text(
        json.dumps({"workflow": {"current_state": "PASSED", "revision": 12}}), encoding="utf-8")
    (root / "evidence").mkdir()
    (root / "evidence" / "verdict.yaml").write_text("verdict: PASSED\n", encoding="utf-8")
    return root


@pytest.fixture
def monitor(harness_dir: Path) -> Iterator[Monitor]:
    mon, _ = start_monitor(harness_dir)
    yield mon
    stop_monitor(mon)


def python_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    return env
