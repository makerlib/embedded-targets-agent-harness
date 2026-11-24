# Changelog

## 0.1.0 (unreleased)

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
