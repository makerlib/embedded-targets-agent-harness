# Embedded Targets Agent Harness

A governed runtime for agent-assisted Embedded Targets engineering: one locked
feature contract drives one bounded implementation, a kernel owns workflow
state, and executed gates with preserved evidence decide completion. The
authoritative build contract is the *Embedded Targets Harness Kernel
Specification v1.1.1* in `docs/specification/`.

## Status

Built incrementally in the HK feature sequence (spec §30). Only completed
features are claimed here.

| Feature | Scope | Status |
|---|---|---|
| HK-001 | Kernel skeleton: package, CLI framework, layered config, schemas, Git repository port, atomic state store with integrity chain, audit log, reason codes | Implemented (bootstrapping rule, advisory) |
| HK-002 | Feature contracts (RFC 8785 hashing, lint, local owner approval, lock), artefact classes, scope validation, tree-hash checkpoints; `harness feature create/lint/approve` | Implemented (bootstrapping rule, advisory) |
| HK-003 | Orchestration state machine (§15 table, ownership, revisioned and audited transitions, block/resume, tamper recovery); `harness workflow create/start/status/history/block/resume/cancel` | Implemented (bootstrapping rule, advisory) |
| HK-004 | Gate engine and evidence: gate definitions and registry, sequential executor, attempts/retries/timeouts/profiles, process and session tools, JUnit counts, baseline gates at start, evidence bundle; `harness gate run`, `verify`, `evidence collect/validate` | Implemented (advisory) |
| HK-015 | Read-only run monitor (`tools/monitor/`), audit record schema and event vocabulary | Implemented ahead of HK-003 at the feature owner's request (bootstrapping rule, advisory) |
| HK-005 … HK-014 | Contracts and scope, state machine, gates, evaluator, MATLAB slice, personas, handoffs, packages, adapters, policy, rework, CI | Not started |

Every `harness` command from spec §26 is registered. Commands whose feature is
not built report `NOT_IMPLEMENTED` and exit with code 3. They never report
success. `harness hook pre-tool` currently denies everything (fail closed).

## Quick start

Requires Python 3.11+ and Git.

```bash
python -m pip install -e '.[test]'
harness --version
harness config show-effective          # inside any Git repository
harness config show-effective --json
python -m pytest
```

## Feature contracts

```bash
harness feature create FEATURE-001 --title "Short title" --owner jane.doe
harness feature lint features/backlog/FEATURE-001.yaml
harness feature approve features/backlog/FEATURE-001.yaml --approver jane.doe
```

`approve` refuses agent identities, the drafter, and any request from inside
a kernel-launched agent session. The approval is bound to the contract's
content hash, so editing the contract afterwards invalidates it. A local
approval is unauthenticated and valid for advisory runs only (spec §9.13).
Until the gate registry exists (HK-004), declare package gate levels in
`.agent-harness/project.yaml` under `gate_levels`.

## Workflows

With one approved contract in `features/active/` and a committed work tree:

```bash
harness workflow create
harness workflow start
harness workflow status
```

`start` captures the baseline, locks the contract and scope, and moves to
PLANNING. Checks that later features provide (package resolution HK-009,
baseline gates HK-004, host guards HK-010) are listed as NOT_IMPLEMENTED, not
claimed. Kernel state lives in `.agent-harness/state/` (git-ignored). If it is
edited outside the kernel, the next command quarantines the edit, restores
the last-known-good copy and blocks with `STATE_TAMPER_DETECTED`; resume
needs `--maintainer-recovery`.

## Gates and evidence

Gates are YAML definitions (spec §20): built-ins in `builtins/gates/`
(`structural`, L0) and project gates in `.agent-harness/gates/` (this
repository defines `kernel-pytest`, L1). `workflow start` runs the contract's
required gates once with purpose *baseline* and records test counts.

```bash
harness gate run kernel-pytest          # one attempt
harness verify --execution-profile qe   # all required gates, in dependency order
harness evidence collect                # writes the §21 bundle and manifest
harness evidence validate               # re-hashes every file
```

Attempts are numbered, written once and made read-only; a further baseline or
verification attempt in the same rework cycle needs `--retry-reason` and is
limited by the gate's `maximum_retries`. `ci` and `hardware-lab` profiles run
only on the matching runner, and every verdict input produced so far is
*advisory*. The manifest lists evidence that later features produce (package
lock, plan, enforcement report, QE result, verdict) as `not_implemented` and
reports `complete: false` until they exist. `process.command` runs only the
argv written in the gate definition, without a shell.

## Run monitor

A read-only page for watching or replaying a run. See [`docs/monitor.md`](docs/monitor.md).

```bash
python tools/monitor/simulate_run.py --harness-dir /tmp/demo/.agent-harness --speed 30
```

```bash
python tools/monitor/harness_monitor.py --harness-dir /tmp/demo/.agent-harness
```

Then open <http://127.0.0.1:8765/>. The page tests need Node Playwright and
Chromium; without them they are skipped as `BLOCKED`.

## Configuration layers

Precedence, highest first (spec §9.3):

```text
kernel invariant > organisation > project > feature > persona > user-local
```

Lower layers may fill unset values and tighten controls. A lower layer that
tries to weaken a control is rejected with `CONFIG_WEAKENING_REJECTED`; the
higher value stays in force. Built-in defaults only fill keys no layer sets.

| Layer | Source |
|---|---|
| kernel invariant | `et_harness.config.controls` (cannot be changed) |
| organisation | `--org-policy`, `$ET_HARNESS_ORG_POLICY`, or `organisation_policy` in `project.yaml` |
| project | `<repo>/.agent-harness/project.yaml` |
| feature, persona | supplied by later HK features (contract, persona registry) |
| user-local | `--user-config`, `$ET_HARNESS_USER_CONFIG`, or `~/.config/et-harness/user.yaml` (`%APPDATA%` on Windows) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Rejected (invalid input, conflict, tamper, stale revision) |
| 2 | Usage error |
| 3 | `NOT_IMPLEMENTED` |
| 4 | Blocked (mandatory dependency or repository unavailable) |
| 5 | Internal error |

## Layout

```text
src/et_harness/
  cli/          click entrypoint, result envelope, exit codes
  config/       setting registry, layer resolution, file loading
  repository/   GitPort, Git CLI adapter, in-memory fake, RepositoryService
  sessions/     atomic state store with integrity chain
  audit/        hash-chained JSON Lines audit log
  common/       reason codes, errors, canonical hashing, atomic I/O, YAML, schemas, redaction
schemas/        JSON Schemas (draft 2020-12); audit-record is generated from audit/vocabulary.py
tools/monitor/  read-only run monitor (HK-015)
features/       feature contracts and plans (HK features)
docs/bootstrap-evidence/  evidence notes for bootstrapped HK features
```

## Known limitations (HK-001)

- Schemas are read from the repository's `schemas/` directory (or
  `$ET_HARNESS_SCHEMA_DIR`); they are not yet bundled into a wheel.
- Tamper detection is `DETECTED` tier only: the integrity chain detects
  edits to state and audit files, it cannot prevent them.
- The audit log assumes a single writing process; no inter-process lock yet.
- Rename and copy detection is exact-content only.
- The spec targets GitLab CI; this repository is currently hosted on GitHub.
  CI authority arrives in HK-013.
