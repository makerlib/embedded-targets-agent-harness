"""AC-001 / AC-002 / AC-003: packaging, CLI framework and config show-effective."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import et_harness
from et_harness.cli.main import STUBS, cli
from et_harness.cli.output import ExitCode


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def invoke(runner: CliRunner, *args: str):
    return runner.invoke(cli, list(args), catch_exceptions=False)


# --- AC-001 ----------------------------------------------------------------------

def test_version_flag(runner: CliRunner) -> None:
    result = invoke(runner, "--version")
    assert result.exit_code == 0
    assert et_harness.__version__ in result.output


def test_installed_console_entrypoint() -> None:
    exe = shutil.which("harness")
    assert exe is not None, "package must be installed with `pip install -e .`"
    result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert result.stdout.strip() == f"harness, version {et_harness.__version__}"


def test_module_entrypoint_matches() -> None:
    from importlib.metadata import version

    assert version("et-harness") == et_harness.__version__


# --- AC-002 ----------------------------------------------------------------------

def stub_argv(group: str | None, name: str, args: tuple[str, ...]) -> list[str]:
    return ([group] if group else []) + [name] + [f"x-{a}" for a in args]


@pytest.mark.parametrize("group,name,args,options,planned", STUBS,
                         ids=[f"{g or ''} {n}".strip() for g, n, *_ in STUBS])
def test_stubs_never_report_success(runner, group, name, args, options, planned) -> None:
    result = invoke(runner, *stub_argv(group, name, args), "--json")
    assert result.exit_code == ExitCode.NOT_IMPLEMENTED
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["reason_codes"] == ["NOT_IMPLEMENTED"]
    assert payload["data"]["planned_feature"] == planned


def test_stub_text_output_and_exit(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["evaluate"])
    assert result.exit_code == ExitCode.NOT_IMPLEMENTED
    assert "NOT_IMPLEMENTED" in result.output


def test_global_json_flag(runner: CliRunner) -> None:
    result = invoke(runner, "--json", "doctor")
    assert json.loads(result.output)["command"] == "doctor"


def test_hook_stub_fails_closed(runner: CliRunner) -> None:
    result = invoke(runner, "hook", "pre-tool", "--json")
    payload = json.loads(result.output)
    assert payload["data"]["decision"] == "DENY"
    assert result.exit_code != 0


def test_all_section_26_commands_registered(runner: CliRunner) -> None:
    spec_26 = [
        "bootstrap", "doctor", "feature create", "feature draft", "feature lint",
        "feature approve", "workflow create", "workflow start", "workflow status",
        "workflow history", "workflow resume", "workflow block", "workflow cancel",
        "persona launch", "handoff submit", "handoff validate", "gate run", "verify",
        "evidence collect", "evidence validate", "evaluate", "close", "package scaffold",
        "package validate", "package test", "package resolve", "config show-effective",
        "hook pre-tool", "enforcement probe", "ci verify",
    ]
    for command in spec_26:
        result = runner.invoke(cli, command.split() + ["--help"])
        assert result.exit_code == 0, command


def test_usage_error_exit_code(runner: CliRunner) -> None:
    assert runner.invoke(cli, ["no-such-command"]).exit_code == ExitCode.USAGE


# --- AC-003 via CLI ----------------------------------------------------------------

@pytest.fixture
def product(tmp_path: Path, fixtures_dir: Path, monkeypatch) -> Path:
    monkeypatch.delenv("ET_HARNESS_ORG_POLICY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ET_HARNESS_USER_CONFIG", raising=False)
    root = tmp_path / "product"
    (root / ".agent-harness").mkdir(parents=True)
    shutil.copy(fixtures_dir / "config" / "project.yaml", root / ".agent-harness" / "project.yaml")
    return root


def test_show_effective_json(runner: CliRunner, product: Path) -> None:
    result = invoke(runner, "config", "show-effective", "--project-root", str(product), "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    setting = payload["data"]["settings"]["rework.repeated_failure_threshold"]
    assert setting == {
        "value": 1, "layer": "project",
        "source": str(product / ".agent-harness" / "project.yaml"),
        "kind": "control", "invariant": False, "tightened_by": [],
    }


def test_show_effective_text(runner: CliRunner, product: Path) -> None:
    result = invoke(runner, "config", "show-effective", "--project-root", str(product))
    assert result.exit_code == 0
    assert "tools.allow_unrestricted_shell = False  <- kernel-invariant" in result.output


def test_show_effective_reports_weakening(runner: CliRunner, product: Path, fixtures_dir: Path) -> None:
    result = invoke(runner, "config", "show-effective", "--project-root", str(product),
                    "--org-policy", str(fixtures_dir / "config" / "org-policy.yaml"),
                    "--user-config", str(fixtures_dir / "config" / "user-weakening.yaml"), "--json")
    assert result.exit_code == ExitCode.REJECTED
    payload = json.loads(result.output)
    assert payload["reason_codes"] == ["CONFIG_WEAKENING_REJECTED"]
    assert payload["data"]["settings"]["enforcement.minimum_write_tier"]["value"] == "ENFORCED"


def test_show_effective_invalid_config(runner: CliRunner, product: Path) -> None:
    (product / ".agent-harness" / "project.yaml").write_text(
        "api_version: embedded-targets.ai/v1\nkind: ProjectConfig\nbogus: 1\n")
    result = invoke(runner, "config", "show-effective", "--project-root", str(product), "--json")
    assert result.exit_code == ExitCode.REJECTED
    payload = json.loads(result.output)
    assert payload["reason_codes"] == ["CONFIG_INVALID"]
    assert payload["data"]["error"]["details"]


def test_show_effective_outside_repository(runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = invoke(runner, "config", "show-effective", "--json")
    assert result.exit_code == ExitCode.BLOCKED
    assert json.loads(result.output)["reason_codes"] == ["REPOSITORY_NOT_FOUND"]


def test_internal_error_is_not_success(runner: CliRunner, product: Path, monkeypatch) -> None:
    import et_harness.config.loader as loader

    def explode(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(loader, "load_configuration", explode)
    result = invoke(runner, "config", "show-effective", "--project-root", str(product), "--json")
    assert result.exit_code == ExitCode.INTERNAL_ERROR
    assert json.loads(result.output)["reason_codes"] == ["INTERNAL_ERROR"]


def test_python_dash_m_style_invocation() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from et_harness.cli.main import main; raise SystemExit(main(['--version']))"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
