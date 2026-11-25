# Changelog

## 0.1.0 (unreleased)

### HK-005 Deterministic evaluator

- Pure rule function (§22) with categories integrity, failure, blocked, insufficient and fixed verdict precedence.
- Verification-level ladder, criterion status, approval rules (GitLab source required for human review, model review, L5/L6 sign-off, Class D/E), test-weakening, authority marking.
- `harness evaluate`: write-once evaluation records and final `verdict.yaml`, transitions including REWORK_PLANNING, `verdict.issued` / `evaluation.result` audit events; `--preview`.
- Re-evaluation of the bootstrap records (HK-001..HK-004, HK-015); verdict no longer counts against manifest completeness.

### HK-004 Gate engine and evidence

- Gate definitions (`gate.schema.json`), registry (built-in + project), dependency/level ordering, cycle detection; lint uses registry levels.
- Sequential executor: pre/postconditions, `when`, approval pauses, retry policy, cancellation with cleanup, persisted resumable runs.
- Gate runner: immutable numbered attempts, preconditions (never skipped), dependencies, pass conditions, timeouts with process-tree kill, retry reasons and limits, failure signatures, execution profiles.
- Tools: `process.command` (no shell, separate redacted and size-limited streams), `harness.schema-validator`, session-scoped tool supervision.
- JUnit test counts; baseline gates at `workflow start`; lite `verify` moves VERIFYING to EVALUATING.
- Evidence bundle and manifest (§21) with `harness evidence collect | validate`.

### HK-003 Orchestration state machine

- §13 states, §15 transition table (data), §13.2 ownership for both profiles.
- Workflow engine: expected revisions, append-only history with owner and §9.19.3 reason, `state.transition` / `blocker.*` audit events.
- Final-state enforcement, block, resume to the recorded state, INCONCLUSIVE resume, abandon.
- Tamper handling: last-known-good copies, quarantine, BLOCKED with `STATE_TAMPER_DETECTED`; contract mutation after lock blocks.
- `harness workflow create | start | status | history | block | resume | cancel`; scope-lock schema.
- `AuditLog.append` accepts only §9.19.2 event types.

### HK-002 Feature contract, scope and artefact classes

- Feature contract schema, loader (JSON data model only) and RFC 8785 hashing with ECMAScript number serialisation.
- Lint: verification methods, gate levels and required gates, scope globs, lite eligibility.
- `harness feature create | lint | approve`; local owner approval bound to the contract content hash, audited as `approval.recorded`.
- Scope lock, `CONTRACT_MUTATED_AFTER_LOCK` with field-level diff, active-feature discovery.
- Artefact classes (§9.21) and change-set scope validation; `GitPort.checkpoint_tree()` tree-hash checkpoints.

### HK-015 Read-only run monitor

- `tools/monitor/`: read-only live/replay/demo monitor (server, page, simulator, sample run).
- `schemas/audit-record.schema.json`, generated from `et_harness.audit.vocabulary` (spec 1.1.1 §9.19).
- Sample run converted to the 1.1.1 vocabulary with a verifiable hash chain.
- Server: fixed default paths, 405 for all non-GET/HEAD methods, reset on a replaced log, Windows-safe paths.
- Page: orders by `sequence`, warns on gaps, marks unregistered event types, shows transition reasons, labels verdict authority explicitly, states its coverage limits.
- Simulator: re-chains hashes, writes a schema-valid state file, refuses to overwrite a non-empty log.

### HK-001 Kernel skeleton

- Installable `et_harness` package with `harness` console entrypoint and single-source version.
- CLI framework: `--json` everywhere, defined exit codes, `NOT_IMPLEMENTED` stubs for all §26 commands.
- Layered configuration with precedence, no-weakening rule, source attribution and `harness config show-effective`.
- JSON Schemas: harness config, project config, orchestration state.
- `GitPort` with Git CLI adapter and in-memory fake sharing one contract-test suite.
- Atomic, revisioned state store with a hash integrity chain to the audit log and crash recovery.
- Append-only, hash-chained JSON Lines audit log with secret redaction.
- Stable reason-code registry (§27), including reserved v1.2 codes.
