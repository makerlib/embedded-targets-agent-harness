"""HK-004 AC-002: sequential executor (§9.15)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from et_harness.common.errors import HarnessError
from et_harness.common.schemas import validation_errors
from et_harness.tools.base import CancelToken
from et_harness.workflows.executor import RetryPolicy, SequentialExecutor, Step, StepOutcome


def recorder(log: list, status: str = "passed", evidence: list | None = None):
    def run(ctx):
        log.append(ctx.step_id)
        return StepOutcome(status, evidence=evidence or [f"{ctx.step_id}.txt"])
    return run


@pytest.fixture
def ex(tmp_path: Path) -> SequentialExecutor:
    return SequentialExecutor(tmp_path / "runs", workflow_id="wf-1", execution_profile="developer")


def statuses(result) -> dict[str, str]:
    return {s["id"]: s["status"] for s in result.steps}


def test_steps_run_in_order_and_record_evidence(ex: SequentialExecutor) -> None:
    log: list = []
    result = ex.run([Step("a", recorder(log)), Step("b", recorder(log)), Step("c", recorder(log))])
    assert log == ["a", "b", "c"] and result.status == "completed"
    assert all(s["evidence"] == [f"{s['id']}.txt"] for s in result.steps)
    record = json.loads(result.path.read_text())
    assert validation_errors(record, "verification-run") == []


def test_failure_stops_by_default(ex: SequentialExecutor) -> None:
    log: list = []
    result = ex.run([Step("a", recorder(log, "failed")), Step("b", recorder(log))])
    assert log == ["a"] and statuses(result) == {"a": "failed", "b": "not_run"}
    assert result.status == "failed"


def test_failure_can_continue(ex: SequentialExecutor) -> None:
    log: list = []
    result = ex.run([Step("a", recorder(log, "failed"), on_failure="continue"), Step("b", recorder(log))])
    assert log == ["a", "b"] and result.status == "failed"


def test_preconditions_block(ex: SequentialExecutor) -> None:
    log: list = []
    result = ex.run([Step("a", recorder(log), preconditions=[lambda c: (False, "no MATLAB")])])
    assert log == [] and statuses(result) == {"a": "blocked"}
    assert result.steps[0]["reason_codes"] == ["GATE_PRECONDITION_FAILED"]
    assert result.status == "blocked"


def test_postcondition_failure(ex: SequentialExecutor) -> None:
    result = ex.run([Step("a", recorder([]), postconditions=[lambda c: (False, "report missing")])])
    assert statuses(result) == {"a": "failed"} and "report missing" in result.steps[0]["message"]


def test_when_condition_skips(ex: SequentialExecutor) -> None:
    log: list = []
    result = ex.run([Step("a", recorder(log), when=lambda s: s.get("target") == "c2000"),
                     Step("b", recorder(log))], shared={"target": "rh850"})
    assert log == ["b"] and statuses(result)["a"] == "skipped_by_condition" and result.status == "completed"


def test_approval_pauses_run(ex: SequentialExecutor) -> None:
    log: list = []
    result = ex.run([Step("flash", recorder(log), requires_approval=True), Step("b", recorder(log))])
    assert log == [] and result.status == "paused_for_approval"
    assert result.reason_codes == ["APPROVAL_REQUIRED"]
    assert statuses(result) == {"flash": "paused_for_approval", "b": "not_run"}


def test_retry_policy(ex: SequentialExecutor) -> None:
    calls = []

    def flaky(ctx):
        calls.append(ctx.attempt)
        return StepOutcome("passed" if len(calls) == 3 else "failed")

    result = ex.run([Step("a", flaky, retry=RetryPolicy(maximum_retries=2))])
    assert calls == [1, 2, 3] and result.steps[0]["attempts"] == 3 and result.status == "completed"


def test_retry_limit(ex: SequentialExecutor) -> None:
    result = ex.run([Step("a", recorder([], "failed"), retry=RetryPolicy(maximum_retries=1))])
    assert result.steps[0]["attempts"] == 2 and result.status == "failed"


def test_cleanup_always_runs(ex: SequentialExecutor) -> None:
    cleaned = []
    cleanup = [Step("stop", lambda c: (cleaned.append(1), StepOutcome("passed"))[1])]
    ex.run([Step("a", recorder([], "failed"))], cleanup)
    assert cleaned == [1]


def test_cancellation_runs_cleanup(tmp_path: Path) -> None:
    token = CancelToken()
    ex = SequentialExecutor(tmp_path / "runs", cancel=token)
    cleaned = []

    def cancel_now(ctx):
        token.cancel()
        return StepOutcome("passed")

    result = ex.run([Step("a", cancel_now), Step("b", recorder([]))],
                    [Step("stop", lambda c: (cleaned.append(1), StepOutcome("passed"))[1])])
    assert result.status == "cancelled" and cleaned == [1]
    assert statuses(result)["b"] in ("cancelled", "not_run")


def test_keyboard_interrupt_runs_cleanup(ex: SequentialExecutor) -> None:
    cleaned = []

    def interrupt(ctx):
        raise KeyboardInterrupt

    result = ex.run([Step("a", interrupt)], [Step("stop", lambda c: (cleaned.append(1), StepOutcome("passed"))[1])])
    assert result.status == "cancelled" and cleaned == [1]


def test_resume_from_first_incomplete_step(ex: SequentialExecutor) -> None:
    log: list = []
    first = ex.run([Step("a", recorder(log)), Step("b", recorder(log), requires_approval=True),
                    Step("c", recorder(log))])
    assert first.status == "paused_for_approval"
    result = ex.run([Step("a", recorder(log)), Step("b", recorder(log), requires_approval=True,
                                                  approved=lambda: True), Step("c", recorder(log))],
                    run_id=first.run_id, resume=True)
    assert log == ["a", "b", "c"] and result.status == "completed"


def test_resume_rejects_other_steps_and_completed_runs(ex: SequentialExecutor) -> None:
    done = ex.run([Step("a", recorder([]))])
    with pytest.raises(HarnessError):
        ex.run([Step("a", recorder([]))], run_id=done.run_id, resume=True)
    paused = ex.run([Step("x", recorder([]), requires_approval=True)])
    with pytest.raises(HarnessError):
        ex.run([Step("y", recorder([]))], run_id=paused.run_id, resume=True)


def test_run_ids_are_sequential(ex: SequentialExecutor) -> None:
    assert ex.run([Step("a", recorder([]))]).run_id == "run-001"
    assert ex.run([Step("a", recorder([]))]).run_id == "run-002"


def test_duplicate_step_ids_rejected(ex: SequentialExecutor) -> None:
    with pytest.raises(HarnessError):
        ex.run([Step("a", recorder([])), Step("a", recorder([]))])
