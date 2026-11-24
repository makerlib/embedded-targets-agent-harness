"""Factories for kernel-owned state documents."""

from __future__ import annotations

from typing import Any

from et_harness import API_VERSION


def new_orchestration_state(
    *,
    workflow_id: str,
    feature_id: str,
    contract_path: str,
    profile: str = "full",
    maximum_cycles: int = 3,
) -> dict[str, Any]:
    """Return a ``CREATED`` orchestration state (§16) ready for ``StateStore.create``."""
    return {
        "api_version": API_VERSION,
        "kind": "OrchestrationState",
        "workflow": {
            "id": workflow_id,
            "feature_id": feature_id,
            "profile": profile,
            "current_state": "CREATED",
            "previous_state": None,
            "resumable_state": None,
            "revision": 0,
            "superseded_by": None,
        },
        "ownership": {
            "persona": None,
            "agent_instance": None,
            "host": None,
            "lease_id": None,
            "lease_status": None,
        },
        "contract": {"path": contract_path, "hash": None},
        "repository": {"baseline_commit": None, "candidate_commit": None},
        "rework": {"current_cycle": 0, "maximum_cycles": maximum_cycles},
        "active_handoff": None,
        "blocker": None,
        "integrity": {"previous_state_hash": None, "audit_sequence": 1},
        "history": [],
    }
