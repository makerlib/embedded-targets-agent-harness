"""Artefact classification (§9.21).

Every repository path belongs to exactly one class. When several patterns
match, the first class in ``PRECEDENCE`` wins:

    harness > protected > qe_test > derived > model > source

``source`` is also the class of any path no other pattern matches: it follows
the normal scope rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from et_harness.common.paths import matches_any, normalise_path


class ArtefactClass(str, Enum):
    HARNESS = "harness"
    PROTECTED = "protected"
    QE_TEST = "qe_test"
    DERIVED = "derived"
    MODEL = "model"
    SOURCE = "source"


PRECEDENCE: tuple[ArtefactClass, ...] = (
    ArtefactClass.HARNESS,
    ArtefactClass.PROTECTED,
    ArtefactClass.QE_TEST,
    ArtefactClass.DERIVED,
    ArtefactClass.MODEL,
    ArtefactClass.SOURCE,
)

DEFAULT_PATTERNS: dict[ArtefactClass, tuple[str, ...]] = {
    ArtefactClass.HARNESS: (
        ".agent-harness/**",
        # Installed host projections (§7), owned by the kernel.
        "CLAUDE.md", "AGENTS.md", ".claude/settings.json", ".claude/settings.local.json",
        ".codex/**",
    ),
    ArtefactClass.DERIVED: (
        "**/slprj/**", "**/*.slxc", "**/*_ert_rtw/**", "**/*_grt_rtw/**", "**/codegen/**",
        "**/build/**", "**/__pycache__/**", "**/*.pyc",
        "**/*.mexa64", "**/*.mexw64", "**/*.mexmaci64", "**/*.mexmaca64",
    ),
    ArtefactClass.MODEL: ("**/*.slx", "**/*.mdl", "**/*.sldd"),
    ArtefactClass.SOURCE: (
        "**/*.m", "**/*.c", "**/*.h", "**/*.tlc", "**/*.py", "**/*.cpp", "**/*.hpp",
    ),
}


@dataclass(frozen=True)
class ArtefactClassifier:
    """Classifies paths using built-in defaults, project additions and contract scope."""

    harness: tuple[str, ...] = DEFAULT_PATTERNS[ArtefactClass.HARNESS]
    protected: tuple[str, ...] = ()
    qe_test: tuple[str, ...] = ()
    derived: tuple[str, ...] = DEFAULT_PATTERNS[ArtefactClass.DERIVED]
    model: tuple[str, ...] = DEFAULT_PATTERNS[ArtefactClass.MODEL]
    source: tuple[str, ...] = DEFAULT_PATTERNS[ArtefactClass.SOURCE]
    explanations: dict[str, str] = field(default_factory=dict, compare=False)

    @classmethod
    def build(
        cls,
        *,
        protected_paths: list[str] | tuple[str, ...] = (),
        qe_test_paths: list[str] | tuple[str, ...] = (),
        project_classes: dict[str, list[str]] | None = None,
    ) -> "ArtefactClassifier":
        extra = project_classes or {}

        def merged(klass: ArtefactClass) -> tuple[str, ...]:
            base = list(DEFAULT_PATTERNS.get(klass, ()))
            base.extend(p for p in extra.get(klass.value, []) if p not in base)
            return tuple(base)

        return cls(
            harness=merged(ArtefactClass.HARNESS),
            protected=tuple(protected_paths),
            qe_test=tuple(qe_test_paths),
            derived=merged(ArtefactClass.DERIVED),
            model=merged(ArtefactClass.MODEL),
            source=merged(ArtefactClass.SOURCE),
        )

    def patterns(self, klass: ArtefactClass) -> tuple[str, ...]:
        return getattr(self, klass.value)

    def classify(self, path: str) -> ArtefactClass:
        normalised = normalise_path(path)
        for klass in PRECEDENCE[:-1]:
            if matches_any(self.patterns(klass), normalised):
                return klass
        return ArtefactClass.SOURCE

    def to_dict(self) -> dict[str, list[str]]:
        return {k.value: list(self.patterns(k)) for k in PRECEDENCE}
