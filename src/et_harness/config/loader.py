"""Discover, validate and load configuration layer files (§9.3)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document
from et_harness.common.yaml_io import load_yaml_file
from et_harness.config.layers import ConfigLayer, EffectiveConfig, LayerName, resolve

PROJECT_CONFIG_RELATIVE = Path(".agent-harness") / "project.yaml"
ENV_ORG_POLICY = "ET_HARNESS_ORG_POLICY"
ENV_USER_CONFIG = "ET_HARNESS_USER_CONFIG"


def default_user_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "et-harness" / "user.yaml"


def load_layer_document(path: Path, expected_kind: str, schema: str) -> dict[str, Any]:
    document = load_yaml_file(path)
    validate_document(document, schema, source=str(path))
    if document.get("kind") != expected_kind:
        raise HarnessError(
            ReasonCode.CONFIG_INVALID,
            f"{path}: expected kind {expected_kind}, found {document.get('kind')!r}",
            {"source": str(path), "expected_kind": expected_kind},
        )
    return document


@dataclass
class LoadedConfiguration:
    effective: EffectiveConfig
    project_root: Path
    project: dict[str, Any] | None
    notes: list[str] = field(default_factory=list)


def load_configuration(
    project_root: Path,
    *,
    org_policy: Path | None = None,
    user_config: Path | None = None,
    extra_layers: list[ConfigLayer] | None = None,
) -> LoadedConfiguration:
    """Load every available layer for ``project_root`` and resolve them.

    ``extra_layers`` carries feature and persona layers, which come from the
    locked contract and persona registry in later HK features.
    """
    project_root = Path(project_root)
    layers: list[ConfigLayer] = []
    notes: list[str] = []

    project_path = project_root / PROJECT_CONFIG_RELATIVE
    project: dict[str, Any] | None = None
    if project_path.is_file():
        project = load_layer_document(project_path, "ProjectConfig", "project-config")
        layers.append(ConfigLayer(LayerName.PROJECT, str(project_path), project.get("settings") or {}))
    else:
        notes.append(f"no project configuration at {project_path}")

    org_path = org_policy
    if org_path is None and os.environ.get(ENV_ORG_POLICY):
        org_path = Path(os.environ[ENV_ORG_POLICY])
    if org_path is None and project and project.get("organisation_policy"):
        org_path = _inside(project_root, project["organisation_policy"])
    if org_path is not None:
        org = load_layer_document(Path(org_path), "OrganisationPolicy", "harness-config")
        layers.append(ConfigLayer(LayerName.ORGANISATION, str(org_path), org.get("settings") or {}))
    else:
        notes.append("no organisation policy configured")

    user_path = user_config
    if user_path is None and os.environ.get(ENV_USER_CONFIG):
        user_path = Path(os.environ[ENV_USER_CONFIG])
    if user_path is None:
        candidate = default_user_config_path()
        user_path = candidate if candidate.is_file() else None
    if user_path is not None:
        user = load_layer_document(Path(user_path), "UserPreferences", "harness-config")
        layers.append(ConfigLayer(LayerName.USER_LOCAL, str(user_path), user.get("settings") or {}))
    else:
        notes.append("no user-local preferences found")

    for layer in extra_layers or []:
        if layer.name not in (LayerName.FEATURE, LayerName.PERSONA):
            raise HarnessError(
                ReasonCode.CONFIG_INVALID,
                f"extra layer {layer.name.value} is not a feature or persona layer",
            )
        layers.append(layer)

    return LoadedConfiguration(resolve(layers), project_root, project, notes)


def _inside(root: Path, relative: str) -> Path:
    """Resolve a repository-relative path and reject traversal outside root."""
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise HarnessError(
            ReasonCode.CONFIG_INVALID,
            f"path escapes the repository root: {relative}",
            {"path": relative},
        ) from None
    return candidate
