"""Safe YAML loading that rejects duplicate keys and non-mapping roots."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)

# YAML 1.2 booleans: only true/false. Under YAML 1.1 (PyYAML's default) the
# unquoted words yes/no/on/off are booleans, so `qe_test_design: off` would
# load as False and silently change a contract's meaning.
_BOOL_TAG = "tag:yaml.org,2002:bool"
_UniqueKeyLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_UniqueKeyLoader.add_implicit_resolver(
    _BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def load_yaml_text(text: str, *, source: str = "<string>") -> dict[str, Any]:
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise HarnessError(
            ReasonCode.CONFIG_INVALID, f"YAML parse error in {source}", {"source": source},
            details=[str(exc)],
        ) from exc
    if not isinstance(data, dict):
        raise HarnessError(
            ReasonCode.CONFIG_INVALID,
            f"{source} must contain a YAML mapping at the top level",
            {"source": source},
        )
    return data


def load_yaml_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarnessError(
            ReasonCode.CONFIG_INVALID, f"cannot read {path}", {"source": str(path)},
            details=[str(exc)],
        ) from exc
    return load_yaml_text(text, source=str(path))


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
