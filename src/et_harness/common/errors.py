"""Structured kernel errors carrying a stable reason code (§27)."""

from __future__ import annotations

from typing import Any

from et_harness.common.reason_codes import ReasonCode
from et_harness.common.redaction import Redactor

_REDACTOR = Redactor()


class HarnessError(Exception):
    """A kernel error with a reason code and redacted structured context."""

    def __init__(
        self,
        code: ReasonCode,
        message: str,
        context: dict[str, Any] | None = None,
        *,
        details: list[str] | None = None,
    ) -> None:
        self.code = code
        self.message = _REDACTOR.redact_text(message)
        self.context: dict[str, Any] = _REDACTOR.redact(dict(context or {}))
        self.details: list[str] = _REDACTOR.redact(list(details or []))
        super().__init__(f"{code.value}: {self.message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.code.value,
            "message": self.message,
            "context": self.context,
            "details": self.details,
        }
