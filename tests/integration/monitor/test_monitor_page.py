"""HK-015 AC-007: the monitor page in a headless browser (Node Playwright + Chromium).

If Node or Playwright is unavailable these tests are skipped with a reason that
starts with BLOCKED. The HK-015 evidence requires zero skips, so a skip here
blocks completion rather than passing silently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from monitor_helpers import MONITOR_DIR, REPO, start_monitor, stop_monitor

CHECK = Path(__file__).with_name("page_check.cjs")
FIXTURES = REPO / "tests" / "fixtures" / "monitor"
INDEX_URL = (MONITOR_DIR / "index.html").as_uri()


@lru_cache(maxsize=1)
def _playwright_available() -> str | None:
    node = shutil.which("node")
    if node is None:
        return "BLOCKED: node is not installed"
    probe = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, text=True)
    if probe.returncode != 0:
        return "BLOCKED: Node Playwright is not installed"
    return None


def run_page(**config) -> dict:
    reason = _playwright_available()
    if reason:
        pytest.skip(reason)
    result = subprocess.run(["node", str(CHECK), json.dumps(config)], capture_output=True,
                            text=True, timeout=120, cwd=CHECK.parent)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["errors"] == [], data["errors"]
    return data


def non_font_requests(data: dict) -> list[dict]:
    return [r for r in data["requests"] if "fonts.g" not in r["url"]]


# --- demo mode -----------------------------------------------------------------

def test_demo_advisory_verdict_is_labelled_advisory() -> None:
    data = run_page(url=INDEX_URL, toEnd=True)
    assert data["mode"] == "Demo replay"
    assert data["verdictValue"] == "PASSED"
    assert data["verdictAuthority"] == "ADVISORY"
    assert "AUTHORITATIVE" not in data["verdictTile"]["v"]
    assert "Advisory only" in data["verdictTile"]["s"]


def test_demo_orders_by_sequence_without_gaps() -> None:
    data = run_page(url=INDEX_URL, toEnd=True)
    assert data["logSeqs"] == list(range(68, 0, -1))  # newest first
    assert data["gapWarnHidden"] is True
    assert data["unregistered"] == 0


def test_coverage_limit_is_stated() -> None:
    data = run_page(url=INDEX_URL)
    assert "only what reaches the audit log" in data["coverage"]
    assert "Agent conversations never appear" in data["coverage"]


def test_transition_reason_is_shown() -> None:
    data = run_page(url=INDEX_URL, toEnd=True)
    assert "(COMPLETION_RULES_SATISFIED)" in data["logText"]


def test_no_state_changing_controls() -> None:
    data = run_page(url=INDEX_URL)
    assert data["forms"] == 0
    words = ("approve", "override", "retry", "launch", "resume", "cancel", "block", "transition", "submit")
    filters = [c for c in data["controls"] if c["filter"]]
    assert {c["filter"] for c in filters} == {"all", "state", "lease", "tool", "gate", "handoff", "deny"}
    for control in data["controls"]:
        if control["filter"]:
            continue  # view filters for the event list; they change nothing
        assert not any(w in control["text"].lower() for w in words), control
        assert control["type"] != "submit", control
    # Only the replay transport, filters and the local file picker exist.
    ids = {c["id"] for c in data["controls"] if c["id"]}
    assert ids <= {"b-restart", "b-back", "b-play", "b-fwd", "speed", "scrub", "file"}


# --- file replay ---------------------------------------------------------------

def test_replay_lists_unknown_event_types_and_warns_on_gaps() -> None:
    data = run_page(url=INDEX_URL, file=str(FIXTURES / "unknown-and-gap.jsonl"))
    assert data["mode"] == "File replay"
    assert data["unregistered"] == 1
    assert "agent.chatted" in data["logText"]
    assert data["gapWarnHidden"] is False
    assert data["gapWarn"] == "1 gap(s) in audit sequence"
    expected = sorted([s for s in range(1, 22) if s != 5], reverse=True)
    assert data["logSeqs"] == expected  # out-of-order lines are sorted by sequence


def test_authoritative_verdict_shown_as_authoritative() -> None:
    data = run_page(url=INDEX_URL, file=str(FIXTURES / "authoritative-verdict.jsonl"))
    assert data["verdictAuthority"] == "AUTHORITATIVE"
    assert data["verdictTile"]["s"].startswith("Authoritative")


def test_page_makes_no_requests_in_file_modes() -> None:
    data = run_page(url=INDEX_URL, file=str(FIXTURES / "unknown-and-gap.jsonl"))
    for request in non_font_requests(data):
        assert request["method"] == "GET"
        assert request["url"].startswith("file://") or request["url"].endswith("api/snapshot")


# --- live mode -----------------------------------------------------------------

def test_live_mode_uses_get_only(tmp_path: Path) -> None:
    target = tmp_path / ".agent-harness"
    (target / "audit").mkdir(parents=True)
    shutil.copy(MONITOR_DIR / "sample-run.jsonl", target / "audit" / "audit.jsonl")
    mon, _ = start_monitor(target)
    try:
        data = run_page(url=f"http://127.0.0.1:{mon.port}/", waitLive=68)
    finally:
        stop_monitor(mon)
    assert data["mode"] == "Live"
    assert data["logSeqs"][0] == 68
    assert data["verdictAuthority"] == "ADVISORY"
    paths = {r["url"].split(str(mon.port), 1)[1].split("?")[0] for r in non_font_requests(data)}
    assert paths <= {"/", "/api/snapshot", "/api/stream"}
    assert {r["method"] for r in non_font_requests(data)} == {"GET"}
