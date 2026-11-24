# Changelog

## 0.1.0 (unreleased)

### HK-001 Kernel skeleton

- Installable `et_harness` package with `harness` console entrypoint and single-source version.
- CLI framework: `--json` everywhere, defined exit codes, `NOT_IMPLEMENTED` stubs for all §26 commands.
- Layered configuration with precedence, no-weakening rule, source attribution and `harness config show-effective`.
- JSON Schemas: harness config, project config, orchestration state.
- `GitPort` with Git CLI adapter and in-memory fake sharing one contract-test suite.
- Atomic, revisioned state store with a hash integrity chain to the audit log and crash recovery.
- Append-only, hash-chained JSON Lines audit log with secret redaction.
- Stable reason-code registry (§27), including reserved v1.2 codes.
