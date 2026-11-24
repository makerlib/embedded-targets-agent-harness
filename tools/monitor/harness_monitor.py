#!/usr/bin/env python3
"""Read-only live monitor for the Embedded Targets agent harness.

Serves index.html and streams the harness audit log and orchestration state
file to the browser with Server-Sent Events (SSE). Spec v1.1.1 §9.22.

Default inputs (spec §7, §9.19.1, §16):

    <harness-dir>/audit/audit.jsonl
    <harness-dir>/state/orchestration-state.json

It only reads files. It never writes to the repository, the harness state or
the evidence, and it serves no file other than index.html.

Usage:
    python harness_monitor.py --repo /path/to/product-repo
    python harness_monitor.py --harness-dir /path/to/.agent-harness --port 8765

Then open http://127.0.0.1:8765/ in a browser.

Standard library only. PyYAML is used for YAML state files when installed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0
MAX_SNAPSHOT_EVENTS = 20000


# --------------------------------------------------------------------------- files

class Sources:
    """Locates the audit log and state file. Explicit paths win over discovery."""

    def __init__(self, harness_dir: str, audit: str | None, state: str | None):
        self.harness_dir = harness_dir
        self.audit_override = audit
        self.state_override = state

    def audit_path(self) -> str | None:
        if self.audit_override:
            return self.audit_override if os.path.isfile(self.audit_override) else None
        fixed = os.path.join(self.harness_dir, "audit", "audit.jsonl")
        if os.path.isfile(fixed):
            return fixed
        files = glob.glob(os.path.join(self.harness_dir, "audit", "*.jsonl"))
        return max(files, key=os.path.getmtime) if files else None

    def state_path(self) -> str | None:
        if self.state_override:
            return self.state_override if os.path.isfile(self.state_override) else None
        fixed = os.path.join(self.harness_dir, "state", "orchestration-state.json")
        if os.path.isfile(fixed):
            return fixed
        candidates = []
        for ext in ("*.yaml", "*.yml", "*.json"):
            candidates += glob.glob(os.path.join(self.harness_dir, "state", ext))
        preferred = [p for p in candidates if "orchestration" in os.path.basename(p)]
        pool = preferred or candidates
        return max(pool, key=os.path.getmtime) if pool else None


def load_state(path: str) -> dict | None:
    """Parse the state file. JSON first (JSON is valid YAML), then YAML if PyYAML exists."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml  # type: ignore
    except ImportError:
        return {"_error": "State file is YAML; install PyYAML to read it."}
    try:
        data = yaml.safe_load(text)
        return json.loads(json.dumps(data, default=str))
    except Exception as exc:  # noqa: BLE001 - report, never crash the monitor
        return {"_error": f"Could not parse state file: {exc}"}


def read_complete_lines(path: str, offset: int) -> tuple[list[tuple[int, str]], int]:
    """Read whole lines from byte offset. Returns [(end_offset, line)], new_offset.

    A partial last line (kernel still writing) is left for the next poll.
    """
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], offset
    out, pos = [], offset
    for raw in data[: cut + 1].split(b"\n")[:-1]:
        pos += len(raw) + 1
        line = raw.decode("utf-8", errors="replace").strip()
        if line:
            out.append((pos, line))
    return out, offset + cut + 1


def display_path(path: str | None) -> str | None:
    """Path relative to the working directory where possible (never raises on Windows)."""
    if not path:
        return None
    try:
        return os.path.relpath(path, os.getcwd())
    except ValueError:  # different drive on Windows
        return path


