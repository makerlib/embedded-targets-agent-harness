"""GitPort adapter backed by the ``git`` executable (subprocess)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.repository.ports import ChangeKind, PathChange

_STATUS_KINDS = {
    "A": ChangeKind.ADDED,
    "M": ChangeKind.MODIFIED,
    "D": ChangeKind.DELETED,
    "R": ChangeKind.RENAMED,
    "C": ChangeKind.COPIED,
    "T": ChangeKind.TYPE_CHANGED,
}

GIT_TIMEOUT_SECONDS = 60


def _git_executable() -> str:
    exe = shutil.which("git")
    if exe is None:
        raise HarnessError(
            ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE, "git executable not found on PATH"
        )
    return exe


def _run(
    args: list[str], cwd: Path, *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        result = subprocess.run(
            [_git_executable(), *args],
            cwd=cwd,
            env=full_env,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(
            ReasonCode.REPOSITORY_COMMAND_FAILED, f"git {args[0]} timed out",
            {"args": args},
        ) from exc
    if check and result.returncode != 0:
        raise HarnessError(
            ReasonCode.REPOSITORY_COMMAND_FAILED,
            f"git {args[0]} failed with exit code {result.returncode}",
            {"args": args, "exit_code": result.returncode},
            details=[result.stderr.decode("utf-8", "replace").strip()],
        )
    return result


def discover_root(start: Path) -> Path:
    """Return the repository root containing ``start``."""
    start = Path(start)
    if not start.exists():
        raise HarnessError(ReasonCode.REPOSITORY_NOT_FOUND, f"path does not exist: {start}")
    cwd = start if start.is_dir() else start.parent
    result = _run(["rev-parse", "--show-toplevel"], cwd, check=False)
    if result.returncode != 0:
        raise HarnessError(
            ReasonCode.REPOSITORY_NOT_FOUND, f"not inside a Git repository: {start}",
            {"start": str(start)},
        )
    return Path(result.stdout.decode("utf-8").strip()).resolve()


class GitCli:
    """Implements ``GitPort`` using the git command line."""

    def __init__(self, start: Path) -> None:
        self._root = discover_root(start)

    def root(self) -> Path:
        return self._root

    def current_branch(self) -> str | None:
        result = _run(["symbolic-ref", "--quiet", "--short", "HEAD"], self._root, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8").strip() or None

    def head_commit(self) -> str | None:
        result = _run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], self._root, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8").strip()

    def changed_paths(self, base: str | None) -> list[PathChange]:
        if base is None:
            base = self.head_commit()
        untracked = set(_split_z(
            _run(["ls-files", "-z", "--others", "--exclude-standard"], self._root).stdout
        ))
        # Stage the whole working tree (including untracked, non-ignored files)
        # into a throwaway index so rename/copy detection treats every change
        # uniformly. The repository's real index is never touched.
        with tempfile.TemporaryDirectory(prefix="et-harness-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if base is not None:
                _run(["read-tree", base], self._root, env=env)
            _run(["add", "--all", "--", "."], self._root, env=env)
            if base is None:
                listed = _run(["ls-files", "-z", "--cached"], self._root, env=env).stdout
                changes = [PathChange(p, ChangeKind.ADDED) for p in _split_z(listed)]
            else:
                out = _run(
                    ["diff", "--cached", "--name-status", "-z", "--no-ext-diff",
                     "--find-renames=100%", "--find-copies=100%", "--find-copies-harder",
                     base, "--"],
                    self._root, env=env,
                ).stdout
                changes = _parse_name_status(out)
        marked = [
            PathChange(c.path, c.kind, c.old_path, untracked=c.path in untracked)
            for c in changes
        ]
        return sorted(marked)

    def checkpoint_tree(self) -> str:
        """Git tree ID of the whole working tree, via a throwaway index."""
        base = self.head_commit()
        with tempfile.TemporaryDirectory(prefix="et-harness-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if base is not None:
                _run(["read-tree", base], self._root, env=env)
            _run(["add", "--all", "--", "."], self._root, env=env)
            tree = _run(["write-tree"], self._root, env=env).stdout.decode("utf-8").strip()
        return "git-tree:" + tree

    def is_ignored(self, path: str) -> bool:
        result = _run(["check-ignore", "-q", "--no-index", "--", path], self._root, check=False)
        return result.returncode == 0


def _split_z(data: bytes) -> list[str]:
    return [p for p in data.decode("utf-8", "surrogateescape").split("\0") if p]


def _parse_name_status(data: bytes) -> list[PathChange]:
    tokens = data.decode("utf-8", "surrogateescape").split("\0")
    changes: list[PathChange] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if not status:
            i += 1
            continue
        letter = status[0]
        kind = _STATUS_KINDS.get(letter)
        if kind is None:
            raise HarnessError(
                ReasonCode.REPOSITORY_COMMAND_FAILED, f"unexpected git status {status!r}"
            )
        if kind in (ChangeKind.RENAMED, ChangeKind.COPIED):
            old, new = tokens[i + 1], tokens[i + 2]
            changes.append(PathChange(new, kind, old_path=old))
            i += 3
        else:
            changes.append(PathChange(tokens[i + 1], kind))
            i += 2
    return changes
