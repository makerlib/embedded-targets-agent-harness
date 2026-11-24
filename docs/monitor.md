# Run monitor

A read-only web page for watching a harness workflow as it runs, or replaying
a finished one (spec v1.1.1 §9.22). It is an observer, not a control surface:
it cannot approve, override, retry, launch or change state.

It shows:

- the state machine, with the current state coloured by its owner
- one lane per persona, raised while that persona holds the modifying lease
- gate attempts, tool decisions (including hook denials), handoffs and verdicts
- gates by verification level, and enforcement tiers per host
- the audit log, with reason codes and transition reasons

## Requirements

- Python 3.11 or later. The server and simulator use only the standard library.
  PyYAML is needed only if your state file is YAML; the kernel writes JSON.
- A current browser.
- Nothing else. You do not need to install the `et_harness` package to run
  the monitor.

## Three modes

| Mode | How to start it | Use it for |
|---|---|---|
| Live | `python tools/monitor/harness_monitor.py --repo <product-repo>` | Watching a run while it happens |
| Replay | Open the page, then **Open audit file** | Stepping through a finished run, including an `audit.jsonl` from a CI evidence bundle |
| Demo | Open `tools/monitor/index.html` directly in a browser | Seeing the built-in sample run |

In replay and demo modes the page reads only the file you choose. It makes no
network requests apart from optional fonts (see *Air-gapped use*).

## Try it from a clean clone

Run these from the repository root. Terminal 1 replays the sample run into a
scratch folder, taking about two minutes:

```bash
python tools/monitor/simulate_run.py --harness-dir /tmp/demo/.agent-harness --speed 30
```

Terminal 2 starts the monitor:

```bash
python tools/monitor/harness_monitor.py --harness-dir /tmp/demo/.agent-harness
```

Then open <http://127.0.0.1:8765/>.

The simulator writes only `audit/audit.jsonl` and `state/orchestration-state.json`
inside `--harness-dir`. It refuses to overwrite a non-empty audit log unless you
pass `--force`, so it cannot overwrite a real run by accident.

## Watching a real run

```bash
python tools/monitor/harness_monitor.py --repo /path/to/product-repo
```

The server reads two files (spec §7):

| File | Default path |
|---|---|
| Audit log (§9.19) | `<repo>/.agent-harness/audit/audit.jsonl` |
| Orchestration state (§16) | `<repo>/.agent-harness/state/orchestration-state.json` |

Override either with `--audit` or `--state`, or point at another folder with
`--harness-dir`. Use `--port` to change the port from 8765.

The page tracks the audit log as the kernel appends to it:

- A line still being written is held until it is complete.
- If the log is truncated or replaced, the page starts again from the top.
- A malformed line is counted and reported in the header. It is never silently
  dropped.
- If the state file and the audit log disagree about the current state, the
  State tile says so.

## What the page cannot show

The monitor shows only what reaches the audit log:

- **Agent conversations never appear.** The page sees decisions and results,
  not reasoning.
- **Agent actions appear only when the kernel sees them**, either through the
  host's pre-tool hook (where the tier is `ENFORCED`) or when a checkpoint
  catches a change (`scope.violation`). Where a host only *detects* violations,
  edits made between checkpoints show up only at the next checkpoint or handoff.
- **It is not evidence.** A verdict is always shown with its authority. An
  advisory `PASSED` from a developer machine is labelled ADVISORY and is not
  sufficient to merge; only an authoritative verdict from CI is.
- **It does not verify the hash chain.** It warns about gaps in the `sequence`
  numbers, but `harness doctor` and the evaluator are the integrity authority.
- **Unrecognised event types** are listed and marked "unregistered event type".
  The kernel itself emits only the event types in spec §9.19.2.

## Security defaults

- The server binds to `127.0.0.1`. Audit records can contain paths and
  summaries from restricted work, so it prints a warning if you use `--host` to
  serve beyond your machine. Share a recording or the `audit.jsonl` file
  instead of opening the port.
- It accepts only `GET` and `HEAD`. Every other method returns 405.
- It serves exactly three routes: `/` (also `/index.html`), `/api/snapshot`
  and `/api/stream`. Every other path returns 404, so it cannot serve your
  source, evidence or state files.
- It never writes to the repository, the harness state, the audit log or the
  evidence.
- Secrets are redacted by the kernel before they reach the audit log (§9.20);
  the monitor adds no redaction of its own.

## Air-gapped use

The page loads three fonts from Google Fonts. Without network access it falls
back to system fonts automatically, so it still works. To stop the requests
entirely, delete the three `<link>` lines in the `<head>` of
`tools/monitor/index.html`: two `preconnect` links and one stylesheet link to
`fonts.googleapis.com`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Page says **Demo replay** when you expected **Live** | You opened the file directly. Open the server URL instead. |
| **Waiting for an audit log** | No `audit/audit.jsonl` under the harness folder yet. Check `--repo` or `--harness-dir`. |
| **Reconnecting** | The server stopped. Restart it; the page resumes from where it was. |
| **N gap(s) in audit sequence** | Records are missing from the log. Run `harness doctor` once it exists (HK-010); the kernel treats this as `STATE_TAMPER_DETECTED`. |
| `index.html not found` | Run the server from its own folder layout; `index.html` must sit next to `harness_monitor.py`. |

## Tests

```bash
python -m pytest tests/unit/test_audit_record_schema.py tests/integration/monitor
```

The page tests drive headless Chromium through Node Playwright. Without Node
and Playwright they are skipped with a reason starting `BLOCKED`, and HK-015
cannot be claimed complete.
