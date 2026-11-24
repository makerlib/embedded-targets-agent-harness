"""Embedded Targets Agentic AI Harness kernel.

The single authoritative version of the kernel lives here; ``pyproject.toml``
reads it dynamically.
"""

__version__ = "0.1.0"

API_VERSION = "embedded-targets.ai/v1"
SUPPORTED_API_VERSIONS = frozenset({API_VERSION})

__all__ = ["__version__", "API_VERSION", "SUPPORTED_API_VERSIONS"]
