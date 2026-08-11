"""The `netllm ignore` sub-app — `discovery.ignored_urls`.

Same shape as `netllm sources` / `netllm cloud`: a Typer sub-app whose leaves
read the config, mutate one section, run the shared write-path guards, and
save. Nothing here talks to a running agent — the ignore list is config, and
`netllm discover` (or the agent's next scan) is what acts on it.
"""

from __future__ import annotations

from pathlib import Path

import typer
from netllm_core.backend_credentials import (
    add_ignored_url,
    configured_backend_urls,
    normalize_backend_url,
    remove_ignored_url,
)
from netllm_core.config import load_config, save_config
from rich.table import Table

from netllm_cli.commands._common import _config_path_option
from netllm_cli.ui import console, print_error

ignore_app = typer.Typer(
    help="Endpoints local discovery must never register (discovery.ignored_urls)."
)


def _save(cfg, cfg_path: Path, action: str) -> None:
    """Guard + persist, mapping a guard failure to a non-zero exit."""
    from netllm_core.config_guards import ConfigGuardError, apply_config_guards
    from netllm_discovery.lan import own_agent_urls

    try:
        apply_config_guards(cfg, own_agent_urls=own_agent_urls(cfg.agent.listen))
    except ConfigGuardError as exc:
        print_error(f"Could not {action}", str(exc))
        raise typer.Exit(1) from exc
    save_config(cfg, cfg_path)


@ignore_app.command("list")
def ignore_list(config: Path | None = typer.Option(None, "--config")) -> None:
    """Show every ignored URL and whether it is actually in effect."""
    cfg = load_config(_config_path_option(config))
    entries = list(cfg.discovery.ignored_urls)
    if not entries:
        console.print("[dim]No ignored URLs.[/]")
        console.print(
            "[dim]Silence a discovered endpoint: "
            "[cyan]netllm ignore add http://127.0.0.1:8000[/][/]"
        )
        return
    pinned = configured_backend_urls(cfg)
    table = Table(show_header=True, header_style="bold")
    table.add_column("URL")
    table.add_column("Effect")
    for raw in entries:
        norm = normalize_backend_url(raw)
        table.add_row(
            norm or raw,
            "[yellow]overruled by routing.backends[/]"
            if norm in pinned
            else "[green]ignored[/]",
        )
    console.print(table)


@ignore_app.command("add")
def ignore_add(
    url: str = typer.Argument(..., help="Base URL, e.g. http://127.0.0.1:8000"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Stop discovery registering a URL, without pinning it as a backend."""
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    norm = normalize_backend_url(url)
    if not norm:
        print_error("Not a URL", f"Could not read a base URL from {url!r}")
        raise typer.Exit(1)
    if not add_ignored_url(cfg, url):
        console.print(f"[dim]{norm} is already ignored.[/]")
        return
    _save(cfg, cfg_path, f"ignore {norm}")
    console.print(f"[green]Ignoring[/] {norm}.")
    if norm in configured_backend_urls(cfg):
        # Stored, but inert: routing.backends wins. Saying so here is the
        # whole point of surfacing the conflict rather than resolving it
        # silently in either direction.
        console.print(
            "[yellow]Note:[/] this URL is also pinned in [cyan]routing.backends[/], "
            "which wins — it stays routable. Remove the backend override to "
            "make the ignore entry take effect."
        )
    else:
        console.print(
            "[dim]Run [cyan]netllm discover[/] or restart the agent to apply.[/]"
        )


@ignore_app.command("remove")
def ignore_remove(
    url: str = typer.Argument(..., help="Base URL to stop ignoring"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Let discovery see a URL again."""
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    norm = normalize_backend_url(url)
    if not remove_ignored_url(cfg, url):
        print_error(
            "Not ignored",
            f"{norm or url} is not in discovery.ignored_urls",
            hints=["List them: [cyan]netllm ignore list[/]"],
        )
        raise typer.Exit(1)
    _save(cfg, cfg_path, f"stop ignoring {norm}")
    console.print(f"[green]No longer ignoring[/] {norm}.")
    console.print("[dim]Run [cyan]netllm discover[/] to pick it up again.[/]")
