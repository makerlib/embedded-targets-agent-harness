"""``harness`` command-line entrypoint (§26).

Every command accepts ``--json`` and exits with a code from ``ExitCode``.
Commands whose HK feature has not been built are registered as stubs that
report ``NOT_IMPLEMENTED`` and exit non-zero; they never report success.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from et_harness import __version__
from et_harness.cli.output import CommandResult, ExitCode, emit, exit_code_for
from et_harness.common.errors import HarnessError
from et_harness.common.reason_codes import ReasonCode

CommandBody = Callable[..., CommandResult]


def harness_command(name: str) -> Callable[[CommandBody], Callable[..., None]]:
    """Wrap a command body: add ``--json``, render the result, set the exit code.

    Domain errors become reason-coded results; unexpected exceptions become
    ``INTERNAL_ERROR`` (exit 5) and are never swallowed into success.
    """

    def decorator(body: CommandBody) -> Callable[..., None]:
        @click.option("--json", "as_json", is_flag=True, help="Emit structured JSON output.")
        @click.pass_context
        @functools.wraps(body)
        def wrapper(ctx: click.Context, as_json: bool, **kwargs: Any) -> None:
            as_json = as_json or bool((ctx.find_root().obj or {}).get("json"))
            try:
                result = body(**kwargs)
            except HarnessError as exc:
                result = CommandResult(
                    name, False, exc.message, {"error": exc.to_dict()}, [exc.code],
                    exit_code_for(exc.code),
                    text_lines=[f"  {d}" for d in exc.details],
                )
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                result = CommandResult(
                    name, False, f"internal error: {type(exc).__name__}: {exc}",
                    {}, [ReasonCode.INTERNAL_ERROR], ExitCode.INTERNAL_ERROR,
                )
            emit(result, as_json)
            ctx.exit(int(result.exit_code))

        return wrapper

    return decorator


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "--version", prog_name="harness")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON for any subcommand.")
@click.pass_context
def cli(ctx: click.Context, as_json: bool) -> None:
    """Embedded Targets agent harness kernel."""
    ctx.obj = {"json": as_json}


# --- config -------------------------------------------------------------------

@cli.group()
def config() -> None:
    """Configuration commands."""


@config.command("show-effective")
@click.option("--project-root", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Product repository root (default: Git root of the current directory).")
@click.option("--org-policy", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="OrganisationPolicy document (default: $ET_HARNESS_ORG_POLICY or project.yaml).")
@click.option("--user-config", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="UserPreferences document (default: $ET_HARNESS_USER_CONFIG or user config dir).")
@harness_command("config show-effective")
def config_show_effective(
    project_root: Path | None, org_policy: Path | None, user_config: Path | None
) -> CommandResult:
    from et_harness.config.loader import load_configuration
    from et_harness.repository.adapters.git_cli import discover_root

    root = project_root or discover_root(Path.cwd())
    loaded = load_configuration(root, org_policy=org_policy, user_config=user_config)
    effective = loaded.effective
    lines = ["Effective configuration (value  <- layer: source):"]
    for key, setting in sorted(effective.settings.items()):
        marker = " [invariant]" if setting.invariant else ""
        tightened = f" (tightened by {', '.join(setting.tightened_by)})" if setting.tightened_by else ""
        lines.append(f"  {key} = {setting.value!r}  <- {setting.layer.value}: {setting.source}{marker}{tightened}")
    for conflict in effective.conflicts:
        lines.append(
            f"  REJECTED {conflict.key} = {conflict.attempted!r} from {conflict.layer.value} "
            f"({conflict.source}): weaker than {conflict.effective!r} set by {conflict.effective_layer.value}"
        )
    for note in loaded.notes:
        lines.append(f"  note: {note}")
    data = effective.to_dict() | {"project_root": str(root), "notes": loaded.notes}
    if effective.ok:
        return CommandResult("config show-effective", True, "configuration resolved", data,
                             text_lines=lines)
    return CommandResult(
        "config show-effective", False,
        f"{len(effective.conflicts)} lower-precedence setting(s) tried to weaken a control",
        data, [ReasonCode.CONFIG_WEAKENING_REJECTED], ExitCode.REJECTED, text_lines=lines,
    )


# --- stubs for later HK features ----------------------------------------------

# (group or None, command, positional argument names, extra options, planned HK feature)
STUBS: list[tuple[str | None, str, tuple[str, ...], tuple[str, ...], str]] = [
    (None, "bootstrap", (), (), "HK-010"),
    (None, "doctor", (), (), "HK-010"),
    ("feature", "draft", (), (), "HK-007"),
    ("workflow", "create", (), (), "HK-003"),
    ("workflow", "start", (), (), "HK-003"),
    ("workflow", "status", (), (), "HK-003"),
    ("workflow", "history", (), (), "HK-003"),
    ("workflow", "resume", (), (), "HK-003"),
    ("workflow", "block", (), (), "HK-003"),
    ("workflow", "cancel", (), (), "HK-003"),
    ("persona", "launch", ("persona",), (), "HK-007"),
    ("handoff", "submit", (), (), "HK-008"),
    ("handoff", "validate", (), (), "HK-008"),
    ("gate", "run", ("gate_id",), (), "HK-004"),
    (None, "verify", (), ("execution_profile",), "HK-004"),
    ("evidence", "collect", (), (), "HK-004"),
    ("evidence", "validate", (), (), "HK-004"),
    (None, "evaluate", (), (), "HK-005"),
    (None, "close", (), (), "HK-012"),
    ("package", "scaffold", (), (), "HK-009"),
    ("package", "validate", (), (), "HK-009"),
    ("package", "test", (), (), "HK-009"),
    ("package", "resolve", (), (), "HK-009"),
    ("hook", "pre-tool", (), (), "HK-010"),
    ("enforcement", "probe", (), (), "HK-010"),
    ("ci", "verify", (), (), "HK-013"),
]

_GROUP_HELP = {
    "feature": "Feature contract commands.",
    "workflow": "Workflow lifecycle commands.",
    "persona": "Agent persona commands.",
    "handoff": "Handoff protocol commands.",
    "gate": "Verification gate commands.",
    "evidence": "Evidence bundle commands.",
    "package": "Plug-in package commands.",
    "hook": "Host hook entrypoints.",
    "enforcement": "Enforcement tier commands.",
    "ci": "GitLab CI commands.",
}


def _make_stub(full_name: str, planned: str) -> CommandBody:
    def body(**_: Any) -> CommandResult:
        data: dict[str, Any] = {"planned_feature": planned}
        if full_name == "hook pre-tool":
            # Fail closed: with no broker yet, every request is denied.
            data["decision"] = "DENY"
        return CommandResult(
            full_name, False,
            f"'{full_name}' is not implemented yet (planned in {planned})",
            data, [ReasonCode.NOT_IMPLEMENTED], ExitCode.NOT_IMPLEMENTED,
        )

    return body


def _register_stubs() -> None:
    groups: dict[str, click.Group] = {
        name: cmd for name, cmd in cli.commands.items() if isinstance(cmd, click.Group)
    }
    for group_name, name, args, options, planned in STUBS:
        full_name = f"{group_name} {name}" if group_name else name
        command_fn: Any = harness_command(full_name)(_make_stub(full_name, planned))
        for option in options:
            command_fn = click.option(f"--{option.replace('_', '-')}", option, default=None)(command_fn)
        for arg in args:
            command_fn = click.argument(arg, required=False)(command_fn)
        command = click.command(name, help=f"Not implemented yet (planned in {planned}).")(command_fn)
        if group_name is None:
            cli.add_command(command)
            continue
        if group_name not in groups:
            groups[group_name] = click.Group(group_name, help=_GROUP_HELP.get(group_name))
            cli.add_command(groups[group_name])
        groups[group_name].add_command(command)


from et_harness.cli import feature as _feature  # noqa: E402

_feature.register(cli, harness_command)
_register_stubs()


def main(argv: list[str] | None = None) -> int:
    try:
        cli.main(args=argv, prog_name="harness", standalone_mode=True)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
