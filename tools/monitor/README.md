# Harness Monitor

A read-only HTML view of a harness run (spec v1.1.1 §9.22). Full guide:
[`docs/monitor.md`](../../docs/monitor.md).

## Files

| File | Purpose |
|---|---|
| `index.html` | The page. Opens in demo mode with the built-in sample when no server is present. |
| `harness_monitor.py` | Local read-only server. Serves `index.html` and streams the audit log and state file with Server-Sent Events. Standard library only. |
| `simulate_run.py` | Replays `sample-run.jsonl` into a scratch `.agent-harness` folder in real time, re-chaining hashes and writing a schema-valid state file. |
| `sample-run.jsonl` | One full-profile FEATURE-001 run in the §9.19 vocabulary: QE test design, two hook denials, a failed L3 gate, one rework cycle, advisory PASSED. |

## Try it

```bash
python simulate_run.py --harness-dir /tmp/demo/.agent-harness --speed 30
```

```bash
python harness_monitor.py --harness-dir /tmp/demo/.agent-harness
```

Then open <http://127.0.0.1:8765/>.

## Audit records

The page reads records in the format fixed by spec §9.19: the §9.19.1 envelope
(`sequence`, hash chain, `details`), the §9.19.2 event types and the §9.19.3
transition reasons. The machine-readable form is
[`schemas/audit-record.schema.json`](../../schemas/audit-record.schema.json),
generated from `et_harness.audit.vocabulary`. `sample-run.jsonl` validates
against it and its hash chain verifies.

For older logs the page also accepts `seq` in place of `sequence`. Unknown
event types are listed and marked as unregistered.

## Changing the sample

`index.html` embeds a copy of `sample-run.jsonl` for demo mode, and a test
checks the two are identical. After editing the sample, re-chain its hashes and
copy it into the `<script id="sample-run">` block.
