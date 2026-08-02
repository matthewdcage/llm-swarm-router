"""The `netllm sources` sub-app."""

from __future__ import annotations

from pathlib import Path

import typer
from netllm_core.config import load_config, save_config
from rich.table import Table

from netllm_cli.commands._common import _config_path_option
from netllm_cli.ui import console, print_error

sources_app = typer.Typer(help="Manage known-harness source registration.")


@sources_app.command("list")
def sources_list(config: Path | None = typer.Option(None, "--config")) -> None:
    """Show known harnesses: detected on PATH, configured, enabled."""
    from netllm_core.harness_detection import detect
    from netllm_core.known_harnesses import KNOWN_HARNESSES

    cfg = load_config(_config_path_option(config))
    configured = {s.known_id: s for s in cfg.routing.sources if s.known_id}
    table = Table(show_header=True, header_style="bold")
    table.add_column("Harness")
    table.add_column("Detected")
    table.add_column("Configured")
    table.add_column("Enabled")
    for known in KNOWN_HARNESSES:
        source = configured.get(known.id)
        table.add_row(
            known.display_name,
            "[green]yes[/]" if detect(known) else "[dim]no[/]",
            "yes" if source else "[dim]no[/]",
            ("[green]yes[/]" if source.enabled else "[yellow]no[/]") if source else "-",
        )
    console.print(table)


@sources_app.command("toggle")
def sources_toggle(
    id: str = typer.Argument(..., help="Source id, e.g. 'codex'"),
    known: str | None = typer.Option(
        None,
        "--known",
        help="Registry id to link (defaults to <id> if it matches a known harness)",
    ),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Register (if new) and enable a source, or flip its `enabled` state."""
    from netllm_core.config_guards import ConfigGuardError, apply_config_guards
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.known_harnesses import get_known_harness
    from netllm_discovery.lan import own_agent_urls

    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)

    # apply_config_patch's routing.sources merge fully replaces the list
    # from only the entries present in the patch (see
    # netllm_core.config_merge._merge_sources) -- send the complete
    # current list, or every other configured source is silently dropped.
    entries = [s.model_dump(mode="json") for s in cfg.routing.sources]
    existing = next((e for e in entries if e["id"] == id), None)
    if existing is not None:
        existing["enabled"] = not existing["enabled"]
        new_enabled = existing["enabled"]
    else:
        known_id = known or (id if get_known_harness(id) else None)
        entries.append({"id": id, "enabled": True, "known_id": known_id})
        new_enabled = True

    # Same merge + guard pair as `netllm config import` (config_json.py)
    # and the agent's admin save path — see F-02 / F-43 in
    # docs/architecture/07-findings-register.md.
    updated = apply_config_patch(cfg, {"routing": {"sources": entries}})
    try:
        apply_config_guards(
            updated, own_agent_urls=own_agent_urls(updated.agent.listen)
        )
    except ConfigGuardError as exc:
        print_error(f"Could not toggle source {id!r}", str(exc))
        raise typer.Exit(1) from exc

    save_config(updated, cfg_path)
    state = "Enabled" if new_enabled else "Disabled"
    console.print(f"[green]{state}[/] source {id!r}.")
    console.print(
        "[dim]Restart or re-apply config for the running agent to pick this up.[/]"
    )
