"""Repository service (§9.1): baseline capture, inspection and fingerprint.

Depends only on ``GitPort``. Scope validation, artefact classification and
tree-hash checkpoints are HK-002.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.hashing import digest
from et_harness.common.reason_codes import ReasonCode
from et_harness.repository.ports import GitPort, PathChange


@dataclass(frozen=True)
class RepositoryStatus:
    root: str
    branch: str | None
    head_commit: str | None
    changes: list[PathChange] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.changes

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "branch": self.branch,
            "head_commit": self.head_commit,
            "clean": self.clean,
            "changes": [c.to_dict() for c in self.changes],
        }


@dataclass(frozen=True)
class Baseline:
    commit: str
    branch: str | None
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {"commit": self.commit, "branch": self.branch, "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class Checkpoint:
    """A safe or freeze checkpoint (§9.1, §17)."""

    tree_hash: str
    head_commit: str | None
    kind: str = "safe"

    def to_dict(self) -> dict[str, Any]:
        return {"tree_hash": self.tree_hash, "head_commit": self.head_commit, "kind": self.kind}


class RepositoryService:
    def __init__(self, git: GitPort) -> None:
        self._git = git

    @property
    def git(self) -> GitPort:
        return self._git

    def status(self) -> RepositoryStatus:
        return RepositoryStatus(
            root=str(self._git.root()),
            branch=self._git.current_branch(),
            head_commit=self._git.head_commit(),
            changes=self._git.changed_paths(None),
        )

    def changes_since(self, base: str) -> list[PathChange]:
        return self._git.changed_paths(base)

    def fingerprint(self) -> str:
        """Digest of HEAD, branch and the exact set of working-tree changes.

        Detects drift in *which* paths changed and how; content-level tree
        hashing for checkpoints is HK-002.
        """
        status = self.status()
        return digest({
            "head_commit": status.head_commit,
            "branch": status.branch,
            "changes": [c.to_dict() for c in status.changes],
        })

    def checkpoint(self, kind: str = "safe") -> Checkpoint:
        if kind not in ("safe", "freeze"):
            raise HarnessError(ReasonCode.INTERNAL_ERROR, f"unknown checkpoint kind {kind!r}")
        return Checkpoint(self._git.checkpoint_tree(), self._git.head_commit(), kind)

    def verify_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Raise HANDOFF_DIFF_MISMATCH if the working tree changed since ``checkpoint``."""
        current = self._git.checkpoint_tree()
        if current != checkpoint.tree_hash:
            raise HarnessError(
                ReasonCode.HANDOFF_DIFF_MISMATCH,
                f"working tree changed since the {checkpoint.kind} checkpoint",
                {"checkpoint": checkpoint.tree_hash, "current": current},
            )

    def capture_baseline(self, *, require_clean: bool = True) -> Baseline:
        status = self.status()
        if status.head_commit is None:
            raise HarnessError(
                ReasonCode.BASELINE_FAILED, "repository has no commits; cannot capture a baseline"
            )
        if require_clean and not status.clean:
            raise HarnessError(
                ReasonCode.BASELINE_DIRTY,
                f"working tree has {len(status.changes)} uncommitted change(s)",
                {"changes": [c.to_dict() for c in status.changes[:50]]},
            )
        return Baseline(status.head_commit, status.branch, self.fingerprint())
