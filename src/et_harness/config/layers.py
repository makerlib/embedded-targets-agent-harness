"""Layered configuration resolution (§9.3).

Precedence, highest first::

    kernel-invariant > organisation > project > feature > persona > user-local

Built-in defaults fill any key that no layer sets; they are not a control and
never tighten a value chosen by a layer.

Resolution walks the layers from highest to lowest precedence:

* an unset key takes the layer's value;
* for a *control*, a lower layer may tighten the value (it then becomes the
  source) but a weaker value is rejected and recorded as a conflict with
  ``CONFIG_WEAKENING_REJECTED``; kernel invariants cannot be changed at all;
* for a *preference*, the higher layer wins and the lower value is recorded as
  shadowed (not a conflict).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from et_harness.common.reason_codes import ReasonCode
from et_harness.config.controls import SETTINGS, SettingSpec, Strictness, flatten, unflatten


class LayerName(str, Enum):
    KERNEL_INVARIANT = "kernel-invariant"
    ORGANISATION = "organisation"
    PROJECT = "project"
    FEATURE = "feature"
    PERSONA = "persona"
    USER_LOCAL = "user-local"
    BUILTIN_DEFAULT = "builtin-default"


PRECEDENCE: tuple[LayerName, ...] = (
    LayerName.KERNEL_INVARIANT,
    LayerName.ORGANISATION,
    LayerName.PROJECT,
    LayerName.FEATURE,
    LayerName.PERSONA,
    LayerName.USER_LOCAL,
)


@dataclass(frozen=True)
class ConfigLayer:
    name: LayerName
    source: str
    settings: dict[str, Any]  # nested, as written in YAML


@dataclass
class EffectiveSetting:
    key: str
    value: Any
    layer: LayerName
    source: str
    kind: str  # "control" | "preference"
    invariant: bool = False
    tightened_by: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigConflict:
    key: str
    layer: LayerName
    source: str
    attempted: Any
    effective: Any
    effective_layer: LayerName
    reason_code: ReasonCode = ReasonCode.CONFIG_WEAKENING_REJECTED


@dataclass(frozen=True)
class ShadowedSetting:
    key: str
    layer: LayerName
    source: str
    value: Any
    winner_layer: LayerName


@dataclass
class EffectiveConfig:
    settings: dict[str, EffectiveSetting]
    conflicts: list[ConfigConflict]
    shadowed: list[ShadowedSetting]
    layers: list[ConfigLayer]

    def value(self, key: str) -> Any:
        return self.settings[key].value

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def values_nested(self) -> dict[str, Any]:
        return unflatten({k: s.value for k, s in sorted(self.settings.items())})

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings": {
                k: {
                    "value": s.value,
                    "layer": s.layer.value,
                    "source": s.source,
                    "kind": s.kind,
                    "invariant": s.invariant,
                    "tightened_by": s.tightened_by,
                }
                for k, s in sorted(self.settings.items())
            },
            "conflicts": [
                {
                    "key": c.key,
                    "layer": c.layer.value,
                    "source": c.source,
                    "attempted": c.attempted,
                    "effective": c.effective,
                    "effective_layer": c.effective_layer.value,
                    "reason_code": c.reason_code.value,
                }
                for c in self.conflicts
            ],
            "shadowed": [
                {
                    "key": s.key,
                    "layer": s.layer.value,
                    "source": s.source,
                    "value": s.value,
                    "winner_layer": s.winner_layer.value,
                }
                for s in self.shadowed
            ],
            "layers": [{"name": l.name.value, "source": l.source} for l in self.layers],
        }


def kernel_invariant_layer() -> ConfigLayer:
    flat = {k: s.default for k, s in SETTINGS.items() if s.invariant}
    return ConfigLayer(LayerName.KERNEL_INVARIANT, "et_harness.config.controls", unflatten(flat))


def _merge_union(current: list[Any], extra: list[Any]) -> list[Any]:
    merged = list(current)
    merged.extend(item for item in extra if item not in merged)
    return merged


def resolve(layers: list[ConfigLayer]) -> EffectiveConfig:
    """Resolve the effective configuration from the supplied layers.

    The kernel-invariant layer is always applied first, whether or not the
    caller includes it. Unknown keys must already have been rejected by schema
    validation; they are ignored here defensively.
    """
    supplied = [l for l in layers if l.name is not LayerName.KERNEL_INVARIANT]
    ordered = [kernel_invariant_layer()] + sorted(
        supplied, key=lambda l: PRECEDENCE.index(l.name)
    )
    effective: dict[str, EffectiveSetting] = {}
    conflicts: list[ConfigConflict] = []
    shadowed: list[ShadowedSetting] = []

    for layer in ordered:
        for key, value in flatten(layer.settings).items():
            spec = SETTINGS.get(key)
            if spec is None:
                continue
            current = effective.get(key)
            if current is None:
                effective[key] = EffectiveSetting(
                    key, value, layer.name, layer.source,
                    "control" if spec.is_control else "preference",
                    invariant=spec.invariant and layer.name is LayerName.KERNEL_INVARIANT,
                )
                continue
            _apply(spec, current, layer, value, conflicts, shadowed)

    for key, spec in SETTINGS.items():
        if key not in effective:
            effective[key] = EffectiveSetting(
                key, spec.default, LayerName.BUILTIN_DEFAULT, "et_harness.config.controls",
                "control" if spec.is_control else "preference",
            )
    return EffectiveConfig(effective, conflicts, shadowed, ordered)


def _apply(
    spec: SettingSpec,
    current: EffectiveSetting,
    layer: ConfigLayer,
    value: Any,
    conflicts: list[ConfigConflict],
    shadowed: list[ShadowedSetting],
) -> None:
    if not spec.is_control:
        if value != current.value:
            shadowed.append(ShadowedSetting(spec.key, layer.name, layer.source, value, current.layer))
        return
    if current.invariant:
        if value != current.value:
            conflicts.append(ConfigConflict(spec.key, layer.name, layer.source, value,
                                            current.value, current.layer))
        return
    comparison = spec.compare(value, current.value)
    if spec.strictness is Strictness.UNION:
        merged = _merge_union(current.value, value)
        if merged != current.value:
            current.value = merged
            current.tightened_by.append(layer.name.value)
        return
    if comparison > 0:
        current.value = value
        current.tightened_by.append(layer.name.value)
        current.layer, current.source = layer.name, layer.source
    elif comparison < 0:
        conflicts.append(ConfigConflict(spec.key, layer.name, layer.source, value,
                                        current.value, current.layer))
