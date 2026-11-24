"""Registry of kernel settings and their strictness semantics (§9.3).

A *control* has a strictness ordering, so the kernel can tell whether a lower
precedence layer is tightening it (allowed) or weakening it (rejected). A
*preference* has no ordering: the highest-precedence layer that sets it wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from et_harness.common.redaction import DEFAULT_PATTERNS


class Strictness(str, Enum):
    LOWER_IS_STRICTER = "lower_is_stricter"        # integers
    HIGHER_IS_STRICTER = "higher_is_stricter"      # integers
    TRUE_IS_STRICTER = "true_is_stricter"          # booleans
    FALSE_IS_STRICTER = "false_is_stricter"        # booleans
    ORDERED_ENUM = "ordered_enum"                  # later in `order` is stricter
    UNION = "union"                                # lists: layers may only add
    PREFERENCE = "preference"                      # no ordering


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any
    strictness: Strictness
    order: tuple[str, ...] = ()
    invariant: bool = False
    description: str = ""

    @property
    def is_control(self) -> bool:
        return self.strictness is not Strictness.PREFERENCE

    def compare(self, candidate: Any, current: Any) -> int:
        """Return >0 if candidate is stricter, 0 if equal strength, <0 if weaker."""
        s = self.strictness
        if s is Strictness.LOWER_IS_STRICTER:
            return (current > candidate) - (current < candidate)
        if s is Strictness.HIGHER_IS_STRICTER:
            return (candidate > current) - (candidate < current)
        if s is Strictness.TRUE_IS_STRICTER:
            return int(candidate) - int(current)
        if s is Strictness.FALSE_IS_STRICTER:
            return int(current) - int(candidate)
        if s is Strictness.ORDERED_ENUM:
            a, b = self.order.index(candidate), self.order.index(current)
            return (a > b) - (a < b)
        if s is Strictness.UNION:
            cand, cur = set(candidate), set(current)
            if cand == cur:
                return 0
            return 1 if cand >= cur else -1
        return 0


TIERS = ("ADVISORY", "DETECTED", "ENFORCED")
LEVELS_STRICTEST_LAST = ("L6", "L5", "L4", "L3", "L2", "L1", "L0")

SETTINGS: dict[str, SettingSpec] = {
    s.key: s
    for s in (
        SettingSpec("tools.allow_unrestricted_shell", False, Strictness.FALSE_IS_STRICTER,
                    invariant=True, description="No unrestricted shell tool (§9.14)."),
        SettingSpec("packages.allow_unpinned_git_sources", False, Strictness.FALSE_IS_STRICTER,
                    invariant=True, description="Git sources must be pinned to a SHA (§9.9)."),
        SettingSpec("verdict.local_authority", "advisory", Strictness.ORDERED_ENUM,
                    order=("advisory",), invariant=True,
                    description="Local verdicts are advisory only (§3.12)."),
        SettingSpec("scope.detect_on_checkpoint", True, Strictness.TRUE_IS_STRICTER,
                    invariant=True, description="Scope detection always runs (§3.11)."),
        SettingSpec("enforcement.minimum_write_tier", "DETECTED", Strictness.ORDERED_ENUM,
                    order=TIERS, description="Minimum enforcement tier for writes."),
        SettingSpec("rework.maximum_cycles", 3, Strictness.LOWER_IS_STRICTER),
        SettingSpec("rework.repeated_failure_threshold", 2, Strictness.LOWER_IS_STRICTER),
        SettingSpec("lite.max_changed_files", 5, Strictness.LOWER_IS_STRICTER),
        SettingSpec("lite.max_changed_lines", 300, Strictness.LOWER_IS_STRICTER),
        SettingSpec("lite.max_verification_level", "L3", Strictness.ORDERED_ENUM,
                    order=LEVELS_STRICTEST_LAST),
        SettingSpec("audit.redaction_patterns", list(DEFAULT_PATTERNS), Strictness.UNION),
        SettingSpec("hook.decision_budget_ms", 2000, Strictness.PREFERENCE),
        SettingSpec("tools.output_limit_bytes", 1_000_000, Strictness.PREFERENCE),
        SettingSpec("output.format", "text", Strictness.PREFERENCE),
    )
}


def flatten(settings: dict[str, Any] | None, prefix: str = "") -> dict[str, Any]:
    """Flatten nested settings to dotted keys (leaves are non-dict values)."""
    flat: dict[str, Any] = {}
    for key, value in (settings or {}).items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, dotted + "."))
        else:
            flat[dotted] = value
    return flat


def unflatten(flat: dict[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for dotted, value in flat.items():
        node = nested
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return nested
