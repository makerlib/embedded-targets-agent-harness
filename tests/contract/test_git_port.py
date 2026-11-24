"""AC-005: GitPort contract tests, run against the Git CLI adapter and the fake."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import subprocess

import pytest

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.repository.adapters.fake import FakeGit
from et_harness.repository.adapters.git_cli import GitCli, discover_root
from et_harness.repository.ports import ChangeKind, GitPort, PathChange
from et_harness.repository.service import RepositoryService


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Workspace(Protocol):
    port: GitPort

    def write(self, path: str, content: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def rename(self, old: str, new: str) -> None: ...
    def commit(self) -> str: ...
    def ignore(self, pattern: str) -> None: ...


class CliWorkspace:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.port = GitCli(repo)

    def write(self, path: str, content: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def delete(self, path: str) -> None:
        (self.repo / path).unlink()

    def rename(self, old: str, new: str) -> None:
        target = self.repo / new
        target.parent.mkdir(parents=True, exist_ok=True)
        (self.repo / old).rename(target)

    def commit(self) -> str:
        git(self.repo, "add", "--all")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "c")
        return git(self.repo, "rev-parse", "HEAD").strip()

    def ignore(self, pattern: str) -> None:
        with open(self.repo / ".gitignore", "a") as handle:
            handle.write(pattern + "\n")


class FakeWorkspace:
    def __init__(self) -> None:
        self.port = FakeGit()

    def write(self, path: str, content: str) -> None:
        self.port.write(path, content)

    def delete(self, path: str) -> None:
        self.port.delete(path)

    def rename(self, old: str, new: str) -> None:
        self.port.rename(old, new)

    def commit(self) -> str:
        return self.port.commit()

    def ignore(self, pattern: str) -> None:
        self.port.ignore_patterns.append(pattern)
        existing = self.port.worktree.get(".gitignore", b"").decode()
        self.port.write(".gitignore", existing + pattern + "\n")


@pytest.fixture(params=["git-cli", "fake"])
def ws(request: pytest.FixtureRequest, git_repo: Path) -> Workspace:
    return CliWorkspace(git_repo) if request.param == "git-cli" else FakeWorkspace()


def kinds(changes: list[PathChange]) -> dict[str, tuple[ChangeKind, str | None]]:
    return {c.path: (c.kind, c.old_path) for c in changes}


def seed(ws: Workspace) -> str:
    ws.write("src/a.c", "int a;\n")
    ws.write("src/b.c", "int b;\n")
    ws.write("tests/t.m", "% test\n")
    return ws.commit()


def test_port_protocol_conformance(ws: Workspace) -> None:
    assert isinstance(ws.port, GitPort)


def test_clean_tree_has_no_changes(ws: Workspace) -> None:
    head = seed(ws)
    assert ws.port.head_commit() == head
    assert ws.port.changed_paths(None) == []
    assert ws.port.current_branch() == "main"


def test_added_modified_deleted(ws: Workspace) -> None:
    seed(ws)
    ws.write("src/new.c", "int fresh;\n")
    ws.write("src/a.c", "int a = 2;\n")
    ws.delete("tests/t.m")
    got = kinds(ws.port.changed_paths(None))
    assert got == {
        "src/new.c": (ChangeKind.ADDED, None),
        "src/a.c": (ChangeKind.MODIFIED, None),
        "tests/t.m": (ChangeKind.DELETED, None),
    }
    assert next(c for c in ws.port.changed_paths(None) if c.path == "src/new.c").untracked


def test_rename_detected(ws: Workspace) -> None:
    seed(ws)
    ws.rename("src/b.c", "elsewhere/b.c")
    assert kinds(ws.port.changed_paths(None)) == {"elsewhere/b.c": (ChangeKind.RENAMED, "src/b.c")}


def test_copy_detected(ws: Workspace) -> None:
    seed(ws)
    ws.write("protected/a_copy.c", "int a;\n")
    assert kinds(ws.port.changed_paths(None)) == {"protected/a_copy.c": (ChangeKind.COPIED, "src/a.c")}


def test_ignored_untracked_files_excluded(ws: Workspace) -> None:
    ws.ignore("slprj/")
    seed(ws)
    ws.write("slprj/cache.bin", "derived\n")
    assert ws.port.changed_paths(None) == []
    assert ws.port.is_ignored("slprj/cache.bin")
    assert not ws.port.is_ignored("src/a.c")


def test_changes_relative_to_older_base(ws: Workspace) -> None:
    base = seed(ws)
    ws.write("src/c.c", "int c;\n")
    ws.commit()
    ws.write("src/d.c", "int d;\n")
    got = kinds(ws.port.changed_paths(base))
    assert got == {"src/c.c": (ChangeKind.ADDED, None), "src/d.c": (ChangeKind.ADDED, None)}


def test_service_baseline_requires_clean_tree(ws: Workspace) -> None:
    head = seed(ws)
    service = RepositoryService(ws.port)
    baseline = service.capture_baseline()
    assert baseline.commit == head
    assert baseline.fingerprint.startswith("sha256:")
    ws.write("src/a.c", "dirty\n")
    with pytest.raises(HarnessError) as info:
        service.capture_baseline()
    assert info.value.code is ReasonCode.BASELINE_DIRTY
    assert service.capture_baseline(require_clean=False).commit == head


def test_fingerprint_changes_with_worktree(ws: Workspace) -> None:
    seed(ws)
    service = RepositoryService(ws.port)
    before = service.fingerprint()
    assert service.fingerprint() == before
    ws.write("src/z.c", "int z;\n")
    assert service.fingerprint() != before


def test_baseline_fails_without_commits(ws: Workspace) -> None:
    with pytest.raises(HarnessError) as info:
        RepositoryService(ws.port).capture_baseline()
    assert info.value.code is ReasonCode.BASELINE_FAILED


# --- CLI adapter specifics -------------------------------------------------------

def test_discover_root_from_subdirectory(git_repo: Path) -> None:
    sub = git_repo / "a" / "b"
    sub.mkdir(parents=True)
    assert discover_root(sub) == git_repo.resolve()


def test_discover_root_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(HarnessError) as info:
        discover_root(outside)
    assert info.value.code is ReasonCode.REPOSITORY_NOT_FOUND


def test_detached_head_has_no_branch(git_repo: Path) -> None:
    ws = CliWorkspace(git_repo)
    head = seed(ws)
    git(git_repo, "checkout", "-q", "--detach", head)
    assert ws.port.current_branch() is None


def test_real_index_untouched(git_repo: Path) -> None:
    ws = CliWorkspace(git_repo)
    seed(ws)
    ws.write("src/untracked.c", "x\n")
    ws.port.changed_paths(None)
    status = git(git_repo, "status", "--porcelain")
    assert status.strip() == "?? src/untracked.c"


def test_paths_with_spaces_and_unicode(git_repo: Path) -> None:
    ws = CliWorkspace(git_repo)
    seed(ws)
    ws.write("docs/naïve file.md", "x\n")
    assert [c.path for c in ws.port.changed_paths(None)] == ["docs/naïve file.md"]


# --- HK-002 AC-008: tree-hash checkpoints ----------------------------------------------

def test_checkpoint_stable_without_changes(ws: Workspace) -> None:
    seed(ws)
    assert ws.port.checkpoint_tree() == ws.port.checkpoint_tree()


@pytest.mark.parametrize("mutation", ["modify", "add_untracked", "delete", "rename"])
def test_checkpoint_detects_any_change(ws: Workspace, mutation: str) -> None:
    seed(ws)
    service = RepositoryService(ws.port)
    checkpoint = service.checkpoint("freeze")
    if mutation == "modify":
        ws.write("src/a.c", "int a = 99;\n")
    elif mutation == "add_untracked":
        ws.write("notes/new.txt", "x\n")
    elif mutation == "delete":
        ws.delete("src/b.c")
    else:
        ws.rename("src/b.c", "src/c.c")
    with pytest.raises(HarnessError) as info:
        service.verify_checkpoint(checkpoint)
    assert info.value.code is ReasonCode.HANDOFF_DIFF_MISMATCH


def test_checkpoint_includes_untracked_edits_after_checkpoint(ws: Workspace) -> None:
    seed(ws)
    ws.write("scratch.txt", "one\n")
    service = RepositoryService(ws.port)
    checkpoint = service.checkpoint()
    service.verify_checkpoint(checkpoint)
    ws.write("scratch.txt", "two\n")
    with pytest.raises(HarnessError):
        service.verify_checkpoint(checkpoint)


def test_checkpoint_ignores_ignored_files(ws: Workspace) -> None:
    ws.ignore("slprj/")
    seed(ws)
    service = RepositoryService(ws.port)
    checkpoint = service.checkpoint()
    ws.write("slprj/cache.bin", "derived\n")
    service.verify_checkpoint(checkpoint)


def test_checkpoint_record(ws: Workspace) -> None:
    head = seed(ws)
    record = RepositoryService(ws.port).checkpoint("freeze").to_dict()
    assert record["head_commit"] == head and record["kind"] == "freeze" and record["tree_hash"]


def test_checkpoint_leaves_real_index_untouched(git_repo: Path) -> None:
    ws = CliWorkspace(git_repo)
    seed(ws)
    ws.write("src/untracked.c", "x\n")
    ws.port.checkpoint_tree()
    assert git(git_repo, "status", "--porcelain").strip() == "?? src/untracked.c"
