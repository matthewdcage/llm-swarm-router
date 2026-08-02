"""The `netllm config` sub-app (JSON import/export for the settings UI)."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from netllm_cli.commands._common import _config_path_option
from netllm_cli.config_json import emit_export, read_import

config_app = typer.Typer(help="Import/export config.toml as JSON (settings UI).")


@config_app.command("export")
def config_export(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Write full config as JSON to stdout."""
    emit_export(_config_path_option(config))


@config_app.command("schema")
def config_schema_cmd(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Write the config form schema as JSON to stdout (macOS Settings UI).

    Same document as GET /netllm/v1/config/schema, reachable here without
    a running agent — the macOS app edits config.toml via this CLI even
    when the agent process is stopped. See
    docs/config-schema-rewrite-plan.md.

    `--config` is accepted (and ignored — the schema describes shape, not
    a specific file's values) only because CLIRunner.run() unconditionally
    appends `--config <path>` to every CLI invocation it makes; omitting
    the option here makes Typer reject the call.
    """
    import json

    from netllm_core.config_schema import config_schema_document

    del config
    sys.stdout.write(json.dumps(config_schema_document()))


@config_app.command("import")
def config_import_cmd(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Read JSON from stdin and save to config.toml."""
    from netllm_core.config_guards import ConfigGuardError

    try:
        read_import(_config_path_option(config))
    except ConfigGuardError as exc:
        # stdout is the machine-readable channel here (the macOS Settings
        # app parses {"path": ...} from it), so a rejected save must report
        # on stderr and exit non-zero -- that is what CLIRunner surfaces as
        # the user-visible error in the app, and it mirrors the dashboard's
        # HTTP 400 for the same config.
        print(f"config import rejected: {exc}", file=sys.stderr)
        raise typer.Exit(1) from exc
