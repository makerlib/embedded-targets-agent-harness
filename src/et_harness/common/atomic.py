"""Atomic file replacement and append helpers (§9.5, §29 Reliability)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _fsync_directory(directory: Path) -> None:
    # Directory fsync makes the rename durable on POSIX; Windows does not
    # support opening directories, and its replace is already metadata-safe.
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers see either the old or new file.

    The data is written to a temporary file in the same directory, flushed and
    fsynced, then moved over the target with ``os.replace``. If anything fails
    the temporary file is removed and the original is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def append_line_durable(path: Path, line: str) -> None:
    """Append one line (newline added) and fsync before returning."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "\n" in line:
        raise ValueError("append_line_durable expects a single line")
    with open(path, "ab") as handle:
        handle.write(line.encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
