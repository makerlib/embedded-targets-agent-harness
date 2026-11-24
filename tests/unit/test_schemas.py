"""AC-004: schema validation, CONFIG_INVALID and SCHEMA_VERSION_UNSUPPORTED (§28.1)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.schemas import validate_document
from et_harness.common.yaml_io import load_yaml_file, load_yaml_text


@pytest.fixture
def state_doc(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "orchestration-state.valid.json").read_text())


def test_valid_orchestration_state_passes(state_doc: dict) -> None:
    validate_document(state_doc, "orchestration-state")


def test_valid_project_config_passes(fixtures_dir: Path) -> None:
    validate_document(load_yaml_file(fixtures_dir / "config" / "project.yaml"), "project-config")


@pytest.mark.parametrize("missing", ["workflow", "integrity", "history", "kind"])
def test_missing_required_key_fails(state_doc: dict, missing: str) -> None:
    del state_doc[missing]
    with pytest.raises(HarnessError) as info:
        validate_document(state_doc, "orchestration-state")
    assert info.value.code is ReasonCode.CONFIG_INVALID
    assert any(missing in d for d in info.value.details)


def test_missing_nested_required_key_fails(state_doc: dict) -> None:
    del state_doc["workflow"]["revision"]
    with pytest.raises(HarnessError) as info:
        validate_document(state_doc, "orchestration-state")
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_unknown_key_fails(state_doc: dict) -> None:
    state_doc["workflow"]["surprise"] = True
    with pytest.raises(HarnessError) as info:
        validate_document(state_doc, "orchestration-state")
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_unknown_setting_key_fails() -> None:
    doc = load_yaml_text(
        "api_version: embedded-targets.ai/v1\nkind: UserPreferences\nmetadata: {name: u}\n"
        "settings: {tools: {allow_everything: true}}\n"
    )
    with pytest.raises(HarnessError) as info:
        validate_document(doc, "harness-config")
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_invalid_state_enum_fails(state_doc: dict) -> None:
    state_doc["workflow"]["current_state"] = "INITIALIZING"  # American spelling is not a state
    with pytest.raises(HarnessError):
        validate_document(state_doc, "orchestration-state")


@pytest.mark.parametrize("version", ["embedded-targets.ai/v2", "v1", None])
def test_unsupported_schema_version_fails(state_doc: dict, version: str | None) -> None:
    doc = copy.deepcopy(state_doc)
    doc["api_version"] = version
    with pytest.raises(HarnessError) as info:
        validate_document(doc, "orchestration-state")
    assert info.value.code is ReasonCode.SCHEMA_VERSION_UNSUPPORTED


def test_duplicate_yaml_keys_rejected() -> None:
    with pytest.raises(HarnessError) as info:
        load_yaml_text("a: 1\na: 2\n")
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_non_mapping_yaml_rejected() -> None:
    with pytest.raises(HarnessError):
        load_yaml_text("- just\n- a list\n")


# --- HK-002: YAML 1.2 boolean semantics -------------------------------------------------

@pytest.mark.parametrize("word", ["off", "on", "yes", "no", "y", "n", "Off", "NO"])
def test_yaml_1_1_bool_words_stay_strings(word: str) -> None:
    assert load_yaml_text(f"value: {word}\n")["value"] == word


@pytest.mark.parametrize("word,expected", [("true", True), ("false", False), ("True", True), ("FALSE", False)])
def test_yaml_true_false_are_booleans(word: str, expected: bool) -> None:
    assert load_yaml_text(f"value: {word}\n")["value"] is expected


def test_pyyaml_safe_loader_unchanged() -> None:
    import yaml

    assert yaml.safe_load("value: off\n")["value"] is False  # the global loader is not patched
