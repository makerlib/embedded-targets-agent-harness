"""Canonical hashing and atomic-write helpers used by AC-006 / AC-007 / AC-008."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from et_harness.common.atomic import append_line_durable, atomic_write_text
from et_harness.common.errors import HarnessError
from et_harness.common.hashing import canonical_json_bytes, digest


def test_canonical_form_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json_bytes({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'


def test_digest_stable_across_key_order() -> None:
    assert digest({"a": 1, "b": {"c": 2, "d": 3}}) == digest({"b": {"d": 3, "c": 2}, "a": 1})


def test_keys_sorted_by_utf16_code_units() -> None:
    # U+1F600 (surrogate pair D83D...) sorts before U+FF61 in UTF-16, after it by code point.
    data = {"｡": 1, "\U0001f600": 2}
    assert canonical_json_bytes(data).decode() == '{"\U0001f600":2,"｡":1}'


def test_floats_rejected() -> None:
    with pytest.raises(HarnessError):
        digest({"x": 1.5})


def test_digest_format() -> None:
    value = digest({})
    assert value.startswith("sha256:") and len(value) == len("sha256:") + 64


def test_atomic_write_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_atomic_write_failure_keeps_original(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "original")

    def fail(*_):
        raise OSError("crash")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    monkeypatch.undo()
    assert target.read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_append_rejects_multiline(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        append_line_durable(tmp_path / "x", "a\nb")
