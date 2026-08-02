"""`netllm serve` plus the start/stop/restart/config-edit lifecycle commands."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import typer
from netllm_core.config import (
    ensure_lan_mesh_defaults,
    is_lan_listen,
    save_config,
)
from netllm_core.models import format_listen, split_listen
from netllm_discovery.local import scan_local_providers
from rich.panel import Panel

from netllm_cli.commands._common import _config_path_option, _require_config
from netllm_cli.install import (
    find_repo_root,
    global_cli_on_path,
    global_netllm_installed,
    listen_is_loopback,
    path_export_line,
    suggested_cli,
)
from netllm_cli.lifecycle import control_socket_path, lifecycle_command
from netllm_cli.ui import (
    console,
    listen_urls,
    mdns_available,
    print_endpoints_table,
    print_env_block,
    print_error,
    print_heading,
    print_next_steps,
    print_warnings,
)


def serve(
    config: Path | None = typer.Option(None, "--config"),
    host: str | None = typer.Option(None, "--host", help="Override listen host"),
    port: int | None = typer.Option(None, "--port", help="Override listen port"),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Stop an existing netllm agent on this port and start fresh",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Minimal startup output (logs only)"
    ),
) -> None:
    """Start the netllm agent (foreground)."""
    from netllm_discovery.runtime import (
        check_listen_port,
        format_port_conflict_message,
        port_conflict_hints,
        stop_netllm_on_port,
    )

    from netllm_cli.install_detect import is_menubar_supervised

    cfg_path = _config_path_option(config)
    cfg = _require_config(cfg_path)

    if ensure_lan_mesh_defaults(cfg):
        save_config(cfg, cfg_path)

    if host or port:
        current_host, current_port = split_listen(cfg.agent.listen)
        cfg.agent.listen = format_listen(host or current_host, port or current_port)

    conflict = check_listen_port(cfg)
    port_cleared = False
    if conflict:
        replace_cmd = suggested_cli("serve --replace")
        if (
            conflict.occupied_by_netllm
            and conflict.agent_id
            and conflict.agent_id == cfg.agent.agent_id
        ):
            if replace:
                if control_socket_path().exists() and not is_menubar_supervised():
                    if not quiet:
                        console.print(
                            "[yellow]Restarting agent via llm-swarm-router app…[/]"
                        )
                    raise typer.Exit(
                        lifecycle_command("restart", timeout=60.0, no_wait=quiet)
                    )
                if stop_netllm_on_port(conflict.port):
                    port_cleared = check_listen_port(cfg) is None
                    if not port_cleared:
                        still = check_listen_port(cfg)
                        if still is not None:
                            conflict = still
                        print_error(
                            "Could not free port",
                            format_port_conflict_message(conflict),
                            hints=port_conflict_hints(
                                conflict, replace_flag=replace_cmd
                            ),
                        )
                        raise typer.Exit(1)
                else:
                    print_error(
                        "Could not restart agent",
                        "Same agent is running but could not stop it for --replace.",
                        hints=[
                            suggested_cli("restart"),
                            "Or use Settings → Restart Agent in the menubar app",
                        ],
                    )
                    raise typer.Exit(1)
            else:
                if not quiet:
                    console.print(
                        Panel(
                            f"[green]netllm agent already running[/]\n"
                            f"  agent_id: {conflict.agent_id}\n"
                            f"  url: {conflict.url}\n"
                            f"  pid: {conflict.pid or 'unknown'}\n\n"
                            f"  Reload config: [cyan]{suggested_cli('restart')}[/]",
                            border_style="green",
                        )
                    )
                raise typer.Exit(0)
        if not port_cleared and replace and conflict.occupied_by_netllm:
            if not quiet:
                console.print(
                    f"[yellow]Stopping existing netllm agent on port "
                    f"{conflict.port}…[/]"
                )
            if not stop_netllm_on_port(conflict.port):
                print_error(
                    "Could not free port",
                    format_port_conflict_message(conflict),
                    hints=port_conflict_hints(conflict, replace_flag=replace_cmd),
                )
                raise typer.Exit(1)
        elif not port_cleared:
            print_error(
                "Port already in use",
                format_port_conflict_message(conflict),
                hints=port_conflict_hints(conflict, replace_flag=replace_cmd),
            )
            raise typer.Exit(1)

    base, lan_base = listen_urls(cfg.agent.listen)
    warnings: list[str] = []

    if is_lan_listen(cfg.agent.listen) and not cfg.swarm.cluster_token:
        warnings.append(
            "LAN swarm is open (no cluster token). Trusted home LAN is fine; "
            "run [cyan]netllm swarm-token[/] to require a token on other machines."
        )

    results = asyncio.run(scan_local_providers(cfg))
    online = [r for r in results if r.get("status") == "online"]

    if cfg.swarm.mdns and cfg.agent.advertise and not mdns_available():
        warnings.append(
            "Swarm mDNS unavailable — reinstall netllm ([cyan]uv sync[/]). "
            "Static peers in swarm.peers still work."
        )

    if not quiet:
        print_heading(
            "Starting netllm agent",
            f"role={cfg.agent.role}  strategy={cfg.routing.default_strategy}",
        )
        summary = f"[bold]Listen[/]  {base}\n"
        if lan_base:
            summary += f"[bold]LAN[/]     {lan_base}\n"
        summary += (
            f"[bold]Config[/]  {cfg_path}\n[bold]Backends[/] {len(online)} online"
        )
        if online:
            names = ", ".join(r.get("name", "?") for r in online)
            summary += f" ({names})"
        else:
            summary += " [yellow]— start oMLX/Ollama/LM Studio, then refresh[/]"
        console.print(Panel(summary, border_style="cyan"))
        print_endpoints_table(base)
        print_env_block(base)

        while_steps: list[tuple[str, str]] = [
            (suggested_cli("status"), "New terminal — health + backends"),
            (suggested_cli("models"), "List all routed models"),
            (f"curl -sf {base}/health", "Quick health check"),
        ]
        if listen_is_loopback(cfg.agent.listen):
            while_steps.insert(
                0,
                (
                    suggested_cli("serve --host 0.0.0.0"),
                    "Restart for LAN/swarm — other machines + mDNS can reach you",
                ),
            )
        else:
            while_steps.append(
                (suggested_cli("peers"), "Find other netllm agents on the LAN"),
            )

        repo = find_repo_root()
        if repo:
            while_steps.append(
                (
                    f"{repo / 'netllm'} status",
                    "Works in any terminal — no global PATH needed",
                ),
            )
        elif not global_cli_on_path() and global_netllm_installed():
            while_steps.append(
                (path_export_line(), "Then use netllm in other terminals"),
            )

        print_next_steps(while_steps, title="While the agent runs")
        print_warnings(warnings)
        console.print(
            "[dim]Press Ctrl+C to stop. "
            "Dashboard: [cyan]" + base + "/ui/[/] · API help JSON at [cyan]/[/][/]\n"
        )
    elif warnings:
        print_warnings(warnings)

    import logging
    import logging.handlers

    import uvicorn
    from netllm_agent.app import create_app

    log_dir = cfg.resolved_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"
    # Rotate: this is the file every troubleshooting doc points users at and
    # the one GET /netllm/v1/logs tails. A plain FileHandler grew it without
    # bound for the life of the agent (F-15).
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(logger_name).addHandler(file_handler)

    fastapi_app = create_app(cfg, config_path=cfg_path)
    host_part, port_part = split_listen(cfg.agent.listen)
    uvicorn.run(
        fastapi_app,
        host=host_part or "127.0.0.1",
        port=port_part,
        log_level="info",
    )


def start(
    timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for agent"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after dispatch"),
) -> None:
    """Start the netllm agent (menubar app, Homebrew, systemd, or Windows service)."""
    raise typer.Exit(lifecycle_command("start", timeout=timeout, no_wait=no_wait))


def stop() -> None:
    """Stop the netllm agent (menubar app, Homebrew, systemd, or Windows service)."""
    raise typer.Exit(lifecycle_command("stop"))


def restart(
    timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for agent"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after dispatch"),
) -> None:
    """Restart the netllm agent (menubar app, Homebrew, systemd, or Windows service)."""
    raise typer.Exit(lifecycle_command("restart", timeout=timeout, no_wait=no_wait))


def config_edit(
    path: Path | None = typer.Option(None, "--config"),
) -> None:
    """Open config in $EDITOR."""
    cfg_path = _config_path_option(path)
    if not cfg_path.is_file():
        print_error(
            "Config not found",
            f"No file at {cfg_path}",
            hints=["Run [cyan]netllm init[/] first"],
        )
        raise typer.Exit(1)
    editor = os.environ.get("EDITOR", "nano")
    console.print(f"[dim]Opening {cfg_path} with {editor}[/]")
    subprocess.run([editor, str(cfg_path)], check=False)
