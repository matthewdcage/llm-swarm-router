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


@config_app.command("migrate")
def config_migrate_cmd(
    config: Path | None = typer.Option(None, "--config"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report what would change and write nothing. Exit 0 either way.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Report or apply pending config.toml schema migrations.

    Migrations run automatically on every load, so this command is not
    required for correctness -- it exists so an operator can *see* what a
    version bump will do to their file before the agent does it, and so a
    rolling upgrade can be rehearsed one machine at a time.

    `--dry-run` opens the file read-only and never writes, not even a backup.
    Without it, the config is loaded (migrating it) and saved, which is what
    writes `config.toml.bak-v<n>` alongside the original.
    """
    import json
    import tomllib

    from netllm_core.config_migrations import (
        CURRENT_SCHEMA_VERSION,
        document_schema_version,
        migrate_document,
    )
    from netllm_core.models import load_config, save_config

    cfg_path = _config_path_option(config)

    if not cfg_path.is_file():
        report = {
            "path": str(cfg_path),
            "exists": False,
            "from_version": None,
            "to_version": CURRENT_SCHEMA_VERSION,
            "pending": [],
            "written": False,
        }
        if as_json:
            sys.stdout.write(json.dumps(report))
        else:
            print(f"No config at {cfg_path}; nothing to migrate.")
        return

    try:
        document = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        # Never "repair" a broken file. Say what is wrong and stop, leaving
        # the bytes exactly as they are.
        print(f"cannot read {cfg_path}: {exc}", file=sys.stderr)
        raise typer.Exit(1) from exc

    on_disk = document_schema_version(document)
    result = migrate_document(document)
    pending = [
        {
            "from_version": m.from_version,
            "to_version": m.to_version,
            "notes": m.notes,
        }
        for m in result.applied
    ]

    written = False
    if not dry_run and result.changed:
        # Deliberately load-then-save rather than writing `result.document`:
        # the ordinary write path is the one that has the guards, the
        # unknown-key preservation and the pre-migration backup. A second
        # writer here would be a second thing to keep correct.
        save_config(load_config(cfg_path), cfg_path)
        written = True

    report = {
        "path": str(cfg_path),
        "exists": True,
        "from_version": on_disk,
        "to_version": result.to_version,
        "pending": pending,
        "from_the_future": result.from_the_future,
        "written": written,
    }

    if as_json:
        sys.stdout.write(json.dumps(report))
        return

    print(f"config: {cfg_path}")
    print(f"on disk: schema_version {on_disk}")
    print(f"this build writes: schema_version {CURRENT_SCHEMA_VERSION}")
    if result.from_the_future:
        print(
            "This config was written by a NEWER netllm. Nothing will be "
            "migrated and the stamp will not be lowered; upgrade this machine "
            "before managing the config from here."
        )
        return
    if not pending:
        print("up to date — no migrations pending")
        return
    for step in pending:
        print(f"  {step['from_version']} -> {step['to_version']}: {step['notes']}")
    if dry_run:
        print("dry run — nothing written")
    else:
        print(f"migrated; original copied to {cfg_path.name}.bak-v{on_disk}")


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
