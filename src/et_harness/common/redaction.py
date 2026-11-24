"""Secret-pattern redaction for captured output and audit records (§9.20)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

# Patterns with capture groups keep the captured prefix (for example the key
# name in ``token=abc``) and redact the remainder of the match.
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)\b\s*[=:]\s*)[^\s,;'\"]+",
    r"glpat-[A-Za-z0-9_\-]{20,}",          # GitLab access tokens
    r"gh[pousr]_[A-Za-z0-9]{30,}",         # GitHub tokens
    r"\bAKIA[0-9A-Z]{16}\b",               # AWS access key IDs
    r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}",  # bearer tokens
)


class Redactor:
    """Replaces configured secret patterns with ``[REDACTED]``."""

    def __init__(self, patterns: Iterable[str] = DEFAULT_PATTERNS) -> None:
        self._patterns = [re.compile(p) for p in patterns]

    @property
    def patterns(self) -> list[str]:
        return [p.pattern for p in self._patterns]

    def redact_text(self, text: str) -> str:
        for pattern in self._patterns:
            if pattern.groups:
                text = pattern.sub(
                    lambda m: "".join(g or "" for g in m.groups()) + REDACTED, text
                )
            else:
                text = pattern.sub(REDACTED, text)
        return text

    def redact(self, value: Any) -> Any:
        """Recursively redact strings in dicts, lists and tuples."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        return value
