"""Repository diff for the evidence bundle (§21 repository-diff.patch).

Adapter code: it calls git directly because ``GitPort`` has no patch method
yet. It diffs the baseline commit against the whole working tree, including
untracked non-ignored files, through a throwaway index, so the repository's
real index is never touched. Binary files appear as binary patches.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def working_tree_patch(repo_root: Path, base: str) -> str:
    with tempfile.TemporaryDirectory(prefix="et-harness-index-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))

        def git(*args: str) -> bytes:
            return subprocess.run(["git", *args], cwd=repo_root, env=env, capture_output=True,
                                  check=True, timeout=120).stdout

        git("read-tree", base)
        git("add", "--all", "--", ".")
        return git("diff", "--cached", "--binary", "--no-ext-diff", "--find-renames", base, "--").decode(
            "utf-8", "replace")