def file_identity(path: str) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def file_sig(path: str | None) -> tuple | None:
    if not path:
        return None
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# --------------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "HarnessMonitor/0.1"
    sources: Sources
    index_path: str

    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("HARNESS_MONITOR_VERBOSE"):
            super().log_message(fmt, *args)

    # Read-only: anything other than GET/HEAD is refused.
    def do_POST(self):
        self.send_error(405, "Read-only monitor")

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = do_POST

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send_file(self.index_path, "text/html; charset=utf-8")
        if url.path == "/api/snapshot":
            return self._snapshot()
        if url.path == "/api/stream":
            qs = parse_qs(url.query)
            last_id = self.headers.get("Last-Event-ID")
            try:
                offset = int(last_id if last_id else qs.get("offset", ["0"])[0])
            except ValueError:
                offset = 0
            return self._stream(max(0, offset))
        self.send_error(404)

    def _headers(self, ctype: str, length: int | None = None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _send_file(self, path: str, ctype: str):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_error(500, "index.html not found next to harness_monitor.py")
        self._headers(ctype, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _snapshot(self):
        audit = self.sources.audit_path()
        events, offset, malformed = [], 0, 0
        if audit:
            lines, offset = read_complete_lines(audit, 0)
            for _, line in lines[-MAX_SNAPSHOT_EVENTS:]:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    malformed += 1
        state_path = self.sources.state_path()
        body = json.dumps({
            "kind": "harness-monitor-snapshot",
            "audit_path": display_path(audit),
            "state_path": display_path(state_path),
            "offset": offset,
            "malformed": malformed,
            "events": events,
            "state": load_state(state_path) if state_path else None,
        }).encode("utf-8")
        self._headers("application/json", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _emit(self, event: str, data: str, event_id: int | None = None):
        msg = ""
        if event_id is not None:
            msg += f"id: {event_id}\n"
        msg += f"event: {event}\n"
        for part in data.splitlines() or [""]:
            msg += f"data: {part}\n"
        self.wfile.write((msg + "\n").encode("utf-8"))

    def _stream(self, offset: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        if self.command == "HEAD":
            return
        audit_now = None
        audit_identity = None
        state_seen = file_sig(self.sources.state_path())
        last_beat = time.monotonic()
        try:
            while True:
                audit = self.sources.audit_path()
                if audit != audit_now:
                    if audit_now is not None:
                        offset = 0
                        self._emit("reset", "{}")
                    audit_now = audit
                    audit_identity = file_identity(audit) if audit else None
                    self._emit("info", json.dumps({"audit_path": display_path(audit)}))
                if audit:
                    identity = file_identity(audit)
                    if identity is None:
                        time.sleep(POLL_SECONDS)
                        continue
                    try:
                        size = os.path.getsize(audit)
                    except OSError:  # removed between stat calls; retry next poll
                        time.sleep(POLL_SECONDS)
                        continue
                    if identity != audit_identity:  # replaced by a new file
                        audit_identity = identity
                        offset = 0
                        self._emit("reset", "{}")
                    if size < offset:  # truncated or rotated
                        offset = 0
                        self._emit("reset", "{}")
                    if size > offset:
                        lines, offset = read_complete_lines(audit, offset)
                        for end, line in lines:
                            try:
                                json.loads(line)
                            except ValueError:
                                self._emit("warning", json.dumps({"offset": end, "message": "Malformed audit line skipped"}))
                                continue
                            self._emit("audit", line, end)
                sig = file_sig(self.sources.state_path())
                if sig and sig != state_seen:
                    state_seen = sig
                    self._emit("state", json.dumps(load_state(sig[0])))
                if time.monotonic() - last_beat > HEARTBEAT_SECONDS:
                    self.wfile.write(b": heartbeat\n\n")
                    last_beat = time.monotonic()
                self.wfile.flush()
                time.sleep(POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only live monitor for the agent harness.")
    ap.add_argument("--repo", default=".", help="Product repository root (default: current directory)")
    ap.add_argument("--harness-dir", help="Path to .agent-harness (default: <repo>/.agent-harness)")
    ap.add_argument("--audit", help="Explicit audit .jsonl file")
    ap.add_argument("--state", help="Explicit orchestration state file (.yaml or .json)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="Bind address. Default is local only; audit logs may contain restricted content.")
    args = ap.parse_args(argv)

    harness_dir = os.path.abspath(args.harness_dir or os.path.join(args.repo, ".agent-harness"))
    index = os.path.join(HERE, "index.html")
    if not os.path.isfile(index):
        print(f"index.html not found next to {__file__}", file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: serving beyond this machine. Audit logs can contain restricted content.", file=sys.stderr)

    Handler.sources = Sources(harness_dir, args.audit, args.state)
    Handler.index_path = index
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"Harness monitor (read-only) on http://{args.host}:{args.port}/  watching {harness_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
