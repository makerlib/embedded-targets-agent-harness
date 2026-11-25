"""`process.command`: run the argv fixed in a gate definition (§9.14).

This is not a shell. The command line comes only from the versioned gate
definition; nothing an agent passes at run time reaches it. Only these
placeholders are substituted inside argv items, cwd and env values:

    {repo} {attempt_dir} {evidence_dir} {python}

stdout and stderr go to separate files, are redacted and limited in size. On
timeout or cancellation the whole process tree is killed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from et_harness.common.redaction import DEFAULT_PATTERNS, Redactor
from et_harness.gates.junit import parse_junit
from et_harness.tools.base import ToolContext, ToolDescriptor, ToolResult

PLACEHOLDERS = ("repo", "attempt_dir", "evidence_dir", "python")
POLL_SECONDS = 0.05


def substitute(value: str, context: ToolContext) -> str:
    values = {"repo": str(context.repo_root), "attempt_dir": str(context.attempt_dir),
              "evidence_dir": str(context.evidence_dir), "python": sys.executable}
    for key, replacement in values.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    except ProcessLookupError:
        pass
    process.wait()


def _finalise_stream(raw: Path, final: Path, limit: int, redactor: Redactor) -> bool:
    data = raw.read_bytes()
    raw.unlink()
    truncated = len(data) > limit
    if truncated:
        data = data[:limit]
    text = redactor.redact_text(data.decode("utf-8", "replace"))
    if truncated:
        text += f"\n[output truncated at {limit} bytes]\n"
    final.write_text(text, encoding="utf-8")
    return truncated


class ProcessCommandTool:
    def describe(self) -> ToolDescriptor:
        return ToolDescriptor("process.command", "1.0.0", "B", "process",
                              "Runs the argv fixed in the gate definition, without a shell.")

    def validate_inputs(self, inputs: dict[str, Any]) -> list[str]:
        errors = []
        argv = inputs.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            errors.append("inputs.argv must be a non-empty list of strings")
        for key in ("cwd", "junit"):
            if key in inputs and not isinstance(inputs[key], str):
                errors.append(f"inputs.{key} must be a string")
        env = inputs.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            errors.append("inputs.env must map strings to strings")
        unknown = set(inputs) - {"argv", "cwd", "env", "junit"}
        if unknown:
            errors.append(f"unknown inputs {sorted(unknown)}")
        return errors

    def execute(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult:
        context.attempt_dir.mkdir(parents=True, exist_ok=True)
        argv = [substitute(a, context) for a in inputs["argv"]]
        cwd = Path(substitute(inputs.get("cwd", "{repo}"), context))
        env = dict(os.environ)
        env.update({k: substitute(v, context) for k, v in (inputs.get("env") or {}).items()})
        raw_out = context.attempt_dir / ".stdout.raw"
        raw_err = context.attempt_dir / ".stderr.raw"
        start = time.monotonic()
        timed_out = cancelled = False
        try:
            with open(raw_out, "wb") as out, open(raw_err, "wb") as err:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err,
                                           stdin=subprocess.DEVNULL, start_new_session=(os.name == "posix"))
                while process.poll() is None:
                    if time.monotonic() - start > context.timeout_seconds:
                        timed_out = True
                        _kill_tree(process)
                        break
                    if context.cancel.cancelled:
                        cancelled = True
                        _kill_tree(process)
                        break
                    time.sleep(POLL_SECONDS)
        except OSError as exc:
            for raw in (raw_out, raw_err):
                raw.unlink(missing_ok=True)
            return ToolResult(None, int((time.monotonic() - start) * 1000), error=f"cannot start {argv[0]}: {exc}")
        duration = int((time.monotonic() - start) * 1000)
        redactor = Redactor(context.redaction_patterns or DEFAULT_PATTERNS)
        stdout_path = context.attempt_dir / "stdout.txt"
        stderr_path = context.attempt_dir / "stderr.txt"
        out_trunc = _finalise_stream(raw_out, stdout_path, context.output_limit_bytes, redactor)
        err_trunc = _finalise_stream(raw_err, stderr_path, context.output_limit_bytes, redactor)
        result = ToolResult(
            exit_code=None if (timed_out or cancelled) else process.returncode,
            duration_ms=duration, timed_out=timed_out, cancelled=cancelled,
            stdout_path=stdout_path, stderr_path=stderr_path,
            stdout_truncated=out_trunc, stderr_truncated=err_trunc,
            evidence={"stdout": stdout_path, "stderr": stderr_path},
        )
        if "junit" in inputs:
            junit = Path(substitute(inputs["junit"], context))
            if junit.is_file():
                counts, failing = parse_junit(junit)
                if counts is not None:
                    result.evidence["junit-results"] = junit
                    result.test_counts = counts
                    result.failing_tests = failing
        return result
