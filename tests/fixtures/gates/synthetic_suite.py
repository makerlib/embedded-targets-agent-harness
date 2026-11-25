"""SYNTHETIC test fixture standing in for a test runner. It is not MATLAB.

Writes a JUnit XML file with --tests test cases. Each case named in
--require passes only if that path exists under the repository; others pass.
Exit code 0 if nothing failed, else 1. Optional --sleep to exercise timeouts.
"""
import argparse
import sys
import time
from pathlib import Path
from xml.sax.saxutils import quoteattr

ap = argparse.ArgumentParser()
ap.add_argument("--junit", required=True)
ap.add_argument("--repo", required=True)
ap.add_argument("--tests", type=int, default=3)
ap.add_argument("--require", action="append", default=[])
ap.add_argument("--sleep", type=float, default=0)
ap.add_argument("--stderr", default="")
args = ap.parse_args()
time.sleep(args.sleep)
cases, failed = [], 0
for i in range(args.tests):
    name = f"test_{i:02d}"
    cases.append(f'<testcase classname="synthetic" name="{name}"/>')
for req in args.require:
    if (Path(args.repo) / req).exists():
        cases.append(f'<testcase classname="synthetic" name={quoteattr("requires " + req)}/>')
    else:
        failed += 1
        cases.append(f'<testcase classname="synthetic" name={quoteattr("requires " + req)}>'
                     f'<failure message="missing {req}"/></testcase>')
Path(args.junit).write_text(f'<testsuite name="synthetic" tests="{len(cases)}" failures="{failed}">'
                            + "".join(cases) + "</testsuite>")
if args.stderr:
    print(args.stderr, file=sys.stderr)
print(f"{len(cases)} tests, {failed} failed")
sys.exit(1 if failed else 0)
