#!/usr/bin/env python3
"""Replay sample-run.jsonl into a scratch .agent-harness folder, in real time.

Use it to try the monitor before the kernel exists, or to test monitor changes.
It writes only inside the --harness-dir you give it:

    <harness-dir>/audit/audit.jsonl
    <harness-dir>/state/orchestration-state.json

    python simulate_run.py --harness-dir /tmp/demo/.agent-harness --speed 30
    python harness_monitor.py --harness-dir /tmp/demo/.agent-harness

--speed 30 plays the one-hour sample run in about two minutes. Timestamps are
shifted to the current time so the live view reads naturally, and the audit
hash chain (spec v1.1.1 §9.19.1) is recomputed so the output still verifies.
The state file follows the orchestration-state schema (§16).

It refuses to overwrite a non-empty audit log unless --force is given, so it
cannot clobber a real run by accident.

Standard library only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
GENESIS_HASH = "sha256:" + hashlib.sha256(b"et-harness-audit-genesis").hexdigest()


def canonical(value) -> bytes:
    """RFC 8785 form for records without floats and with ASCII keys."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def write_state(path: str, state: dict) -> None:
    """Atomic replace, as the kernel's state service does (spec §9.5)."""
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".orchestration-state.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def initial_state(first: dict) -> dict:
    return {
        "api_version": "embedded-targets.ai/v1",
        "kind": "OrchestrationState",
        "workflow": {
            "id": first["workflow_id"], "feature_id": first["feature_id"],
            "profile": (first.get("details") or {}).get("profile", "full"),
            "current_state": "CREATED", "previous_state": None, "resumable_state": None,
            "revision": 0, "superseded_by": None,
        },
        "ownership": {"persona": None, "agent_instance": None, "host": None,
                      "lease_id": None, "lease_status": None},
        "contract": {"path": f"features/active/{first['feature_id']}.yaml", "hash": None},
        "repository": {"baseline_commit": None, "candidate_commit": None},
        "rework": {"current_cycle": 0,
                   "maximum_cycles": (first.get("details") or {}).get("maximum_rework_cycles", 3)},
        "active_handoff": None,
        "blocker": None,
        "integrity": {"previous_state_hash": None, "audit_sequence": first["sequence"]},
        "history": [],
    }


def apply_event(state: dict, ev: dict) -> bool:
    """Fold one audit event into the state document. Returns True if it changed."""
    et, d = ev.get("event_type"), ev.get("details") or {}
    wf = state["workflow"]
    if et == "state.transition":
        wf["previous_state"], wf["current_state"] = wf["current_state"], d["to"]
        state["history"].append({"sequence": len(state["history"]) + 1, "from": d["from"],
                                 "to": d["to"], "reason": d["reason"], "revision": wf["revision"] + 1})
        if d["to"] == "REWORK_PLANNING":
            state["rework"]["current_cycle"] += 1
        return True
    if et == "lease.acquired":
        state["ownership"] = {"persona": ev.get("persona"), "agent_instance": ev.get("agent_instance"),
                              "host": d.get("host"), "lease_id": d.get("lease_id"), "lease_status": "active"}
        return True
    if et == "lease.released":
        state["ownership"]["lease_status"] = "released"
        return True
    if et == "handoff.submitted":
        state["active_handoff"] = d.get("handoff_id")
        return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness-dir", required=True, help="Scratch .agent-harness folder to write into")
    ap.add_argument("--sample", default=os.path.join(HERE, "sample-run.jsonl"))
    ap.add_argument("--speed", type=float, default=30.0, help="Time compression factor (default 30)")
    ap.add_argument("--max-gap", type=float, default=4.0, help="Longest real pause between events, seconds")
    ap.add_argument("--min-gap", type=float, default=0.05, help="Shortest real pause between events, seconds")
    ap.add_argument("--force", action="store_true", help="Overwrite a non-empty audit log in --harness-dir")
    ap.add_argument("--quiet", action="store_true", help="Do not print one line per event")
    args = ap.parse_args(argv)

    with open(args.sample, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    if not events:
        print("Sample file is empty", file=sys.stderr)
        return 2

    harness_dir = os.path.abspath(args.harness_dir)
    audit_dir = os.path.join(harness_dir, "audit")
    state_dir = os.path.join(harness_dir, "state")
    audit_path = os.path.join(audit_dir, "audit.jsonl")
    state_path = os.path.join(state_dir, "orchestration-state.json")
    if os.path.isfile(audit_path) and os.path.getsize(audit_path) > 0 and not args.force:
        print(f"Refusing to overwrite non-empty {audit_path}; pass --force for a scratch folder.",
              file=sys.stderr)
        return 3
    os.makedirs(audit_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)
    open(audit_path, "w", encoding="utf-8").close()

    t_first = datetime.fromisoformat(events[0]["timestamp"])
    start = datetime.now(timezone.utc).astimezone()
    state = initial_state(events[0])
    previous_entry = GENESIS_HASH
    previous_state_hash = None

    prev_orig = t_first
    for ev in events:
        orig = datetime.fromisoformat(ev["timestamp"])
        gap = (orig - prev_orig).total_seconds() / args.speed
        time.sleep(min(max(gap, args.min_gap), args.max_gap))
        prev_orig = orig

        record = {k: v for k, v in ev.items() if k != "entry_hash"}
        record["timestamp"] = (start + (orig - t_first) / args.speed).isoformat(timespec="seconds")
        record["previous_entry_hash"] = previous_entry
        record["entry_hash"] = digest(record)
        previous_entry = record["entry_hash"]

        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(canonical(record).decode("utf-8") + "\n")
            f.flush()

        before = copy.deepcopy(state)
        if apply_event(state, record) or ev is events[0]:
            if ev is not events[0]:
                state["workflow"]["revision"] += 1
                previous_state_hash = digest(before)
            state["integrity"] = {"previous_state_hash": previous_state_hash,
                                  "audit_sequence": record["sequence"]}
            write_state(state_path, state)
        if not args.quiet:
            print(f"{record['sequence']:>3}  {record['event_type']:<18} {record.get('result') or ''}")
    if not args.quiet:
        print("Run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
