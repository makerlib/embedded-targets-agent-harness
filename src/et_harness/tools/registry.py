"""Registered tools and session supervision (§9.14 execution modes)."""

from __future__ import annotations

from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.tools.base import SessionToolProvider, ToolContext, ToolProvider


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, tool: Any) -> None:
        descriptor = tool.describe()
        if descriptor.id in self._tools:
            raise HarnessError(ReasonCode.TOOL_NOT_REGISTERED, f"tool {descriptor.id} registered twice")
        self._tools[descriptor.id] = tool

    def get(self, tool_id: str) -> Any:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise HarnessError(ReasonCode.TOOL_NOT_REGISTERED, f"tool {tool_id!r} is not registered") from None

    def ids(self) -> list[str]:
        return sorted(self._tools)

    def mode(self, tool_id: str) -> str:
        return self.get(tool_id).describe().execution_mode


def default_registry() -> ToolRegistry:
    from et_harness.tools.process import ProcessCommandTool
    from et_harness.tools.schema_validator import SchemaValidatorTool

    registry = ToolRegistry()
    registry.register(ProcessCommandTool())
    registry.register(SchemaValidatorTool())
    return registry


class SessionManager:
    """Keeps one warm session per session tool for the length of a run."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[SessionToolProvider, Any]] = {}
        self.started: list[str] = []
        self.stopped: list[str] = []

    def session_for(self, tool: SessionToolProvider, context: ToolContext) -> Any:
        tool_id = tool.describe().id
        if tool_id not in self._sessions:
            self._sessions[tool_id] = (tool, tool.start(context))
            self.started.append(tool_id)
        return self._sessions[tool_id][1]

    def stop_all(self) -> list[str]:
        errors = []
        for tool_id, (tool, session) in list(self._sessions.items()):
            try:
                tool.stop(session)
            except Exception as exc:  # noqa: BLE001 - recorded, cleanup continues
                errors.append(f"{tool_id}: {exc}")
            self.stopped.append(tool_id)
            del self._sessions[tool_id]
        return errors

    @property
    def active(self) -> list[str]:
        return sorted(self._sessions)


def execute_tool(tool: Any, inputs: dict[str, Any], context: ToolContext, sessions: SessionManager):
    if tool.describe().execution_mode == "session":
        if not isinstance(tool, SessionToolProvider):
            raise HarnessError(ReasonCode.TOOL_NOT_REGISTERED, f"{tool.describe().id} is not a session tool")
        return tool.run(sessions.session_for(tool, context), inputs, context)
    if not isinstance(tool, ToolProvider):
        raise HarnessError(ReasonCode.TOOL_NOT_REGISTERED, f"{tool.describe().id} is not a process tool")
    return tool.execute(inputs, context)
