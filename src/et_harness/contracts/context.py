"""Build lint and classification context from a product repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from et_harness.artefacts.classes import ArtefactClassifier
from et_harness.config.loader import load_configuration
from et_harness.contracts.lint import LintContext
from et_harness.contracts.model import FeatureContract


def project_context(
    project_root: Path,
    contract: FeatureContract | None = None,
    *,
    repository_paths: tuple[str, ...] = (),
) -> tuple[LintContext, dict[str, Any] | None]:
    loaded = load_configuration(Path(project_root))
    project = loaded.project or {}
    classifier = ArtefactClassifier.build(
        protected_paths=contract.protected_paths if contract else (),
        qe_test_paths=contract.qe_test_paths if contract else (),
        project_classes=project.get("artefact_classes"),
    )
    context = LintContext(
        gate_levels=dict(project.get("gate_levels") or {}),
        public_api_paths=tuple(project.get("public_api_paths") or ()),
        classifier=classifier,
        lite_max_level=loaded.effective.value("lite.max_verification_level"),
        repository_paths=repository_paths,
    )
    return context, loaded.project
