#!/usr/bin/env python3
"""Replay sample-run.jsonl into a scratch .agent-harness folder, in real time.

Use it to try the monitor before the kernel exists, or to test monitor changes.
It writes only inside the --harness-dir you give it.

    python simulate_run.py --harness-dir /tmp/demo/.agent-harness --speed 30
    python harness_monitor.py --harness-dir /tmp/demo/.agent-harness

--speed 30 plays the one-hour sample run in about two minutes.
Timestamps are shifted to the current time so the live view reads naturally.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def write_state(path: str, state: dict) -> None:
    """Atomic replace, as the kernel's state service does (spec §9.5).

    The content is JSON, which is also valid YAML.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness-dir", required=True, help="Scratch .agent-harness folder to write into")
    ap.add_argument("--sample", default=os.path.join(HERE, "sample-run.jsonl"))
    ap.add_argument("--speed", type=float, default=30.0, help="Time compression factor (default 30)")
    ap.add_argument("--max-gap", type=float, default=4.0, help="Longest real pause between events, seconds")
    args = ap.parse_args(argv)

    with open(args.sample, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    if not events:
        print("Sample file is empty", file=sys.stderr)
        return 2

    audit_dir = os.path.join(args.harness_dir, "audit")
    state_dir = os.path.join(args.harness_dir, "state")
    os.makedirs(audit_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)
    audit_path = os.path.join(audit_dir, "audit.jsonl")
    state_path = os.path.join(state_dir, "orchestration-state.yaml")
    open(audit_path, "w").close()

    t_first = datetime.fromisoformat(events[0]["timestamp"])
    start = datetime.now(timezone.utc).astimezone()
    state = {
        "api_version": "embedded-targets.ai/v1", "kind": "OrchestrationState",
        "workflow": {"id": events[0]["workflow_id"], "feature_id": events[0]["feature_id"],
                     "profile": "full", "current_state": None, "previous_state": None, "revision": 0},
        "ownership": None, "rework": {"current_cycle": 0, "maximum_cycles": 3}, "history": [],
    }

    prev_orig = t_first
    for ev in events:
        orig = datetime.fromisoformat(ev["timestamp"])
        gap = (orig - prev_orig).total_seconds() / args.speed
        time.sleep(min(max(gap, 0.05), args.max_gap))
        prev_orig = orig
        ev = dict(ev)
        ev["timestamp"] = (start + (orig - t_first) / args.speed).isoformat()

        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")
            f.flush()

        et, d = ev.get("event_type"), ev.get("details") or {}
        changed = False
        if et in ("workflow.created", "state.transition"):
            wf = state["workflow"]
            wf["previous_state"], wf["current_state"] = wf["current_state"], d.get("to") or ev.get("state")
            wf["revision"] += 1
            state["history"].append({"sequence": wf["revision"], "from": d.get("from"), "to": wf["current_state"],
                                     "reason": (ev.get("reason_codes") or [None])[0]})
            if wf["current_state"] == "REWORK_PLANNING":
                state["rework"]["current_cycle"] += 1
            changed = True
        elif et == "lease.acquired":
            state["ownership"] = {"persona": ev.get("persona"), "agent_instance": ev.get("agent_instance"),
                                  "host": d.get("host"), "lease_id": d.get("lease_id"), "lease_status": "active"}
            changed = True
        elif et == "lease.released":
            state["ownership"] = None
            changed = True
        if changed:
            write_state(state_path, state)
        print(f"{ev['seq']:>3}  {et:<18} {ev.get('result') or ''}")
    print("Run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
