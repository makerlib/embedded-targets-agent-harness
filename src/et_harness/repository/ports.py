"""Repository port (§9.1). Domain code depends only on this interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class ChangeKind(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"


@dataclass(frozen=True, order=True)
class PathChange:
    path: str                      # repository-relative, POSIX separators
    kind: ChangeKind
    old_path: str | None = None    # source path for renames and copies
    untracked: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "old_path": self.old_path,
            "untracked": self.untracked,
        }


@runtime_checkable
class GitPort(Protocol):
    """Read-only Git operations needed by the kernel."""

    def root(self) -> Path:
        """Absolute repository root."""

    def current_branch(self) -> str | None:
        """Branch name, or None for a detached HEAD."""

    def head_commit(self) -> str | None:
        """Full SHA of HEAD, or None if the repository has no commits."""

    def changed_paths(self, base: str | None) -> list[PathChange]:
        """Changes between ``base`` (None: HEAD) and the working tree.

        Includes untracked, non-ignored files as ``ADDED`` with
        ``untracked=True``. Detects exact renames and copies.
        """

    def is_ignored(self, path: str) -> bool:
        """True if ``path`` is ignored by the repository's ignore rules."""
