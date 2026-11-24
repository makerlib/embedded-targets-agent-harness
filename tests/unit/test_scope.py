"""HK-002 AC-006 / AC-007: path globs, artefact classes and scope validation (§28.2)."""

from __future__ import annotations

import pytest

from et_harness.artefacts.classes import ArtefactClass, ArtefactClassifier
from et_harness.artefacts.scope import ScopeMode, Writer, validate_changes
from et_harness.common.errors import HarnessError
from et_harness.common.paths import glob_matches, normalise_path, validate_glob
from et_harness.common.reason_codes import ReasonCode
from et_harness.repository.ports import ChangeKind, PathChange

ALLOWED = ["src/blocks/adc/**", "tests/adc/**"]
CLASSIFIER = ArtefactClassifier.build(
    protected_paths=["src/blocks/adc/legacy/**", "tests/golden/**"],
    qe_test_paths=["qe-tests/adc/**"],
)


def change(path: str, kind: ChangeKind = ChangeKind.MODIFIED, old: str | None = None) -> PathChange:
    return PathChange(path, kind, old)


def check(changes, writer=Writer.DEVELOPMENT, mode=ScopeMode.WORK, allowed=ALLOWED):
    return validate_changes(changes, allowed_paths=allowed, classifier=CLASSIFIER, writer=writer, mode=mode)


def codes(report) -> list[str]:
    return [c.value for c in report.reason_codes()]


# --- globs and paths ------------------------------------------------------------------

@pytest.mark.parametrize("glob,path,expected", [
    ("src/**", "src/a.c", True),
    ("src/**", "src/a/b/c.c", True),
    ("src/**", "src", False),
    ("src/**/*.c", "src/a.c", True),
    ("src/**/*.c", "src/x/y/a.c", True),
    ("src/**/*.c", "src/x/a.h", False),
    ("src/*.c", "src/x/a.c", False),
    ("**/slprj/**", "slprj/a", True),
    ("**/slprj/**", "m/slprj/a/b", True),
    ("src/a?.c", "src/ab.c", True),
    ("src/[ab].c", "src/b.c", True),
    ("src/[!ab].c", "src/c.c", True),
    ("README.md", "README.md", True),
    ("README.md", "docs/README.md", False),
])
def test_glob_semantics(glob: str, path: str, expected: bool) -> None:
    assert glob_matches(glob, path) is expected


@pytest.mark.parametrize("raw,expected", [
    ("src/a.c", "src/a.c"), ("./src//a.c", "src/a.c"), ("src\\win\\a.c", "src/win/a.c"),
])
def test_normalise(raw: str, expected: str) -> None:
    assert normalise_path(raw) == expected


@pytest.mark.parametrize("raw", ["../etc/passwd", "src/../../x", "/etc/passwd", "C:/x", "C:\\x", "", ".", "a/\x00"])
def test_traversal_and_absolute_rejected(raw: str) -> None:
    with pytest.raises(HarnessError) as info:
        normalise_path(raw)
    assert info.value.code is ReasonCode.PATH_OUTSIDE_SCOPE


@pytest.mark.parametrize("glob", ["src/**", "**/*.slx", "a/b?.c", "x/[ab]/y"])
def test_valid_globs(glob: str) -> None:
    assert validate_glob(glob) is None


# --- AC-006 classification ---------------------------------------------------------------

@pytest.mark.parametrize("path,klass", [
    (".agent-harness/state/x.json", "harness"),
    ("CLAUDE.md", "harness"),
    (".claude/settings.json", "harness"),
    ("src/blocks/adc/legacy/old.tlc", "protected"),
    ("tests/golden/ref.mat", "protected"),
    ("qe-tests/adc/tA.m", "qe_test"),
    ("src/blocks/adc/slprj/cache.bin", "derived"),
    ("slprj/ert/x.c", "derived"),
    ("models/adc_ert_rtw/adc.c", "derived"),
    ("models/adc.slxc", "derived"),
    ("models/adc.slx", "model"),
    ("data/params.sldd", "model"),
    ("src/blocks/adc/adc.c", "source"),
    ("docs/notes.md", "source"),
])
def test_classification(path: str, klass: str) -> None:
    assert CLASSIFIER.classify(path).value == klass


def test_every_path_has_exactly_one_class() -> None:
    # Overlapping patterns resolve by precedence: protected beats model and source.
    clf = ArtefactClassifier.build(protected_paths=["models/**"])
    assert clf.classify("models/a.slx") is ArtefactClass.PROTECTED


