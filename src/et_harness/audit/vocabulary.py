"""Audit event vocabulary (spec v1.1.1 §9.19.1–§9.19.3).

This module is the single source for ``schemas/audit-record.schema.json``.
``build_schema()`` renders the schema; a structural test checks that the
committed file matches it byte for byte (after JSON parsing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from et_harness.common.reason_codes import RESERVED_V1_2, ReasonCode

SCHEMA_ID = "https://embedded-targets.ai/schemas/audit-record.schema.json"

STATES = (
    "CREATED", "INITIALISING", "PLANNING", "PLAN_REVIEW", "TEST_DESIGN", "DEVELOPING",
    "DEVELOPMENT_REVIEW", "VERIFYING", "EVALUATING", "REWORK_PLANNING",
    "PASSED", "FAILED", "BLOCKED", "INCONCLUSIVE",
)
PERSONAS = ("kernel", "intake", "planner", "development", "qe", "evaluator")
HOSTS = ("claude-code", "codex")
LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
VERDICTS = ("PASSED", "FAILED", "BLOCKED", "INCONCLUSIVE")
TIERS = ("ENFORCED", "DETECTED", "ADVISORY")
EXECUTION_PROFILES = ("developer", "qe", "ci", "hardware-lab")
CONTROL_IDS = (
    "write_outside_scope", "protected_paths", "kernel_state",
    "shell_commands", "network", "class_d_e_actions",
)

# §9.19.3: transition reason -> allowed (from, to) pairs. "*" matches any state
# except PASSED and FAILED; "resumable" means any non-terminal state.
_NON_FINAL = tuple(s for s in STATES if s not in ("PASSED", "FAILED"))
_RESUMABLE = tuple(s for s in STATES if s not in VERDICTS)
TRANSITION_REASONS: dict[str, tuple[tuple[str, str], ...]] = {
    "START_REQUESTED": (("CREATED", "INITIALISING"),),
    "INITIALISATION_PASSED": (("INITIALISING", "PLANNING"),),
    "BASELINE_FAILURE_ESTABLISHED": (("INITIALISING", "FAILED"),),
    "PLAN_SUBMITTED": (("PLANNING", "PLAN_REVIEW"),),
    "PLAN_ACCEPTED": (("PLAN_REVIEW", "TEST_DESIGN"), ("PLAN_REVIEW", "DEVELOPING")),
    "PLAN_REJECTED": (("PLAN_REVIEW", "PLANNING"),),
    "TESTS_ACCEPTED": (("TEST_DESIGN", "DEVELOPING"),),
    "HANDOFF_SUBMITTED": (("DEVELOPING", "DEVELOPMENT_REVIEW"),),
    "HANDOFF_ACCEPTED": (("DEVELOPMENT_REVIEW", "VERIFYING"),),
    "HANDOFF_REJECTED": (("DEVELOPMENT_REVIEW", "DEVELOPING"),),
    "NON_RECOVERABLE_FAILURE": (("DEVELOPING", "FAILED"),),
    "QE_RESULT_SUBMITTED": (("VERIFYING", "EVALUATING"),),
    "GATE_RUN_RECORDED": (("VERIFYING", "EVALUATING"),),
    "COMPLETION_RULES_SATISFIED": (("EVALUATING", "PASSED"),),
    "REWORK_ALLOWED": (("EVALUATING", "REWORK_PLANNING"),),
    "FAILURE_ESTABLISHED": (("EVALUATING", "FAILED"), ("REWORK_PLANNING", "FAILED")),
    "EVIDENCE_INSUFFICIENT": (("EVALUATING", "INCONCLUSIVE"),),
    "REWORK_ACCEPTED": (("REWORK_PLANNING", "DEVELOPING"),),
    "BLOCKER_RAISED": tuple((s, "BLOCKED") for s in _NON_FINAL if s != "BLOCKED"),
    "BLOCKER_RESOLVED": tuple(("BLOCKED", s) for s in _RESUMABLE),
    "ADDITIONAL_EVIDENCE_AUTHORISED": tuple(("INCONCLUSIVE", s) for s in _RESUMABLE),
    "FEATURE_ABANDONED": (("BLOCKED", "FAILED"),),
}


def emittable_reason_codes() -> list[str]:
    return [c.value for c in ReasonCode if c not in RESERVED_V1_2]


@dataclass(frozen=True)
class EventSpec:
    """Required top-level fields and ``details`` keys for one event type."""

    fields: dict[str, Any] = field(default_factory=dict)     # field -> JSON Schema
    details: dict[str, Any] = field(default_factory=dict)    # required details keys
    optional_details: dict[str, Any] = field(default_factory=dict)


_STR = {"type": "string", "minLength": 1}
_NSTR = {"type": ["string", "null"]}
_INT = {"type": "integer", "minimum": 0}
_DIGEST = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
_NDIGEST = {"oneOf": [_DIGEST, {"type": "null"}]}
_TESTS = {
    "type": "object",
    "required": ["run", "passed", "failed"],
    "properties": {k: _INT for k in ("run", "passed", "failed", "skipped")},
    "additionalProperties": False,
}
_LEASE = EventSpec(
    fields={"persona": {"enum": list(PERSONAS)}, "agent_instance": _STR},
    details={"host": {"enum": list(HOSTS)}, "lease_id": _STR, "mode": {"enum": ["exclusive-modifying", "read-only"]}},
)
_HANDOFF_DONE = EventSpec(details={"handoff_id": _STR, "summary": {"type": "string"}})

EVENTS: dict[str, EventSpec] = {
    "workflow.created": EventSpec(
        details={"title": _STR, "profile": {"enum": ["full", "lite"]},
                 "required_level": {"enum": list(LEVELS)}, "maximum_rework_cycles": _INT},
        optional_details={"target": _STR},
    ),
    "state.write": EventSpec(
        details={"state_key": _STR, "state_hash": _DIGEST, "previous_state_hash": _NDIGEST,
                 "revision": _INT},
    ),
    "state.write_rolled_back": EventSpec(
        fields={"reason_codes": {"const": ["STATE_WRITE_INTERRUPTED"]}},
        details={"state_key": _STR, "state_hash": _DIGEST, "abandoned_state_hash": _DIGEST,
                 "abandoned_sequence": {"type": "integer", "minimum": 1}},
    ),
    "state.transition": EventSpec(
        fields={"state": {"enum": list(STATES)}},
        details={"from": {"enum": list(STATES)}, "to": {"enum": list(STATES)},
                 "reason": {"enum": list(TRANSITION_REASONS)}},
    ),
    "lease.acquired": _LEASE,
    "lease.released": _LEASE,
    "lease.revoked": _LEASE,
    "tool.decision": EventSpec(
        fields={"tool": _STR, "action": _STR, "result": {"enum": ["ALLOW", "DENY", "REQUIRE_APPROVAL"]}},
        details={"via": {"enum": ["broker", "pre-tool hook"]}},
    ),
    "tool.executed": EventSpec(
        fields={"tool": _STR, "result": _STR},
        details={"exit_code": {"type": ["integer", "null"]}, "duration_ms": _INT},
        optional_details={"summary": {"type": "string"}, "tests": _TESTS, "expected": _STR},
    ),
    "gate.attempt": EventSpec(
        fields={"action": _STR, "result": {"enum": list(VERDICTS)}},
        details={"level": {"enum": list(LEVELS)}, "attempt": {"type": "integer", "minimum": 1},
                 "execution_profile": {"enum": list(EXECUTION_PROFILES)}},
        optional_details={"purpose": _STR, "tests": _TESTS, "failure_signature": _STR,
                          "summary": {"type": "string"}},
    ),
    "handoff.submitted": EventSpec(
        details={"handoff_id": _STR, "to_persona": {"enum": list(PERSONAS)}, "summary": {"type": "string"}},
        optional_details={"cycle": {"type": "integer", "minimum": 1}},
    ),
    "handoff.accepted": _HANDOFF_DONE,
    "handoff.rejected": _HANDOFF_DONE,
    "checkpoint.recorded": EventSpec(
        details={"tree_hash": _DIGEST, "kind": {"enum": ["freeze", "safe"]}},
    ),
    "scope.violation": EventSpec(
        fields={"action": _STR, "reason_codes": {"type": "array", "minItems": 1}},
        details={"change_kind": {"enum": ["added", "modified", "deleted", "renamed", "copied", "type_changed"]}},
    ),
    "enforcement.probe": EventSpec(
        fields={"action": {"enum": list(CONTROL_IDS)}, "result": {"enum": list(TIERS)}},
        details={"host": {"enum": list(HOSTS)}},
    ),
    "package.resolved": EventSpec(
        details={"packages": {"type": "array", "items": {"type": "string", "pattern": "^[^@\\s]+@[^@\\s]+$"}},
                 "lock_hash": _DIGEST},
    ),
    "approval.recorded": EventSpec(
        fields={"approval_id": _STR, "result": {"enum": ["VALID", "REJECTED"]}},
        details={"source": {"enum": ["local", "gitlab"]}, "action": _STR},
    ),
    "blocker.raised": EventSpec(
        fields={"reason_codes": {"type": "array", "minItems": 1}},
        details={"resume_state": {"enum": list(STATES)}},
    ),
    "blocker.resolved": EventSpec(details={"resume_state": {"enum": list(STATES)}}),
    "evaluation.result": EventSpec(
        fields={"result": {"enum": list(VERDICTS)}},
        details={"failed_gates": {"type": "array", "items": _STR}, "rework_allowed": {"type": "boolean"},
                 "summary": {"type": "string"}},
    ),
    "verdict.issued": EventSpec(
        fields={"result": {"enum": list(VERDICTS)}},
        details={"authority": {"enum": ["advisory", "authoritative"]},
                 "execution_profile": {"enum": list(EXECUTION_PROFILES)},
                 "required_level": {"enum": list(LEVELS)},
                 "highest_level_passed": {"enum": list(LEVELS) + [None]},
                 "summary": {"type": "string"}},
    ),
}

ENVELOPE_FIELDS = (
    "sequence", "timestamp", "workflow_id", "session_id", "feature_id", "persona",
    "agent_instance", "state", "event_type", "action", "tool", "package", "result",
    "reason_codes", "evidence", "approval_id", "details", "previous_entry_hash", "entry_hash",
)


def build_schema() -> dict[str, Any]:
    """Render the audit-record JSON Schema (draft 2020-12)."""
    rules = []
    for event_type, spec in EVENTS.items():
        details_schema: dict[str, Any] = {
            "type": "object",
            "required": sorted(spec.details),
            "properties": {**spec.details, **spec.optional_details},
        }
        then: dict[str, Any] = {
            "required": sorted(spec.fields),
            "properties": {**spec.fields, "details": details_schema},
        }
        rules.append({
            "if": {"properties": {"event_type": {"const": event_type}}, "required": ["event_type"]},
            "then": then,
        })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Audit record",
        "description": (
            "One line of .agent-harness/audit/audit.jsonl (spec v1.1.1 §9.19). "
            "Generated from et_harness.audit.vocabulary; do not edit by hand."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": list(ENVELOPE_FIELDS),
        "properties": {
            "sequence": {"type": "integer", "minimum": 1},
            "timestamp": {"type": "string",
                          "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"},
            "workflow_id": _NSTR,
            "session_id": _NSTR,
            "feature_id": _NSTR,
            "persona": {"enum": list(PERSONAS) + [None]},
            "agent_instance": _NSTR,
            "state": {"enum": list(STATES) + [None]},
            "event_type": {"enum": list(EVENTS)},
            "action": _NSTR,
            "tool": _NSTR,
            "package": _NSTR,
            "result": _NSTR,
            "reason_codes": {"type": "array", "items": {"enum": emittable_reason_codes()},
                             "uniqueItems": True},
            "evidence": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "approval_id": _NSTR,
            "details": {"type": "object"},
            "previous_entry_hash": _DIGEST,
            "entry_hash": _DIGEST,
        },
        "allOf": rules,
    }


def transition_is_allowed(reason: str, from_state: str, to_state: str) -> bool:
    return (from_state, to_state) in TRANSITION_REASONS.get(reason, ())
