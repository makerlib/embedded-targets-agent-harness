"""States, ownership (§13.2) and the transition table (§15).

The table is data: every legal edge, the §9.19.3 reason it must carry, and
any routing condition. ``check_transition`` is the only gate the engine uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode


class State(str, Enum):
    CREATED = "CREATED"
    INITIALISING = "INITIALISING"
    PLANNING = "PLANNING"
    PLAN_REVIEW = "PLAN_REVIEW"
    TEST_DESIGN = "TEST_DESIGN"
    DEVELOPING = "DEVELOPING"
    DEVELOPMENT_REVIEW = "DEVELOPMENT_REVIEW"
    VERIFYING = "VERIFYING"
    EVALUATING = "EVALUATING"
    REWORK_PLANNING = "REWORK_PLANNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


FINAL = frozenset({State.PASSED, State.FAILED})
TERMINAL = frozenset({State.PASSED, State.FAILED, State.BLOCKED, State.INCONCLUSIVE})
NON_TERMINAL = tuple(s for s in State if s not in TERMINAL)

OWNERS_FULL: dict[State, str] = {
    State.CREATED: "kernel", State.INITIALISING: "kernel", State.PLANNING: "planner",
    State.PLAN_REVIEW: "kernel", State.TEST_DESIGN: "qe", State.DEVELOPING: "development",
    State.DEVELOPMENT_REVIEW: "kernel", State.VERIFYING: "qe", State.EVALUATING: "evaluator",
    State.REWORK_PLANNING: "planner", State.PASSED: "kernel", State.FAILED: "kernel",
    State.BLOCKED: "kernel", State.INCONCLUSIVE: "kernel",
}
OWNERS_LITE: dict[State, str] = {
    **OWNERS_FULL,
    State.PLANNING: "kernel", State.VERIFYING: "kernel", State.REWORK_PLANNING: "kernel",
}
# TEST_DESIGN is not used in the lite profile (§13.2); it has no lite owner.
del OWNERS_LITE[State.TEST_DESIGN]


def owner(state: State, profile: str) -> str:
    table = OWNERS_LITE if profile == "lite" else OWNERS_FULL
    try:
        return table[state]
    except KeyError:
        raise HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                           f"{state.value} is not used in the {profile} profile") from None


@dataclass(frozen=True)
class Edge:
    source: State
    target: State
    reason: str
    event: str
    condition: str | None = None  # "test_design", "no_test_design", "full", "lite", "tamper"


S = State
EDGES: tuple[Edge, ...] = (
    Edge(S.CREATED, S.INITIALISING, "START_REQUESTED", "Start requested"),
    Edge(S.CREATED, S.BLOCKED, "BLOCKER_RAISED", "Required initial dependency unavailable"),
    Edge(S.INITIALISING, S.PLANNING, "INITIALISATION_PASSED", "Initialisation accepted"),
    Edge(S.INITIALISING, S.BLOCKED, "BLOCKER_RAISED", "Mandatory dependency unavailable"),
    Edge(S.INITIALISING, S.FAILED, "BASELINE_FAILURE_ESTABLISHED", "Baseline failure established"),
    Edge(S.PLANNING, S.PLAN_REVIEW, "PLAN_SUBMITTED", "Plan submitted"),
    Edge(S.PLANNING, S.BLOCKED, "BLOCKER_RAISED", "Planning dependency unavailable"),
    Edge(S.PLAN_REVIEW, S.TEST_DESIGN, "PLAN_ACCEPTED", "Plan accepted; test design", "test_design"),
    Edge(S.PLAN_REVIEW, S.DEVELOPING, "PLAN_ACCEPTED", "Plan accepted; lite or no test design", "no_test_design"),
    Edge(S.PLAN_REVIEW, S.PLANNING, "PLAN_REJECTED", "Plan rejected"),
    Edge(S.PLAN_REVIEW, S.BLOCKED, "BLOCKER_RAISED", "Approval unavailable"),
    Edge(S.TEST_DESIGN, S.DEVELOPING, "TESTS_ACCEPTED", "Acceptance tests accepted"),
    Edge(S.TEST_DESIGN, S.BLOCKED, "BLOCKER_RAISED", "QE environment unavailable"),
    Edge(S.DEVELOPING, S.DEVELOPMENT_REVIEW, "HANDOFF_SUBMITTED", "Handoff submitted"),
    Edge(S.DEVELOPING, S.BLOCKED, "BLOCKER_RAISED", "Required tool unavailable"),
    Edge(S.DEVELOPING, S.FAILED, "NON_RECOVERABLE_FAILURE", "Non-recoverable failure"),
    Edge(S.DEVELOPMENT_REVIEW, S.VERIFYING, "HANDOFF_ACCEPTED", "Handoff accepted"),
    Edge(S.DEVELOPMENT_REVIEW, S.DEVELOPING, "HANDOFF_REJECTED", "Handoff rejected"),
    Edge(S.DEVELOPMENT_REVIEW, S.BLOCKED, "BLOCKER_RAISED", "Approval unavailable or lite eligibility exceeded"),
    Edge(S.VERIFYING, S.EVALUATING, "QE_RESULT_SUBMITTED", "QE result submitted", "full"),
    Edge(S.VERIFYING, S.EVALUATING, "GATE_RUN_RECORDED", "Kernel gate run recorded", "lite"),
    Edge(S.VERIFYING, S.BLOCKED, "BLOCKER_RAISED", "Mandatory QE environment unavailable"),
    Edge(S.EVALUATING, S.PASSED, "COMPLETION_RULES_SATISFIED", "Completion rules satisfied"),
    Edge(S.EVALUATING, S.REWORK_PLANNING, "REWORK_ALLOWED", "Failure established and rework allowed"),
    Edge(S.EVALUATING, S.FAILED, "FAILURE_ESTABLISHED", "Failure established and no rework"),
    Edge(S.EVALUATING, S.BLOCKED, "BLOCKER_RAISED", "External dependency unavailable"),
    Edge(S.EVALUATING, S.INCONCLUSIVE, "EVIDENCE_INSUFFICIENT", "Evidence insufficient"),
    Edge(S.REWORK_PLANNING, S.DEVELOPING, "REWORK_ACCEPTED", "Rework accepted"),
    Edge(S.REWORK_PLANNING, S.BLOCKED, "BLOCKER_RAISED", "Rework dependency unavailable"),
    Edge(S.REWORK_PLANNING, S.FAILED, "FAILURE_ESTABLISHED", "Rework limit reached"),
    Edge(S.BLOCKED, S.FAILED, "FEATURE_ABANDONED", "Feature abandoned"),
    # Kernel-state tamper: any state except PASSED and FAILED (BLOCKED itself is
    # handled by replacing the blocker, without a transition).
    Edge(S.INCONCLUSIVE, S.BLOCKED, "BLOCKER_RAISED", "Kernel-state tamper detected", "tamper"),
)
# Resume edges go to the recorded resumable state; they are checked dynamically.
RESUME_REASONS = {S.BLOCKED: "BLOCKER_RESOLVED", S.INCONCLUSIVE: "ADDITIONAL_EVIDENCE_AUTHORISED"}


def edges_from(state: State) -> list[Edge]:
    return [e for e in EDGES if e.source is state]


def check_transition(
    source: State,
    target: State,
    reason: str,
    *,
    profile: str,
    qe_test_design: str,
    resumable: State | None = None,
    tamper: bool = False,
) -> Edge | None:
    """Return the matching edge or raise TRANSITION_NOT_ALLOWED.

    Resume edges (BLOCKED/INCONCLUSIVE to the recorded state) return None.
    """
    def refuse(message: str) -> HarnessError:
        return HarnessError(ReasonCode.TRANSITION_NOT_ALLOWED,
                            f"{source.value} -> {target.value} ({reason}): {message}",
                            {"from": source.value, "to": target.value, "reason": reason})

    if source in FINAL:
        raise refuse(f"{source.value} is final")
    if source in RESUME_REASONS and reason == RESUME_REASONS[source]:
        if resumable is None or target is not resumable:
            raise refuse(f"resume must return to the recorded resumable state "
                         f"({resumable.value if resumable else 'none recorded'})")
        return None
    test_design = profile == "full" and qe_test_design != "off"
    for edge in edges_from(source):
        if edge.target is not target or edge.reason != reason:
            continue
        if edge.condition == "test_design" and not test_design:
            raise refuse("test design is off or the profile is lite; go to DEVELOPING")
        if edge.condition == "no_test_design" and test_design:
            raise refuse("test design is enabled; go to TEST_DESIGN")
        if edge.condition in ("full", "lite") and edge.condition != profile:
            raise refuse(f"this edge applies to the {edge.condition} profile")
        if edge.condition == "tamper" and not tamper:
            raise refuse("only a kernel-state tamper may block an INCONCLUSIVE workflow")
        return edge
    raise refuse("not in the §15 transition table")
