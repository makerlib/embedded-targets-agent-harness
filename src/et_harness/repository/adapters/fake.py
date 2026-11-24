"""In-memory GitPort for tests and fixtures.

Models commits as path->content snapshots and the working tree as a dict.
Rename and copy detection is exact-content, matching the CLI adapter's
``--find-renames=100% --find-copies=100%`` behaviour.
"""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path, PurePosixPath

from et_harness.repository.ports import ChangeKind, PathChange


class FakeGit:
    def __init__(self, root: Path = Path("/fake-repo"), branch: str | None = "main") -> None:
        self._root = Path(root)
        self._branch = branch
        self._commits: dict[str, dict[str, bytes]] = {}
        self._head: str | None = None
        self._tracked: set[str] = set()
        self.worktree: dict[str, bytes] = {}
        self.ignore_patterns: list[str] = []

    # --- test helpers ------------------------------------------------------
    def write(self, path: str, content: str | bytes) -> None:
        self.worktree[path] = content.encode() if isinstance(content, str) else content

    def delete(self, path: str) -> None:
        del self.worktree[path]

    def rename(self, old: str, new: str) -> None:
        self.worktree[new] = self.worktree.pop(old)

    def commit(self, message: str = "commit") -> str:
        snapshot = {p: c for p, c in self.worktree.items() if not self.is_ignored(p)}
        digest = hashlib.sha1(
            repr(sorted(snapshot.items())).encode() + message.encode() + str(len(self._commits)).encode()
        ).hexdigest()
        self._commits[digest] = snapshot
        self._head = digest
        self._tracked = set(snapshot)
        return digest

    def checkout_detached(self) -> None:
        self._branch = None

    # --- GitPort ----------------------------------------------------------
    def root(self) -> Path:
        return self._root

    def current_branch(self) -> str | None:
        return self._branch

    def head_commit(self) -> str | None:
        return self._head

    def is_ignored(self, path: str) -> bool:
        pure = PurePosixPath(path)
        for pattern in self.ignore_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in pure.parts[:-1] or path.startswith(pattern):
                    return True
            elif fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(pure.name, pattern):
                return True
        return False

    def changed_paths(self, base: str | None) -> list[PathChange]:
        base = base if base is not None else self._head
        before = dict(self._commits[base]) if base is not None else {}
        after = {p: c for p, c in self.worktree.items() if p in self._tracked or not self.is_ignored(p)}
        untracked = {p for p in after if p not in self._tracked}

        added = sorted(p for p in after if p not in before)
        deleted = sorted(p for p in before if p not in after)
        modified = sorted(p for p in after if p in before and after[p] != before[p])

        changes: list[PathChange] = [PathChange(p, ChangeKind.MODIFIED) for p in modified]
        remaining_deleted = list(deleted)
        for path in added:
            content = after[path]
            source = next((d for d in remaining_deleted if before[d] == content), None)
            if source is not None:
                remaining_deleted.remove(source)
                changes.append(PathChange(path, ChangeKind.RENAMED, source, path in untracked))
                continue
            copy_source = next(
                (p for p in sorted(before) if before[p] == content and p in after), None
            )
            if copy_source is not None:
                changes.append(PathChange(path, ChangeKind.COPIED, copy_source, path in untracked))
            else:
                changes.append(PathChange(path, ChangeKind.ADDED, None, path in untracked))
        changes.extend(PathChange(p, ChangeKind.DELETED) for p in remaining_deleted)
        return sorted(changes)
