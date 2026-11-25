"""JUnit XML test-count capture (§9.16, §14.7)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def parse_junit(path: Path) -> tuple[dict[str, int] | None, list[str]]:
    """Return ({run, passed, failed, skipped}, failing test IDs), or (None, []) if unreadable.

    ``run`` excludes skipped tests; ``failed`` counts failures and errors.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None, []
    cases = list(root.iter("testcase"))
    if root.tag not in ("testsuites", "testsuite"):
        return None, []
    total = skipped = failed = 0
    failing: list[str] = []
    if cases:
        for case in cases:
            total += 1
            name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
            if case.find("skipped") is not None:
                skipped += 1
            elif case.find("failure") is not None or case.find("error") is not None:
                failed += 1
                failing.append(name)
    else:
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for suite in suites:
            total += int(suite.get("tests", 0))
            skipped += int(suite.get("skipped", 0))
            failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    run = total - skipped
    return {"run": run, "passed": max(run - failed, 0), "failed": failed, "skipped": skipped}, sorted(failing)
