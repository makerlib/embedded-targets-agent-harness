"""Repository-relative path normalisation and scope glob matching (§9.4, §9.20).

Paths are POSIX-style and relative to the repository root. Globs support:

* ``**``  any number of whole path segments, including none
* ``*``   any characters within one segment
* ``?``   one character within a segment
* ``[..]`` a character class within a segment

A glob that names a directory without a wildcard (``src/adc``) matches only
that exact path; write ``src/adc/**`` for the tree.
"""

from __future__ import annotations

import re
from functools import lru_cache

from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode


def normalise_path(path: str) -> str:
    """Normalise a repository-relative path; reject absolute paths and traversal."""
    if not isinstance(path, str) or not path.strip():
        raise HarnessError(ReasonCode.PATH_OUTSIDE_SCOPE, "empty path", {"path": path})
    candidate = path.replace("\\", "/")
    if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate) or candidate.startswith("//"):
        raise HarnessError(ReasonCode.PATH_OUTSIDE_SCOPE, f"absolute path not allowed: {path}",
                           {"path": path})
    parts = []
    for part in candidate.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise HarnessError(ReasonCode.PATH_OUTSIDE_SCOPE, f"path traversal not allowed: {path}",
                               {"path": path})
        if "\x00" in part:
            raise HarnessError(ReasonCode.PATH_OUTSIDE_SCOPE, "NUL byte in path", {"path": path})
        parts.append(part)
    if not parts:
        raise HarnessError(ReasonCode.PATH_OUTSIDE_SCOPE, f"path resolves to the root: {path}",
                           {"path": path})
    return "/".join(parts)


def validate_glob(glob: str) -> str | None:
    """Return an error message for an invalid scope glob, else None."""
    if not isinstance(glob, str) or not glob.strip():
        return "glob is empty"
    g = glob.replace("\\", "/")
    if g.startswith("/") or re.match(r"^[A-Za-z]:", g):
        return "glob must be repository-relative"
    segments = g.split("/")
    if any(s == ".." for s in segments):
        return "glob must not contain '..'"
    if any(s == "" for s in segments):
        return "glob has an empty segment"
    for s in segments:
        if "**" in s and s != "**":
            return "'**' must be a whole path segment"
        if s.count("[") != s.count("]"):
            return "unbalanced character class"
    try:
        _compile(g)
    except re.error as exc:
        return f"invalid pattern: {exc}"
    return None


def _segment_regex(segment: str) -> str:
    out, i = [], 0
    while i < len(segment):
        c = segment[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = segment.index("]", i + 1)
            body = segment[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("/", "") + "]")
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@lru_cache(maxsize=4096)
def _compile(glob: str) -> re.Pattern[str]:
    segments = glob.split("/")
    pattern = ""
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            pattern += ".*" if last else "(?:[^/]+/)*"
        else:
            pattern += _segment_regex(segment) + ("" if last else "/")
    return re.compile("^" + pattern + "$")


def glob_matches(glob: str, path: str) -> bool:
    return bool(_compile(glob.replace("\\", "/")).match(path))


def matches_any(globs: list[str] | tuple[str, ...], path: str) -> bool:
    return any(glob_matches(g, path) for g in globs)
