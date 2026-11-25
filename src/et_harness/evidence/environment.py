"""Environment description and fingerprint (§9.2, §21 environment.yaml)."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from et_harness import __version__
from et_harness.common.hashing import digest

PACKAGES = ("PyYAML", "jsonschema", "click", "referencing")


def _tool_version(argv: list[str]) -> str | None:
    if shutil.which(argv[0]) is None:
        return None
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else None


def describe_environment() -> dict[str, Any]:
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    env = {
        "harness_version": __version__,
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "git": _tool_version(["git", "--version"]),
        "packages": packages,
        "matlab": None,  # MatlabSessionPort detection arrives in HK-006
    }
    stable = {k: v for k, v in env.items() if k != "python_executable"}
    env["fingerprint"] = digest(stable)
    return env
