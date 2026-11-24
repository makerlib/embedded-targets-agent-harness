"""Scope validation of a set of path changes (§9.1, §9.21, §14.6, §14.7, UC-10).

Two modes:

* ``WORK``: changes in the working tree during a state (checkpoints,
  handoffs). Derived artefacts are ignored.
* ``CANDIDATE``: the diff of a candidate commit against the baseline. A
  derived artefact here is ``DERIVED_ARTEFACT_COMMITTED``.

For renames and copies both the destination and (for renames) the source are
checked, because a rename deletes its source path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from et_harness.artefacts.classes import ArtefactClass, ArtefactClassifier
from et_harness.common.errors import HarnessError
from et_harness.common.paths import matches_any, normalise_path
from et_harness.common.reason_codes import ReasonCode
from et_harness.repository.ports import ChangeKind, PathChange


class ScopeMode(str, Enum):
    WORK = "work"
    CANDIDATE = "candidate"


class Writer(str, Enum):
    """Who made the changes being checked (§13.2 write authority)."""

    DEVELOPMENT = "development"
    QE = "qe"
    PLANNER = "planner"
    KERNEL = "kernel"


@dataclass(frozen=True)
class ScopeFinding:
    path: str
    reason_code: ReasonCode
    change_kind: str
    artefact_class: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "reason_code": self.reason_code.value,
            "change_kind": self.change_kind,
            "artefact_class": self.artefact_class,
            "message": self.message,
        }


@dataclass
class ScopeReport:
    findings: list[ScopeFinding] = field(default_factory=list)
    changed_models: list[str] = field(default_factory=list)
    ignored_derived: list[str] = field(default_factory=list)
    checked_paths: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def reason_codes(self) -> list[ReasonCode]:
        seen: list[ReasonCode] = []
        for f in self.findings:
            if f.reason_code not in seen:
                seen.append(f.reason_code)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason_codes": [c.value for c in self.reason_codes()],
            "findings": [f.to_dict() for f in self.findings],
            "changed_models": self.changed_models,
            "ignored_derived": self.ignored_derived,
            "checked_paths": self.checked_paths,
        }


def _sides(change: PathChange) -> list[str]:
    if change.kind is ChangeKind.RENAMED and change.old_path:
        return [change.path, change.old_path]
    return [change.path]


def validate_changes(
    changes: list[PathChange],
    *,
    allowed_paths: list[str] | tuple[str, ...],
    classifier: ArtefactClassifier,
    writer: Writer = Writer.DEVELOPMENT,
    mode: ScopeMode = ScopeMode.WORK,
) -> ScopeReport:
    report = ScopeReport()
    for change in changes:
        for raw in _sides(change):
            kind = change.kind.value if raw == change.path else "deleted (rename source)"
            try:
                path = normalise_path(raw)
            except HarnessError as exc:
                report.findings.append(ScopeFinding(raw, ReasonCode.PATH_OUTSIDE_SCOPE, kind,
                                                    "invalid", exc.message))
                continue
            report.checked_paths.append(path)
            klass = classifier.classify(path)
            finding = _check(path, klass, kind, allowed_paths, writer, mode, report)
            if finding is not None:
                report.findings.append(finding)
    return report


def _check(
    path: str,
    klass: ArtefactClass,
    kind: str,
    allowed: list[str] | tuple[str, ...],
    writer: Writer,
    mode: ScopeMode,
    report: ScopeReport,
) -> ScopeFinding | None:
    def finding(code: ReasonCode, message: str) -> ScopeFinding:
        return ScopeFinding(path, code, kind, klass.value, message)

    if klass is ArtefactClass.HARNESS:
        if writer is Writer.KERNEL:
            return None
        return finding(ReasonCode.PROTECTED_PATH_CHANGED, "harness paths are writable by the kernel only")
    if klass is ArtefactClass.PROTECTED:
        return finding(ReasonCode.PROTECTED_PATH_CHANGED, "protected path changed without an approved scope change")
    if klass is ArtefactClass.QE_TEST:
        if writer is Writer.QE:
            return None
        return finding(ReasonCode.QE_TEST_MODIFIED, f"QE test paths are writable by QE only, not {writer.value}")
    if klass is ArtefactClass.DERIVED:
        if mode is ScopeMode.CANDIDATE:
            return finding(ReasonCode.DERIVED_ARTEFACT_COMMITTED, "derived artefacts must not be committed")
        report.ignored_derived.append(path)
        return None
    if writer is Writer.QE:
        return finding(ReasonCode.PATH_OUTSIDE_SCOPE, "QE may write only QE test paths")
    if writer is Writer.PLANNER:
        return finding(ReasonCode.PATH_OUTSIDE_SCOPE, "the Planner may not modify product files")
    if not matches_any(allowed, path):
        return finding(ReasonCode.PATH_OUTSIDE_SCOPE, "path is outside the contract's allowed_paths")
    if klass is ArtefactClass.MODEL and kind != "deleted (rename source)":
        report.changed_models.append(path)
    return None
