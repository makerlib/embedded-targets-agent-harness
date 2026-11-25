"""Gate definitions and the gate registry (§9.16, §20).

Sources, in order: built-in gates (``builtins/gates/*.yaml``) and the
project's ``.agent-harness/gates/*.yaml``. Package-supplied gates are added in
HK-009. Every definition is validated against ``gate.schema.json`` and against
the registered tools, preconditions and pass conditions; duplicate IDs and
dependency cycles are rejected.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document
from et_harness.common.yaml_io import load_yaml_file
from et_harness.tools.registry import ToolRegistry, default_registry

LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
PROFILES = ("developer", "qe", "ci", "hardware-lab")
PASS_CONDITIONS = ("action_exit_code_zero", "all_required_evidence_present",
                   "zero_failed_tests", "test_count_not_below_baseline")


def builtins_dir() -> Path:
    override = os.environ.get("ET_HARNESS_BUILTINS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "builtins"


# --- preconditions -----------------------------------------------------------------

@dataclass(frozen=True)
class PreconditionResult:
    id: str
    ok: bool
    message: str


def _precondition_parts(raw: Any) -> tuple[str, Any]:
    if isinstance(raw, str):
        return raw, None
    (name, arg), = raw.items()
    return name, arg


def evaluate_precondition(raw: Any, *, repo_root: Path, workflow: dict[str, Any] | None) -> PreconditionResult:
    name, arg = _precondition_parts(raw)
    label = name if arg is None else f"{name}:{arg}"
    if name == "contract_locked":
        locked = bool(workflow and (workflow.get("contract") or {}).get("hash"))
        lock_file = (repo_root / ".agent-harness" / "state" / "scope-lock.json").is_file()
        return PreconditionResult(label, locked or lock_file, "contract is locked" if (locked or lock_file)
                                  else "contract is not locked")
    if name == "command_available":
        found = shutil.which(str(arg)) is not None
        return PreconditionResult(label, found, f"{arg} {'found' if found else 'not found'} on PATH")
    if name == "file_exists":
        exists = (repo_root / str(arg)).exists()
        return PreconditionResult(label, exists, f"{arg} {'exists' if exists else 'is missing'}")
    if name == "env_set":
        ok = bool(os.environ.get(str(arg)))
        return PreconditionResult(label, ok, f"${arg} {'is set' if ok else 'is not set'}")
    return PreconditionResult(label, False, f"unknown precondition {name}")


KNOWN_PRECONDITIONS = {"contract_locked": False, "command_available": True, "file_exists": True, "env_set": True}


# --- definitions -------------------------------------------------------------------

@dataclass(frozen=True)
class GateDefinition:
    data: dict[str, Any]
    source: str

    @property
    def id(self) -> str:
        return self.data["gate"]["id"]

    @property
    def level(self) -> str:
        return self.data["gate"]["level"]

    @property
    def version(self) -> str:
        return self.data["gate"]["version"]

    @property
    def depends_on(self) -> list[str]:
        return list(self.data["gate"].get("depends_on") or [])

    @property
    def baseline(self) -> str:
        return self.data["gate"].get("baseline", "record")

    @property
    def actions(self) -> list[dict[str, Any]]:
        return self.data["actions"]

    @property
    def preconditions(self) -> list[Any]:
        return list(self.data.get("preconditions") or [])

    @property
    def required_evidence(self) -> list[str]:
        return list(self.data["evidence"]["required"])

    @property
    def pass_conditions(self) -> list[str]:
        return list(self.data["pass_conditions"])

    @property
    def maximum_retries(self) -> int:
        return int(self.data["failure_policy"]["maximum_retries"])

    def timeout_for(self, action: dict[str, Any], profile: str) -> int:
        value = action["timeout_seconds"]
        if isinstance(value, int):
            return value
        return int(value.get(profile) or value.get("default") or value.get("developer") or max(value.values()))


def _check_definition(defn: GateDefinition, tools: ToolRegistry) -> list[str]:
    errors = []
    ids = [a["id"] for a in defn.actions]
    if len(ids) != len(set(ids)):
        errors.append("action IDs must be unique")
    for action in defn.actions:
        try:
            tool = tools.get(action["tool"])
        except HarnessError as exc:
            errors.append(exc.message)
            continue
        mode = action.get("execution_mode", tool.describe().execution_mode)
        if mode != tool.describe().execution_mode:
            errors.append(f"action {action['id']}: tool {action['tool']} runs in {tool.describe().execution_mode} mode")
        errors.extend(f"action {action['id']}: {e}" for e in tool.validate_inputs(action.get("inputs") or {}))
    for raw in defn.preconditions + [a["when"] for a in defn.actions if "when" in a]:
        name, arg = _precondition_parts(raw)
        if name not in KNOWN_PRECONDITIONS:
            errors.append(f"unknown precondition {name!r}")
        elif KNOWN_PRECONDITIONS[name] != (arg is not None):
            errors.append(f"precondition {name!r} {'needs' if KNOWN_PRECONDITIONS[name] else 'takes no'} argument")
    if defn.id in defn.depends_on:
        errors.append("a gate cannot depend on itself")
    return errors


def load_definition(path: Path, tools: ToolRegistry | None = None) -> GateDefinition:
    data = load_yaml_file(path)
    validate_document(data, "gate", source=str(path), code=ReasonCode.CONFIG_INVALID)
    defn = GateDefinition(data, str(path))
    errors = _check_definition(defn, tools or default_registry())
    if errors:
        raise HarnessError(ReasonCode.CONFIG_INVALID, f"{path}: invalid gate definition",
                           {"gate": defn.id}, details=errors)
    return defn


class GateRegistry:
    def __init__(self, definitions: dict[str, GateDefinition], tools: ToolRegistry) -> None:
        self._defs = definitions
        self.tools = tools
        self._check_graph()

    @classmethod
    def load(cls, repo_root: Path | None = None, *, tools: ToolRegistry | None = None,
             extra_dirs: list[Path] = ()) -> "GateRegistry":
        tools = tools or default_registry()
        dirs = [builtins_dir() / "gates"]
        if repo_root is not None:
            dirs.append(Path(repo_root) / ".agent-harness" / "gates")
        dirs.extend(extra_dirs)
        defs: dict[str, GateDefinition] = {}
        for directory in dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.y*ml")):
                defn = load_definition(path, tools)
                if defn.id in defs:
                    raise HarnessError(ReasonCode.CONFIG_INVALID,
                                       f"gate {defn.id} defined twice: {defs[defn.id].source} and {path}")
                defs[defn.id] = defn
        return cls(defs, tools)

    def _check_graph(self) -> None:
        for defn in self._defs.values():
            for dep in defn.depends_on:
                if dep not in self._defs:
                    raise HarnessError(ReasonCode.CONFIG_INVALID, f"gate {defn.id} depends on unknown gate {dep}")
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(gate_id: str, chain: list[str]) -> None:
            if gate_id in done:
                return
            if gate_id in visiting:
                raise HarnessError(ReasonCode.CONFIG_INVALID,
                                   f"gate dependency cycle: {' -> '.join(chain + [gate_id])}")
            visiting.add(gate_id)
            for dep in self._defs[gate_id].depends_on:
                visit(dep, chain + [gate_id])
            visiting.discard(gate_id)
            done.add(gate_id)

        for gate_id in self._defs:
            visit(gate_id, [])

    def get(self, gate_id: str) -> GateDefinition:
        try:
            return self._defs[gate_id]
        except KeyError:
            raise HarnessError(ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE,
                               f"no definition for gate {gate_id!r}", {"gate": gate_id}) from None

    def has(self, gate_id: str) -> bool:
        return gate_id in self._defs

    def levels(self) -> dict[str, str]:
        return {g: d.level for g, d in self._defs.items()}

    def ids(self) -> list[str]:
        return sorted(self._defs)

    def order(self, gate_ids: list[str]) -> list[str]:
        """Dependencies first, then by level, then by the requested order."""
        wanted = list(dict.fromkeys(gate_ids))
        for gate_id in wanted:
            self.get(gate_id)
        result: list[str] = []
        remaining = sorted(wanted, key=lambda g: (LEVELS.index(self._defs[g].level), wanted.index(g)))
        while remaining:
            for gate_id in remaining:
                deps = [d for d in self._defs[gate_id].depends_on if d in wanted]
                if all(d in result for d in deps):
                    result.append(gate_id)
                    remaining.remove(gate_id)
                    break
        return result