def test_project_classes_extend_defaults() -> None:
    clf = ArtefactClassifier.build(project_classes={"derived": ["out/**"], "model": ["**/*.mldatx"]})
    assert clf.classify("out/a.o") is ArtefactClass.DERIVED
    assert clf.classify("x/a.mldatx") is ArtefactClass.MODEL
    assert clf.classify("x/slprj/a") is ArtefactClass.DERIVED  # defaults kept


# --- AC-007 scope validation ---------------------------------------------------------------

def test_allowed_path_passes() -> None:
    report = check([change("src/blocks/adc/adc.c"), change("tests/adc/t.m", ChangeKind.ADDED)])
    assert report.ok


def test_outside_scope_fails() -> None:
    assert codes(check([change("src/other/x.c")])) == ["PATH_OUTSIDE_SCOPE"]


def test_protected_wins_over_allowed() -> None:
    assert codes(check([change("src/blocks/adc/legacy/old.tlc")])) == ["PROTECTED_PATH_CHANGED"]


@pytest.mark.parametrize("kind", [ChangeKind.ADDED, ChangeKind.DELETED, ChangeKind.MODIFIED])
def test_unauthorised_add_delete_modify_detected(kind: ChangeKind) -> None:
    assert codes(check([change("lib/x.c", kind)])) == ["PATH_OUTSIDE_SCOPE"]


def test_rename_out_of_scope_detected_on_both_sides() -> None:
    report = check([change("lib/moved.c", ChangeKind.RENAMED, "src/blocks/adc/a.c")])
    assert codes(report) == ["PATH_OUTSIDE_SCOPE"]
    report = check([change("src/blocks/adc/new.c", ChangeKind.RENAMED, "src/blocks/adc/legacy/old.c")])
    assert codes(report) == ["PROTECTED_PATH_CHANGED"]
    assert report.findings[0].change_kind == "deleted (rename source)"


def test_copy_from_protected_into_scope_allowed_but_copy_out_detected() -> None:
    assert check([change("src/blocks/adc/copy.c", ChangeKind.COPIED, "src/blocks/adc/legacy/o.c")]).ok
    assert codes(check([change("tests/golden/copy.mat", ChangeKind.COPIED, "tests/adc/x.mat")])) == [
        "PROTECTED_PATH_CHANGED"]


def test_qe_tests_writable_by_qe_only() -> None:
    assert codes(check([change("qe-tests/adc/t.m")])) == ["QE_TEST_MODIFIED"]
    assert check([change("qe-tests/adc/t.m")], writer=Writer.QE).ok


def test_qe_cannot_write_implementation() -> None:
    assert codes(check([change("src/blocks/adc/adc.c")], writer=Writer.QE)) == ["PATH_OUTSIDE_SCOPE"]


def test_planner_cannot_write_product_files() -> None:
    assert codes(check([change("src/blocks/adc/adc.c")], writer=Writer.PLANNER)) == ["PATH_OUTSIDE_SCOPE"]


def test_harness_paths_kernel_only() -> None:
    assert codes(check([change(".agent-harness/state/orchestration-state.json")])) == ["PROTECTED_PATH_CHANGED"]
    assert check([change(".agent-harness/project.yaml")], writer=Writer.KERNEL).ok


def test_derived_ignored_during_work() -> None:
    report = check([change("slprj/ert/adc.c", ChangeKind.ADDED), change("x/build/out.o", ChangeKind.ADDED)])
    assert report.ok
    assert report.ignored_derived == ["slprj/ert/adc.c", "x/build/out.o"]


def test_derived_in_candidate_commit_rejected() -> None:
    report = check([change("slprj/ert/adc.c", ChangeKind.ADDED)], mode=ScopeMode.CANDIDATE)
    assert codes(report) == ["DERIVED_ARTEFACT_COMMITTED"]


def test_changed_models_listed() -> None:
    report = check([change("src/blocks/adc/adc.slx")])
    assert report.ok and report.changed_models == ["src/blocks/adc/adc.slx"]


def test_traversal_in_change_rejected() -> None:
    assert codes(check([change("src/blocks/adc/../../../etc/passwd")])) == ["PATH_OUTSIDE_SCOPE"]


def test_report_serialises() -> None:
    data = check([change("lib/x.c"), change("qe-tests/adc/t.m")]).to_dict()
    assert data["ok"] is False
    assert data["reason_codes"] == ["PATH_OUTSIDE_SCOPE", "QE_TEST_MODIFIED"]
