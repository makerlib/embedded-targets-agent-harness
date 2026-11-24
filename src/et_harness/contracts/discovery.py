"""Active-feature discovery (§3.5, §28.2): exactly one contract in features/active."""

from __future__ import annotations

from pathlib import Path

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode
from et_harness.common.yaml_io import load_yaml_file
from et_harness.contracts.model import FeatureContract, load_contract

FEATURE_DIRS = ("backlog", "active", "blocked", "completed")


def contract_files(directory: Path) -> list[Path]:
    """YAML files in ``directory`` whose kind is FeatureContract (plans etc. skipped)."""
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.y*ml")):
        try:
            kind = load_yaml_file(path).get("kind")
        except HarnessError:
            kind = "FeatureContract"  # unreadable: count it, so it is not silently ignored
        if kind == "FeatureContract":
            found.append(path)
    return found


def find_active_feature(features_dir: Path) -> FeatureContract:
    active = contract_files(Path(features_dir) / "active")
    if not active:
        raise HarnessError(ReasonCode.NO_ACTIVE_FEATURE,
                           f"no feature contract in {Path(features_dir) / 'active'}")
    if len(active) > 1:
        raise HarnessError(ReasonCode.MULTIPLE_ACTIVE_FEATURES,
                           f"{len(active)} active feature contracts; exactly one is allowed",
                           {"contracts": [str(p) for p in active]})
    return load_contract(active[0])
