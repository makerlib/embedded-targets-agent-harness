"""`structural` gate (L0) for HK-001: schemas, packaging metadata, config files."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import et_harness
from et_harness.common.schemas import available_schemas, validate_document
from et_harness.common.yaml_io import load_yaml_file
from et_harness.config.controls import SETTINGS, flatten

REPO = Path(__file__).resolve().parents[2]
HK001_SCHEMAS = ["artefact-classes", "audit-record", "feature", "harness-config", "orchestration-state", "project-config", "scope-lock"]


@pytest.mark.parametrize("name", HK001_SCHEMAS)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    document = json.loads((REPO / "schemas" / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(document)
    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert document["$id"].endswith(f"/{name}.schema.json")


def test_all_hk001_schemas_are_registered() -> None:
    assert set(HK001_SCHEMAS) <= set(available_schemas())


def test_pyproject_reads_version_from_single_source() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "et_harness.__version__"}
    assert pyproject["project"]["scripts"]["harness"] == "et_harness.cli.main:main"


def test_harness_yaml_is_valid() -> None:
    document = load_yaml_file(REPO / "harness.yaml")
    validate_document(document, "harness-config", source="harness.yaml")
    assert document["kind"] == "HarnessConfig"


def test_settings_schema_matches_control_registry() -> None:
    schema = json.loads((REPO / "schemas" / "harness-config.schema.json").read_text())

    def leaves(node: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for key, sub in node["properties"].items():
            if sub.get("type") == "object" and "properties" in sub:
                keys |= leaves(sub, f"{prefix}{key}.")
            else:
                keys.add(f"{prefix}{key}")
        return keys

    assert leaves(schema["$defs"]["settings"]) == set(SETTINGS)


def test_registry_defaults_validate_against_schema() -> None:
    from et_harness.config.controls import unflatten

    document = {
        "api_version": et_harness.API_VERSION,
        "kind": "HarnessConfig",
        "metadata": {"name": "defaults"},
        "settings": unflatten({k: s.default for k, s in SETTINGS.items()}),
    }
    validate_document(document, "harness-config")
    assert set(flatten(document["settings"])) == set(SETTINGS)
