"""Uniform command results, exit codes and rendering (§26)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import click

from et_harness.common.reason_codes import ReasonCode


class ExitCode(IntEnum):
    OK = 0
    REJECTED = 1          # operation ran and the result is negative (conflict, invalid input)
    USAGE = 2             # click usage error
    NOT_IMPLEMENTED = 3   # command exists but its HK feature is not built
    BLOCKED = 4           # mandatory dependency unavailable
    INTERNAL_ERROR = 5    # unexpected failure; never reported as success


_BLOCKING_CODES = {
    ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE,
    ReasonCode.REPOSITORY_NOT_FOUND,
}


def exit_code_for(code: ReasonCode) -> ExitCode:
    if code is ReasonCode.NOT_IMPLEMENTED:
        return ExitCode.NOT_IMPLEMENTED
    if code is ReasonCode.INTERNAL_ERROR:
        return ExitCode.INTERNAL_ERROR
    if code in _BLOCKING_CODES:
        return ExitCode.BLOCKED
    return ExitCode.REJECTED


@dataclass
class CommandResult:
    command: str
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[ReasonCode] = field(default_factory=list)
    exit_code: ExitCode = ExitCode.OK
    text_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "ok": self.ok,
            "exit_code": int(self.exit_code),
            "message": self.message,
            "reason_codes": [c.value for c in self.reason_codes],
            "data": self.data,
        }


def emit(result: CommandResult, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    status = "OK" if result.ok else "ERROR"
    click.echo(f"{status}: {result.message}", err=not result.ok)
    for code in result.reason_codes:
        click.echo(f"- {code.value}", err=not result.ok)
    for line in result.text_lines:
        click.echo(line)
