"""Tool interfaces used by gate actions (§11.2, §9.14).

The full tool broker (persona, scope, approval checks) is HK-011. In HK-004,
gate actions call registered tools through these interfaces only; an
unregistered tool ID is rejected when the gate definition is loaded.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class CancelToken:
    """Cooperative cancellation shared by the executor and running tools."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class ToolContext:
    repo_root: Path
    attempt_dir: Path
    evidence_dir: Path
    timeout_seconds: float
    execution_profile: str
    output_limit_bytes: int = 1_000_000
    redaction_patterns: tuple[str, ...] = ()
    cancel: CancelToken = field(default_factory=CancelToken)
    workflow: dict[str, Any] | None = None


@dataclass
class ToolResult:
    exit_code: int | None
    duration_ms: int
    timed_out: bool = False
    cancelled: bool = False
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    evidence: dict[str, Path] = field(default_factory=dict)  # evidence id -> file
    test_counts: dict[str, int] | None = None
    failing_tests: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class ToolDescriptor:
    id: str
    version: str
    permission_class: str  # A..E (§25)
    execution_mode: str  # process | session
    description: str = ""


@runtime_checkable
class ToolProvider(Protocol):
    def describe(self) -> ToolDescriptor: ...

    def validate_inputs(self, inputs: dict[str, Any]) -> list[str]: ...

    def execute(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult: ...


@runtime_checkable
class SessionToolProvider(Protocol):
    """A tool that reuses a warm, kernel-supervised session (§9.14, §11.9)."""

    def describe(self) -> ToolDescriptor: ...

    def validate_inputs(self, inputs: dict[str, Any]) -> list[str]: ...

    def start(self, context: ToolContext) -> Any: ...

    def run(self, session: Any, inputs: dict[str, Any], context: ToolContext) -> ToolResult: ...

    def stop(self, session: Any) -> None: ...
