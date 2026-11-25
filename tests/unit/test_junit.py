"""HK-004 AC-007: JUnit test-count capture."""

from __future__ import annotations

from pathlib import Path

from et_harness.gates.junit import parse_junit


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(text)
    return path


def test_testcases_counted(tmp_path: Path) -> None:
    counts, failing = parse_junit(write(tmp_path, """<testsuites><testsuite name="s">
      <testcase classname="a" name="ok"/>
      <testcase classname="a" name="bad"><failure/></testcase>
      <testcase classname="b" name="err"><error/></testcase>
      <testcase classname="b" name="skip"><skipped/></testcase>
    </testsuite></testsuites>"""))
    assert counts == {"run": 3, "passed": 1, "failed": 2, "skipped": 1}
    assert failing == ["a::bad", "b::err"]


def test_suite_attributes_without_cases(tmp_path: Path) -> None:
    counts, _ = parse_junit(write(tmp_path, '<testsuite tests="10" failures="1" errors="1" skipped="2"/>'))
    assert counts == {"run": 8, "passed": 6, "failed": 2, "skipped": 2}


def test_unreadable_or_foreign_xml(tmp_path: Path) -> None:
    assert parse_junit(write(tmp_path, "not xml")) == (None, [])
    assert parse_junit(write(tmp_path, "<html/>")) == (None, [])
    assert parse_junit(tmp_path / "missing.xml") == (None, [])
