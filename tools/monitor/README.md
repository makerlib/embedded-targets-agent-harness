# Harness Monitor

A read-only HTML view of a harness run. It shows:

- the orchestration state machine, with the current state coloured by its owner
- one channel per persona (Kernel, Planner, QE, Development, Evaluator), high while that persona holds the modifying lease
- gate attempts, blocked actions and handoffs on those channels
- gates by verification level, enforcement tiers per host, and the audit log

It reads two files the kernel already writes: the audit log (spec §9.19) and the orchestration state file (spec §16). It never writes to the repository and has no approve or override controls.

## Files

| File | Purpose |
|---|---|
| `index.html` | The monitor page. Opens in demo mode with a built-in sample run when no server is present. |
| `harness_monitor.py` | Local read-only server. Serves `index.html` and streams the audit log and state file with Server-Sent Events. Python standard library; PyYAML only for YAML state files. |
| `simulate_run.py` | Writes `sample-run.jsonl` into a scratch `.agent-harness` folder in real time, for trying the monitor before the kernel exists. |
| `sample-run.jsonl` | One full-profile FEATURE-001 run: QE test design, a hook denial, a failed L3 gate, one rework cycle, advisory PASSED. |

## Try it

```bash
# Terminal 1: replay the sample into a scratch folder (about 2 minutes)
python simulate_run.py --harness-dir /tmp/demo/.agent-harness --speed 30

# Terminal 2: start the monitor
python harness_monitor.py --harness-dir /tmp/demo/.agent-harness
# open http://127.0.0.1:8765/
```

Against a real product repository:

```bash
python harness_monitor.py --repo /path/to/product-repo
```

## Three ways to use the page

| Mode | How | Use |
|---|---|---|
| Live | Served by `harness_monitor.py` | Watch a run as it happens. |
| File replay | **Open audit file** on the page | Step through a finished run, for example the `audit.jsonl` from a GitLab CI evidence bundle. |
| Demo | Open `index.html` directly | Built-in sample run. |

## Audit event vocabulary

Spec §9.19 fixes the audit fields but not the values of `event_type`. The monitor uses the vocabulary below and two extra fields. These are proposed additions to §9.19.

| `event_type` | Key fields |
|---|---|
| `workflow.created` | `details.title`, `details.profile`, `details.required_level`, `details.maximum_rework_cycles` |
| `state.transition` | `details.from`, `details.to`, `reason_codes` |
| `lease.acquired`, `lease.released` | `persona`, `agent_instance`, `details.host`, `details.lease_id` |
| `tool.decision` | `tool`, `action` (path or command), `result` = `ALLOW` or `DENY`, `reason_codes`, `details.via` |
| `tool.executed` | `tool`, `result`, `details.summary` |
| `gate.attempt` | `action` = gate ID, `result`, `details.level`, `details.attempt`, `details.purpose`, `details.tests`, `details.failure_signature` |
| `handoff.submitted`, `handoff.accepted`, `handoff.rejected` | `details.handoff_id`, `details.to_persona`, `details.summary` |
| `enforcement.probe` | `action` = control, `result` = tier, `details.host` |
| `evaluation.result`, `verdict.issued` | `result`, `details.authority`, `details.summary` |
| `blocker.raised`, `blocker.resolved` | `reason_codes` |
| `package.resolved` | `details.packages` |

Additional fields:

- `seq`: monotonically increasing integer per workflow, so readers can order and de-duplicate events.
- `details`: an object for event-specific data.

Unknown event types still appear in the log.

## Security

- Binds to `127.0.0.1` by default. Audit logs can contain paths and summaries from restricted work, so `--host` prints a warning when used to serve beyond the machine.
- Serves only `index.html` and the two JSON endpoints. It accepts GET and HEAD only.
- The page loads fonts from Google Fonts. Remove the `<link>` tags for air-gapped use; system fonts are already the fallback.
