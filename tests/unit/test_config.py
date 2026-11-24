"""AC-003: layered configuration precedence and no-weakening rule (§9.3, §28.1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.config.layers import ConfigLayer, LayerName, resolve
from et_harness.config.loader import load_configuration


def layer(name: LayerName, settings: dict, source: str | None = None) -> ConfigLayer:
    return ConfigLayer(name, source or f"{name.value}.yaml", settings)


def test_defaults_apply_when_no_layers() -> None:
    effective = resolve([])
    assert effective.ok
    assert effective.value("rework.maximum_cycles") == 3
    assert effective.settings["rework.maximum_cycles"].layer is LayerName.BUILTIN_DEFAULT


def test_kernel_invariants_always_present() -> None:
    effective = resolve([])
    setting = effective.settings["tools.allow_unrestricted_shell"]
    assert setting.value is False
    assert setting.invariant
    assert setting.layer is LayerName.KERNEL_INVARIANT


def test_lower_layer_cannot_change_invariant() -> None:
    effective = resolve([layer(LayerName.PROJECT, {"tools": {"allow_unrestricted_shell": True}})])
    assert effective.value("tools.allow_unrestricted_shell") is False
    assert [c.key for c in effective.conflicts] == ["tools.allow_unrestricted_shell"]
    assert effective.conflicts[0].reason_code is ReasonCode.CONFIG_WEAKENING_REJECTED


def test_precedence_order_is_respected_for_preferences() -> None:
    effective = resolve([
        layer(LayerName.USER_LOCAL, {"output": {"format": "json"}}),
        layer(LayerName.PROJECT, {"output": {"format": "text"}}),
    ])
    assert effective.value("output.format") == "text"
    assert effective.settings["output.format"].layer is LayerName.PROJECT
    assert [s.layer for s in effective.shadowed] == [LayerName.USER_LOCAL]
    assert effective.ok  # shadowing a preference is not a conflict


def test_lower_layer_fills_unset_preference() -> None:
    effective = resolve([layer(LayerName.USER_LOCAL, {"output": {"format": "json"}})])
    assert effective.value("output.format") == "json"
    assert effective.settings["output.format"].source == "user-local.yaml"


def test_user_local_cannot_weaken_organisation_policy() -> None:
    effective = resolve([
        layer(LayerName.ORGANISATION, {"enforcement": {"minimum_write_tier": "ENFORCED"},
                                       "rework": {"maximum_cycles": 2}}),
        layer(LayerName.USER_LOCAL, {"enforcement": {"minimum_write_tier": "ADVISORY"},
                                     "rework": {"maximum_cycles": 10}}),
    ])
    assert effective.value("enforcement.minimum_write_tier") == "ENFORCED"
    assert effective.value("rework.maximum_cycles") == 2
    assert not effective.ok
    assert {c.key for c in effective.conflicts} == {
        "enforcement.minimum_write_tier", "rework.maximum_cycles"}
    assert all(c.effective_layer is LayerName.ORGANISATION for c in effective.conflicts)


def test_lower_layer_may_tighten_a_control() -> None:
    effective = resolve([
        layer(LayerName.PROJECT, {"rework": {"maximum_cycles": 3}}),
        layer(LayerName.FEATURE, {"rework": {"maximum_cycles": 1}}),
    ])
    setting = effective.settings["rework.maximum_cycles"]
    assert setting.value == 1
    assert setting.layer is LayerName.FEATURE
    assert setting.tightened_by == ["feature"]
    assert effective.ok


def test_full_precedence_chain_with_attribution() -> None:
    effective = resolve([
        layer(LayerName.PERSONA, {"lite": {"max_changed_files": 2}}),
        layer(LayerName.FEATURE, {"lite": {"max_changed_files": 4}}),
        layer(LayerName.PROJECT, {"lite": {"max_changed_files": 5}}),
        layer(LayerName.ORGANISATION, {"lite": {"max_changed_files": 8}}),
        layer(LayerName.USER_LOCAL, {"lite": {"max_changed_files": 3}}),  # weaker than persona's 2
    ])
    setting = effective.settings["lite.max_changed_files"]
    assert setting.value == 2
    assert setting.layer is LayerName.PERSONA
    assert setting.tightened_by == ["project", "feature", "persona"]
    assert [(c.layer, c.attempted) for c in effective.conflicts] == [(LayerName.USER_LOCAL, 3)]


def test_ordered_enum_level_strictness() -> None:
    effective = resolve([
        layer(LayerName.PROJECT, {"lite": {"max_verification_level": "L2"}}),
        layer(LayerName.USER_LOCAL, {"lite": {"max_verification_level": "L4"}}),
    ])
    assert effective.value("lite.max_verification_level") == "L2"
    assert effective.conflicts[0].key == "lite.max_verification_level"


def test_union_lists_only_grow() -> None:
    effective = resolve([
        layer(LayerName.ORGANISATION, {"audit": {"redaction_patterns": ["corp-[0-9]+"]}}),
        layer(LayerName.USER_LOCAL, {"audit": {"redaction_patterns": ["mine-[a-z]+"]}}),
    ])
    assert effective.value("audit.redaction_patterns") == ["corp-[0-9]+", "mine-[a-z]+"]
    assert effective.ok


def test_layer_order_in_input_does_not_matter() -> None:
    a = layer(LayerName.ORGANISATION, {"rework": {"maximum_cycles": 2}})
    b = layer(LayerName.USER_LOCAL, {"rework": {"maximum_cycles": 9}})
    assert resolve([a, b]).to_dict() == resolve([b, a]).to_dict()


def test_every_setting_has_source_attribution() -> None:
    data = resolve([layer(LayerName.PROJECT, {"output": {"format": "json"}})]).to_dict()
    for setting in data["settings"].values():
        assert setting["layer"] and setting["source"]


# --- loader -------------------------------------------------------------------

@pytest.fixture
def project_root(tmp_path: Path, fixtures_dir: Path) -> Path:
    root = tmp_path / "product"
    (root / ".agent-harness").mkdir(parents=True)
    shutil.copy(fixtures_dir / "config" / "project.yaml", root / ".agent-harness" / "project.yaml")
    return root


def test_loader_reads_all_layers(project_root: Path, fixtures_dir: Path, monkeypatch) -> None:
    monkeypatch.delenv("ET_HARNESS_USER_CONFIG", raising=False)
    monkeypatch.delenv("ET_HARNESS_ORG_POLICY", raising=False)
    loaded = load_configuration(
        project_root,
        org_policy=fixtures_dir / "config" / "org-policy.yaml",
        user_config=fixtures_dir / "config" / "user-weakening.yaml",
    )
    eff = loaded.effective
    assert eff.value("enforcement.minimum_write_tier") == "ENFORCED"
    assert eff.value("rework.maximum_cycles") == 2       # org wins over project 3 and user 10
    assert eff.value("rework.repeated_failure_threshold") == 1  # project tightened default
    assert eff.value("output.format") == "text"          # project preference beats user
    assert {c.key for c in eff.conflicts} == {"enforcement.minimum_write_tier", "rework.maximum_cycles"}


def test_loader_org_policy_from_project_config(project_root: Path, fixtures_dir: Path, monkeypatch) -> None:
    monkeypatch.delenv("ET_HARNESS_ORG_POLICY", raising=False)
    shutil.copy(fixtures_dir / "config" / "org-policy.yaml", project_root / "org.yaml")
    project_file = project_root / ".agent-harness" / "project.yaml"
    project_file.write_text(project_file.read_text() + "organisation_policy: org.yaml\n")
    loaded = load_configuration(project_root, user_config=None)
    assert loaded.effective.settings["enforcement.minimum_write_tier"].layer is LayerName.ORGANISATION


def test_loader_rejects_org_policy_path_traversal(project_root: Path, monkeypatch) -> None:
    monkeypatch.delenv("ET_HARNESS_ORG_POLICY", raising=False)
    project_file = project_root / ".agent-harness" / "project.yaml"
    project_file.write_text(project_file.read_text() + "organisation_policy: ../outside.yaml\n")
    with pytest.raises(HarnessError) as info:
        load_configuration(project_root)
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_loader_rejects_wrong_kind(project_root: Path, fixtures_dir: Path) -> None:
    with pytest.raises(HarnessError) as info:
        load_configuration(project_root, org_policy=fixtures_dir / "config" / "user-weakening.yaml")
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_loader_rejects_invalid_project_config(tmp_path: Path) -> None:
    (tmp_path / ".agent-harness").mkdir()
    (tmp_path / ".agent-harness" / "project.yaml").write_text(
        "api_version: embedded-targets.ai/v1\nkind: ProjectConfig\nproject: {id: x}\n"
    )
    with pytest.raises(HarnessError) as info:
        load_configuration(tmp_path)
    assert info.value.code is ReasonCode.CONFIG_INVALID


def test_loader_rejects_unsupported_version(tmp_path: Path) -> None:
    (tmp_path / ".agent-harness").mkdir()
    (tmp_path / ".agent-harness" / "project.yaml").write_text(
        "api_version: embedded-targets.ai/v9\nkind: ProjectConfig\n"
        "project: {id: x, name: x}\nharness: {version: 0.1.0}\n"
    )
    with pytest.raises(HarnessError) as info:
        load_configuration(tmp_path)
    assert info.value.code is ReasonCode.SCHEMA_VERSION_UNSUPPORTED


def test_feature_and_persona_layers_accepted_programmatically(tmp_path: Path) -> None:
    loaded = load_configuration(tmp_path, extra_layers=[
        layer(LayerName.FEATURE, {"rework": {"maximum_cycles": 1}})])
    assert loaded.effective.value("rework.maximum_cycles") == 1


def test_extra_layers_cannot_impersonate_organisation(tmp_path: Path) -> None:
    with pytest.raises(HarnessError):
        load_configuration(tmp_path, extra_layers=[layer(LayerName.ORGANISATION, {})])
