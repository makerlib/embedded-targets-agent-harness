"""JSON Schema loading and validation (§9.3, draft 2020-12).

Schemas live in the repository's top-level ``schemas/`` directory (§7). The
directory is located from ``ET_HARNESS_SCHEMA_DIR`` or relative to the source
tree. Packaging the schemas inside a wheel is a known HK-001 limitation.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from et_harness import SUPPORTED_API_VERSIONS
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode

SCHEMA_ID_BASE = "https://embedded-targets.ai/schemas/"


def schema_dir() -> Path:
    override = os.environ.get("ET_HARNESS_SCHEMA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "schemas"


@lru_cache(maxsize=None)
def _registry(directory: str) -> tuple[Registry, dict[str, dict[str, Any]]]:
    base = Path(directory)
    if not base.is_dir():
        raise HarnessError(
            ReasonCode.MANDATORY_DEPENDENCY_UNAVAILABLE,
            f"schema directory not found: {base}",
            {"schema_dir": str(base)},
        )
    schemas: dict[str, dict[str, Any]] = {}
    resources = []
    for path in sorted(base.glob("*.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        name = path.name[: -len(".schema.json")]
        schemas[name] = document
        resources.append((document["$id"], Resource.from_contents(document)))
    return Registry().with_resources(resources), schemas


def available_schemas() -> list[str]:
    return sorted(_registry(str(schema_dir()))[1])


def load_schema(name: str) -> dict[str, Any]:
    _, schemas = _registry(str(schema_dir()))
    try:
        return schemas[name]
    except KeyError:
        raise HarnessError(
            ReasonCode.INTERNAL_ERROR, f"unknown schema: {name}", {"schema": name}
        ) from None


def check_api_version(document: dict[str, Any], *, source: str) -> None:
    version = document.get("api_version")
    if version not in SUPPORTED_API_VERSIONS:
        raise HarnessError(
            ReasonCode.SCHEMA_VERSION_UNSUPPORTED,
            f"{source}: unsupported api_version {version!r}",
            {"source": source, "api_version": version,
             "supported": sorted(SUPPORTED_API_VERSIONS)},
        )


def validation_errors(document: Any, name: str) -> list[str]:
    registry, _ = _registry(str(schema_dir()))
    validator = Draft202012Validator(load_schema(name), registry=registry)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]


def validate_document(
    document: Any,
    name: str,
    *,
    source: str = "<document>",
    code: ReasonCode = ReasonCode.CONFIG_INVALID,
) -> None:
    """Validate api_version first, then the full schema.

    Raises ``SCHEMA_VERSION_UNSUPPORTED`` for an unknown api_version and
    ``code`` (default ``CONFIG_INVALID``) for any other schema violation,
    including missing required keys and unknown keys.
    """
    if not isinstance(document, dict):
        raise HarnessError(code, f"{source}: document must be an object", {"source": source})
    check_api_version(document, source=source)
    errors = validation_errors(document, name)
    if errors:
        raise HarnessError(
            code,
            f"{source}: {len(errors)} schema violation(s) against {name}",
            {"source": source, "schema": name},
            details=errors,
        )
