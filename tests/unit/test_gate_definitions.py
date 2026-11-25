"""HK-004 AC-001: gate definitions and the registry (§20)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.contracts.context import project_context
from et_harness.gates.definitions import GateRegistry, evaluate_precondition, load_definition

REPO = Path(__file__).resolve().parents[2]


def gate(gate_id: str = "unit", **overrides) -> dict:
    data = {
        "api_version": "embedded-targets.ai/v1", "kind": "VerificationGate",
        "gate": {"id": gate_id, "version": "1.0.0", "name": gate_id, "level": "L1"},
        "actions": [{"id": "run", "tool": "process.command", "timeout_seconds": 10,
                     "inputs": {"argv": ["{python}", "-c", "pass"]}}],
        "evidence": {"required": []},
        "pass_conditions": ["action_exit_code_zero"],
        "failure_policy": {"maximum_retries": 1},
    }
    for key, value in overrides.items():
        if key in data["gate"] or key in ("depends_on", "baseline"):
            data["gate"][key] = value
        else:
            data[key] = value
    return data


def write(directory: Path, data: dict, name: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name or data['gate']['id']}.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_builtin_structural_and_repo_kernel_pytest_load() -> None:
    registry = GateRegistry.load(REPO)
    assert registry.levels() == {"structural": "L0", "kernel-pytest": "L1"}
    assert registry.get("structural").baseline == "must_pass"


def test_valid_definition_loads(tmp_path: Path) -> None:
    defn = load_definition(write(tmp_path, gate()))
    assert defn.id == "unit" and defn.level == "L1"


@pytest.mark.parametrize("change,message", [
    ({"actions": [{"id": "x", "tool": "shell.anything", "timeout_seconds": 5}]}, "not registered"),
    ({"preconditions": ["matlab_available"]}, "unknown precondition"),
    ({"preconditions": [{"file_exists": None}]}, None),
    ({"preconditions": ["command_available"]}, "needs argument"),
    ({"actions": [{"id": "x", "tool": "process.command", "timeout_seconds": 5, "inputs": {"argv": "ls"}}]}, "argv"),
    ({"actions": [{"id": "x", "tool": "process.command", "timeout_seconds": 5,
                   "inputs": {"argv": ["a"], "shell": True}}]}, "unknown inputs"),
    ({"actions": [{"id": "x", "tool": "process.command", "timeout_seconds": 5, "execution_mode": "session",
                   "inputs": {"argv": ["a"]}}]}, "runs in process mode"),
    ({"pass_conditions": ["looks_fine"]}, None),
    ({"level": "L9"}, None),
])
def test_invalid_definitions_rejected(tmp_path: Path, change: dict, message: str | None) -> None:
    with pytest.raises(HarnessError) as info:
        load_definition(write(tmp_path, gate(**change)))
    assert info.value.code is ReasonCode.CONFIG_INVALID
    if message:
        assert any(message in d for d in info.value.details), info.value.details


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    write(tmp_path / "a", gate("dup"))
    write(tmp_path / "b", gate("dup"))
    with pytest.raises(HarnessError) as info:
        GateRegistry.load(None, extra_dirs=[tmp_path / "a", tmp_path / "b"])
    assert "defined twice" in info.value.message


def test_dependency_cycle_rejected(tmp_path: Path) -> None:
    write(tmp_path, gate("a", depends_on=["b"]))
    write(tmp_path, gate("b", depends_on=["c"]))
    write(tmp_path, gate("c", depends_on=["a"]))
    with pytest.raises(HarnessError) as info:
        GateRegistry.load(None, extra_dirs=[tmp_path])
    assert "cycle" in info.value.message


def test_unknown_dependency_rejected(tmp_path: Path) -> None:
    write(tmp_path, gate("a", depends_on=["ghost"]))
    with pytest.raises(HarnessError):
        GateRegistry.load(None, extra_dirs=[tmp_path])


def test_order_dependencies_then_level(tmp_path: Path) -> None:
    write(tmp_path, gate("l3", level="L3"))
    write(tmp_path, gate("l1", level="L1", depends_on=["l3b"]))
    write(tmp_path, gate("l3b", level="L3"))
    registry = GateRegistry.load(None, extra_dirs=[tmp_path])
    assert registry.order(["l3", "l1", "structural", "l3b"]) == ["structural", "l3", "l3b", "l1"]


def test_unknown_gate_is_unavailable() -> None:
    with pytest.raises(HarnessError) as info:
        GateRegistry.load(None).get("missing")
    assert info.value.code is ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE


def test_timeout_per_profile(tmp_path: Path) -> None:
    defn = load_definition(write(tmp_path, gate(actions=[{
        "id": "x", "tool": "process.command", "timeout_seconds": {"developer": 10, "ci": 99},
        "inputs": {"argv": ["a"]}}])))
    action = defn.actions[0]
    assert defn.timeout_for(action, "ci") == 99
    assert defn.timeout_for(action, "developer") == 10
    assert defn.timeout_for(action, "qe") == 10  # falls back to developer


def test_preconditions(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    assert evaluate_precondition({"file_exists": "f.txt"}, repo_root=tmp_path, workflow=None).ok
    assert not evaluate_precondition({"file_exists": "g.txt"}, repo_root=tmp_path, workflow=None).ok
    assert evaluate_precondition({"command_available": "git"}, repo_root=tmp_path, workflow=None).ok
    assert not evaluate_precondition({"command_available": "no-such-tool-xyz"}, repo_root=tmp_path, workflow=None).ok
    assert not evaluate_precondition("contract_locked", repo_root=tmp_path, workflow=None).ok


def test_lint_levels_come_from_registry(tmp_path: Path) -> None:
    root = tmp_path / "product"
    (root / ".agent-harness").mkdir(parents=True)
    (root / ".agent-harness" / "project.yaml").write_text(
        "api_version: embedded-targets.ai/v1\nkind: ProjectConfig\nproject: {id: p, name: P}\n"
        "harness: {version: 0.1.0}\ngate_levels: {unit: L4, legacy: L2}\n")
    write(root / ".agent-harness" / "gates", gate("unit", level="L1"))
    context, _ = project_context(root)
    assert context.gate_levels["unit"] == "L1"     # definition wins
    assert context.gate_levels["legacy"] == "L2"   # project fallback without a definition
    assert context.gate_levels["structural"] == "L0"
