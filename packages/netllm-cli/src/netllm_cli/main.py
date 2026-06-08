"""User-facing CLI for netllm."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import typer
from netllm_core.config import default_config_path, load_config, save_config
from netllm_core.models import NetllmConfig
from netllm_discovery.lan import (
    discover_lan_agents,
    models_from_status,
)
from netllm_discovery.local import scan_local_providers
from rich.panel import Panel
from rich.table import Table

from netllm_cli.install import (
    ensure_global_cli,
    find_repo_root,
    global_cli_on_path,
    global_netllm_binary,
    global_netllm_installed,
    listen_is_loopback,
    path_export_line,
    print_path_notice,
    suggested_cli,
)
from netllm_cli.config_json import emit_export, read_import
from netllm_cli.lifecycle import lifecycle_command
from netllm_cli.ui import (
    agent_unreachable_message,
    console,
    inference_status_style,
    listen_url,
    listen_urls,
    mdns_available,
    models_table,
    offline_provider_hints,
    peers_table,
    print_endpoints_table,
    print_env_block,
    print_error,
    print_heading,
    print_next_steps,
    print_warnings,
    providers_table,
)

__version__ = "0.2.0"

app = typer.Typer(
    name="netllm",
    help="Network LLM router — discover, route, and load-balance local inference.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"netllm [cyan]{__version__}[/]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Network LLM router CLI."""


def _config_path_option(path: Path | None) -> Path:
    return path or default_config_path()


def _require_config(cfg_path: Path) -> NetllmConfig:
    if not cfg_path.is_file():
        print_error(
            "Config not found",
            f"No config at [cyan]{cfg_path}[/]",
            hints=[
                "Create one: [cyan]netllm init[/]",
                "Custom path: [cyan]netllm init --config /path/to/config.toml[/]",
            ],
        )
        raise typer.Exit(1)
    return load_config(cfg_path)


@app.command()
def init(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
    global_cli: bool = typer.Option(
        True,
        "--global-cli/--no-global-cli",
        help="Install `netllm` globally via uv and update shell PATH",
    ),
) -> None:
    """Write default config, scan local providers, optionally install global CLI."""
    if global_cli and find_repo_root() is not None:
        installed = ensure_global_cli()
        print_path_notice(installed=installed)

    cfg_path = _config_path_option(config)
    if cfg_path.is_file() and not force:
        print_error(
            "Config already exists",
            f"[cyan]{cfg_path}[/] is already present.",
            hints=[
                "Scan providers: [cyan]netllm discover[/]",
                "Overwrite: [cyan]netllm init --force[/]",
                "Edit config: [cyan]netllm config-edit[/]",
            ],
        )
        raise typer.Exit(0)

    cfg = NetllmConfig()
    save_config(cfg, cfg_path)
    base = listen_url(cfg.agent.listen)

    print_heading("netllm initialized", f"Config written to {cfg_path}")

    results = asyncio.run(scan_local_providers(cfg))
    online = [r for r in results if r.get("status") == "online"]
    offline = [r for r in results if r.get("status") != "online"]

    if results:
        providers_table(results, title="Local inference servers")
    if offline:
        print_warnings(offline_provider_hints(results))

    if online:
        total_models = sum(len(r.get("models") or []) for r in online)
        console.print(
            f"\n[green]Ready:[/] {len(online)} provider(s), "
            f"{total_models} model(s) reachable."
        )
    else:
        print_error(
            "No providers online",
            "netllm could not reach oMLX, Ollama, or LM Studio on this machine.",
            hints=offline_provider_hints(results)
            + [
                "Start a server, then run [cyan]netllm discover[/]",
                "You can still run [cyan]netllm serve[/] — backends appear when online",
            ],
        )

    print_next_steps(
        [
            (suggested_cli("serve"), "Start the router (this terminal)"),
            (
                suggested_cli("serve --host 0.0.0.0"),
                "LAN + swarm — other machines can reach this agent",
            ),
            (f"export OPENAI_BASE_URL={base}/v1", "Point OpenAI clients at netllm"),
            (suggested_cli("status"), "New terminal — backends, peers, health"),
            (suggested_cli("models"), "List all routed models"),
        ]
    )


@app.command()
def install(
    repo: Path | None = typer.Option(
        None,
        "--repo",
        help="Path to llm-swarm-router checkout (default: auto-detect)",
    ),
) -> None:
    """Install global `netllm` CLI and register ~/.local/bin in your shell profile."""
    root = repo or find_repo_root()
    if root is None:
        print_error(
            "Repo not found",
            "Run from the netllm clone or pass --repo /path/to/llm-swarm-router",
            hints=[
                "Dev without global install: [cyan]./netllm status[/] from repo root",
                "Or: [cyan]uv run netllm status[/]",
            ],
        )
        raise typer.Exit(1)

    print_heading("Installing global netllm CLI", str(root))
    installed = ensure_global_cli(root)
    print_path_notice(installed=installed)
    if not global_cli_on_path():
        console.print(
            "\n[dim]Tip:[/] From the repo, [cyan]./netllm[/] works immediately "
            "without PATH changes."
        )


@app.command()
def discover(
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Scan localhost for oMLX, Ollama, and LM Studio."""
    cfg = load_config(_config_path_option(config))
    results = asyncio.run(scan_local_providers(cfg))

    if as_json:
        typer.echo(json.dumps({"providers": results}))
        return

    if not results:
        print_error(
            "Nothing to scan",
            "No providers enabled in config.",
            hints=[
                "Check [cyan]discovery.providers[/] in config.toml",
                "Run [cyan]netllm init[/] to create a default config",
            ],
        )
        raise typer.Exit(1)

    providers_table(results, title="Local LLM providers")
    online = sum(1 for r in results if r.get("status") == "online")
    console.print(f"\n[dim]{online}/{len(results)} online[/]")

    hints = offline_provider_hints(results)
    if hints:
        print_warnings(hints)
    if online == 0:
        raise typer.Exit(1)


@app.command()
def models(
    config: Path | None = typer.Option(None, "--config"),
    url: str | None = typer.Option(
        None, "--url", help="Agent base URL (default: config listen)"
    ),
    local: bool = typer.Option(
        False, "--local", help="List models from local providers only (no agent)"
    ),
    lan: bool = typer.Option(
        False,
        "--lan",
        help="Include models from other netllm agents on the LAN",
    ),
    subnet_scan: bool = typer.Option(
        False,
        "--subnet-scan",
        help="With --lan: probe /24 for agents when mDNS is blocked",
    ),
) -> None:
    """List available models (local providers, agent, or LAN swarm)."""
    cfg = load_config(_config_path_option(config))
    rows: list[dict[str, str]] = []

    if local:
        results = asyncio.run(scan_local_providers(cfg))
        for r in results:
            if r.get("status") != "online":
                continue
            host = r.get("name", "local")
            provider = r.get("id", "?")
            base = r.get("base_url", "")
            for mid in r.get("models") or []:
                rows.append({
                    "model": mid,
                    "provider": provider,
                    "host": host,
                    "scope": "local",
                    "backend": base,
                })
        if not rows:
            print_error(
                "No models found",
                "No online local inference servers with models.",
                hints=offline_provider_hints(results)
                + ["Run [cyan]netllm discover[/] to inspect providers"],
            )
            raise typer.Exit(1)
        models_table(rows, title="Local provider models")
        console.print(f"\n[dim]{len(rows)} model(s)[/]")
        return

    if lan:
        warnings: list[str] = []
        if cfg.swarm.mdns and not mdns_available():
            warnings.append(
            "mDNS not available — reinstall: [cyan]uv sync[/] or "
            "[cyan]uv tool install --editable . --reinstall[/]"
            )
        peers = asyncio.run(
            discover_lan_agents(
                cfg,
                use_mdns=True,
                use_subnet=subnet_scan,
            )
        )
        if warnings:
            print_warnings(warnings)
        if not peers:
            print_error(
                "No LAN agents found",
                "Could not find other netllm agents on your network.",
                hints=[
                    "Ensure peers run [cyan]netllm serve[/] with "
                    "[cyan]agent.advertise = true[/]",
                    "Same Wi‑Fi/VLAN; mDNS may be blocked on guest networks",
                    "Try subnet scan: [cyan]netllm peers --subnet-scan[/]",
                    "Add manually: [cyan]swarm.peers[/] in config.toml",
                ],
            )
            raise typer.Exit(1)
        for peer in peers:
            rows.extend(models_from_status(peer))
        models_table(rows, title="Models on LAN agents")
        console.print(
            f"\n[dim]{len(rows)} model(s) across {len(peers)} agent(s)[/]"
        )
        print_next_steps(
            [
                ("netllm peers", "List agents without model detail"),
                (
                    f"netllm models --url {listen_url(cfg.agent.listen)}",
                    "Models via your local agent (merged routing)",
                ),
            ],
            title="Next",
        )
        return

    base = url or listen_url(cfg.agent.listen)
    try:
        with httpx.Client(timeout=10.0) as client:
            status_resp = client.get(f"{base.rstrip('/')}/netllm/v1/status")
            status_resp.raise_for_status()
            status = status_resp.json()
            rows = models_from_status(status)
            if not rows:
                models_resp = client.get(f"{base.rstrip('/')}/v1/models")
                models_resp.raise_for_status()
                for item in models_resp.json().get("data") or []:
                    rows.append({
                        "model": item.get("id", ""),
                        "provider": item.get("owned_by", "?"),
                        "host": status.get("hostname", "agent"),
                        "scope": "routed",
                        "backend": "—",
                    })
    except Exception as exc:
        msg, hints = agent_unreachable_message(base, exc)
        print_error("Agent unreachable", msg, hints=hints)
        raise typer.Exit(1) from exc

    if not rows:
        print_warnings([
            "Agent is up but no models registered — start oMLX/Ollama on this host",
            "Run [cyan]netllm discover[/] then restart [cyan]netllm serve[/]",
        ])
        raise typer.Exit(1)

    models_table(rows, title=f"Routed models ({base})")
    console.print(f"\n[dim]{len(rows)} model(s)[/]")
    print_next_steps(
        [
            ("netllm models --local", "Models on this machine only"),
            ("netllm models --lan", "Models on other LAN agents"),
            ("netllm peers --subnet-scan", "Find agents when mDNS is blocked"),
        ],
        title="See also",
    )


@app.command()
def peers(
    config: Path | None = typer.Option(None, "--config"),
    mdns: bool = typer.Option(True, "--mdns/--no-mdns", help="Browse mDNS"),
    subnet_scan: bool = typer.Option(
        False,
        "--subnet-scan",
        help="Probe local /24 for agents on :11400 (slow; use if mDNS blocked)",
    ),
    timeout: float = typer.Option(
        3.0, "--timeout", "-t", help="mDNS browse seconds"
    ),
    save: bool = typer.Option(
        False,
        "--save",
        help="Append discovered peer URLs to swarm.peers in config",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Find netllm agents on the local network."""
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    warnings: list[str] = []

    if mdns and cfg.swarm.mdns and not mdns_available():
        warnings.append(
            "mDNS unavailable — [cyan]uv sync[/] or use "
            "[cyan]--subnet-scan[/]"
        )
        mdns = False

    if subnet_scan and not cfg.swarm.subnet_cidrs:
        from netllm_discovery.lan import default_subnet_cidrs

        cidrs = default_subnet_cidrs()
        if cidrs:
            warnings.append(f"Scanning {', '.join(cidrs)} for agents on :11400")

    peers_found = asyncio.run(
        discover_lan_agents(
            cfg,
            use_mdns=mdns,
            use_subnet=subnet_scan,
            timeout_s=timeout,
        )
    )

    if as_json:
        typer.echo(json.dumps({"peers": peers_found, "warnings": warnings}))
        return

    print_heading(
        "LAN agent discovery",
        "Finding other netllm routers on your network",
    )
    if warnings:
        print_warnings(warnings)

    if not peers_found:
        print_error(
            "No peers found",
            "No other netllm agents responded on the LAN.",
            hints=[
                "On each machine: [cyan]netllm init && netllm serve[/]",
                "Bind for LAN: [cyan]netllm serve --host 0.0.0.0[/]",
                "Enable advertise: [cyan]agent.advertise = true[/] in config",
                "Guest Wi‑Fi often blocks mDNS — try [cyan]--subnet-scan[/]",
                "Manual: add URLs under [cyan]swarm.peers[/] in config.toml",
            ],
        )
        raise typer.Exit(1)

    peers_table(peers_found, title="LAN netllm agents")

    if save:
        cfg = load_config(cfg_path)
        existing = {u.rstrip("/") for u in cfg.swarm.peers}
        added = 0
        for p in peers_found:
            url = p.get("listen_url", "").rstrip("/")
            if url and url not in existing:
                cfg.swarm.peers.append(url)
                existing.add(url)
                added += 1
        if added:
            save_config(cfg, cfg_path)
            console.print(f"\n[green]Saved {added} peer(s)[/] → {cfg_path}")
        else:
            console.print("\n[dim]All discovered peers already in config[/]")

    print_next_steps(
        [
            ("netllm models --lan", "List models on discovered agents"),
            ("netllm serve", "Restart agent to merge remote backends"),
            ("netllm status", "Backends + peers while agent runs"),
        ],
    )


@app.command("env")
def env_shell() -> None:
    """Print export PATH snippet for shells where `netllm` is not found."""
    if global_cli_on_path():
        console.print("[dim]# netllm is already on PATH in this terminal[/]")
    elif global_netllm_installed():
        console.print(path_export_line())
    else:
        print_error(
            "Global CLI not installed",
            f"No binary at {global_netllm_binary()}",
            hints=[
                "From repo: [cyan]./netllm install[/]",
                "Or use [cyan]./netllm models[/] without global install",
            ],
        )
        raise typer.Exit(1)


@app.command()
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

    cfg_path = _config_path_option(config)
    cfg = _require_config(cfg_path)

    if host or port:
        h = host or cfg.agent.listen.split(":")[0]
        p = port or int(cfg.agent.listen.split(":")[-1])
        cfg.agent.listen = f"{h}:{p}"

    conflict = check_listen_port(cfg)
    if conflict:
        replace_cmd = suggested_cli("serve --replace")
        if (
            conflict.occupied_by_netllm
            and conflict.agent_id
            and conflict.agent_id == cfg.agent.agent_id
        ):
            if not quiet:
                console.print(
                    Panel(
                        f"[green]netllm agent already running[/]\n"
                        f"  agent_id: {conflict.agent_id}\n"
                        f"  url: {conflict.url}\n"
                        f"  pid: {conflict.pid or 'unknown'}",
                        border_style="green",
                    )
                )
            raise typer.Exit(0)
        if replace and conflict.occupied_by_netllm:
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
        else:
            print_error(
                "Port already in use",
                format_port_conflict_message(conflict),
                hints=port_conflict_hints(conflict, replace_flag=replace_cmd),
            )
            raise typer.Exit(1)

    base, lan_base = listen_urls(cfg.agent.listen)
    warnings: list[str] = []

    if cfg.agent.listen.startswith("0.0.0.0"):
        warnings.append(
            "Listening on 0.0.0.0 — the agent is reachable from your LAN. "
            "Set swarm.cluster_token if others can join the swarm."
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
            f"[bold]Config[/]  {cfg_path}\n"
            f"[bold]Backends[/] {len(online)} online"
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
            "Opening [cyan]/[/] in a browser shows a help JSON — not an error.[/]\n"
        )
    elif warnings:
        for w in warnings:
            console.print(f"[yellow]note:[/] {w}", file=sys.stderr)

    import uvicorn
    from netllm_agent.app import create_app

    fastapi_app = create_app(cfg)
    host_part, _, port_part = cfg.agent.listen.partition(":")
    uvicorn.run(
        fastapi_app,
        host=host_part or "127.0.0.1",
        port=int(port_part or 11400),
        log_level="info",
    )


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config"),
    url: str | None = typer.Option(None, "--url", help="Agent base URL"),
) -> None:
    """Show agent, backends, and swarm peers."""
    cfg = load_config(_config_path_option(config))
    base = url or listen_url(cfg.agent.listen)

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base.rstrip('/')}/netllm/v1/status")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        msg, hints = agent_unreachable_message(base, exc)
        print_error("Agent unreachable", msg, hints=hints)
        raise typer.Exit(1) from exc

    info = (
        f"[bold]Agent[/]   {data.get('agent_id')} ({data.get('hostname')})\n"
        f"[bold]Role[/]    {data.get('role')}\n"
        f"[bold]Strategy[/] {data.get('routing_strategy')}\n"
        f"[bold]URL[/]     {data.get('listen_url')}"
    )
    console.print(Panel(info, title="netllm agent", border_style="green"))

    backends = data.get("backends") or []
    if backends:
        table = Table(title="Backends", show_header=True, header_style="bold")
        table.add_column("Provider")
        table.add_column("URL")
        table.add_column("Scope")
        table.add_column("Health")
        table.add_column("Models")
        table.add_column("In-flight")
        for b in backends:
            h = b.get("health", {})
            scope = "local" if b.get("local") else "remote"
            health = h.get("status", "?")
            style = "green" if health == "online" else "red"
            table.add_row(
                b.get("provider", ""),
                b.get("base_url", ""),
                scope,
                f"[{style}]{health}[/{style}]",
                str(h.get("model_count", 0)),
                str(b.get("in_flight", 0)),
            )
        console.print(table)
    else:
        print_warnings([
            "No backends registered — run [cyan]netllm discover[/] on this host",
        ])

    peers = data.get("peers") or []
    if peers:
        console.print(f"\n[bold]Swarm peers[/] ({len(peers)})")
        for p in peers:
            console.print(
                f"  • {p.get('agent_id')} @ {p.get('listen_url')} "
                f"[dim]({p.get('role')})[/]"
            )
    elif cfg.swarm.mdns or cfg.swarm.peers:
        lan_hint = (
            f"{suggested_cli('serve --host 0.0.0.0')} on each machine, then "
            f"{suggested_cli('peers')}"
        )
        print_warnings([
            f"No swarm peers yet — {lan_hint}, or add swarm.peers in config",
            f"Gateway mode: [cyan]{suggested_cli('gateway')}[/] then restart serve",
        ])


async def _test_anthropic_agent(cfg: NetllmConfig, *, model: str | None) -> None:
    base = listen_url(cfg.agent.listen)
    test_model = model
    if not test_model:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base}/v1/models", timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json().get("data") or []
                    if data:
                        test_model = data[0].get("id")
        except httpx.HTTPError:
            pass
    if not test_model:
        print_error(
            "No model for Anthropic test",
            "Pass --model or ensure the agent lists models.",
            hints=[
                f"Start agent: [cyan]{suggested_cli('serve')}[/]",
                "List models: [cyan]netllm models[/]",
            ],
        )
        raise typer.Exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "netllm-local")
    payload = {
        "model": test_model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    headers = {"x-api-key": api_key}
    console.print(f"\n[bold]Testing Anthropic Messages API[/] via {base}")
    console.print(f"  [dim]POST /v1/messages[/]  model={test_model}")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base}/v1/messages",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        console.print(f"  HTTP {resp.status_code}  ({latency_ms}ms)")
        if resp.status_code == 200:
            body = resp.json()
            text = ""
            for block in body.get("content") or []:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            console.print(f"  Reply: {text[:80]!r}")
        else:
            print_error(
                "Anthropic probe failed",
                resp.text[:200],
                hints=[
                    f"Agent running? curl -sf {base}/health",
                    "Cloud failover needs ANTHROPIC_API_KEY in env",
                ],
            )
            raise typer.Exit(1)
    except httpx.HTTPError as exc:
        print_error(
            "Agent unreachable",
            str(exc),
            hints=[agent_unreachable_message(base)],
        )
        raise typer.Exit(1) from exc


@app.command()
def test(
    config: Path | None = typer.Option(None, "--config"),
    backend: str | None = typer.Option(None, "--backend", help="Specific base URL"),
    model: str | None = typer.Option(None, "--model", help="Model to test"),
    api: str = typer.Option(
        "openai",
        "--api",
        help="API surface: openai (local backends) or anthropic (agent /v1/messages)",
    ),
) -> None:
    """Diagnose a backend (models list + 1-token latency)."""
    cfg = load_config(_config_path_option(config))

    async def run() -> None:
        if api == "anthropic":
            await _test_anthropic_agent(cfg, model=model)
            return

        from netllm_core.health import diagnose_backend

        if backend:
            targets = [{"base_url": backend, "name": "custom"}]
        else:
            targets = await scan_local_providers(cfg)
            targets = [t for t in targets if t.get("status") == "online"]

        if not targets:
            print_error(
                "No backends to test",
                "No online inference servers found.",
                hints=[
                    "Start oMLX, Ollama, or LM Studio",
                    "Run [cyan]netllm discover[/]",
                    "Test one URL: [cyan]netllm test --backend http://127.0.0.1:8080/v1[/]",
                ],
            )
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            for t in targets:
                url = t["base_url"]
                console.print(f"\n[bold]Testing[/] {t.get('name', url)}")
                console.print(f"  [dim]{url}[/]")
                key = t.get("api_key") or None
                diag = await diagnose_backend(url, client, api_key=key, model=model)
                console.print(f"  Reachability: {diag.get('status')}")
                console.print(f"  Models: {len(diag.get('models') or [])}")
                if diag.get("latency_ms") is not None:
                    console.print(f"  1-token latency: {diag['latency_ms']}ms")
                inf = diag.get("inference_status")
                console.print("  Inference: ", inference_status_style(inf), sep="")
                if inf == "model_not_found" and model:
                    print_warnings([
                        f"Model [cyan]{model}[/] not loaded on this server",
                        "List models: curl {}/models".format(url.rstrip("/")),
                    ])
                elif inf in ("offline", "timeout"):
                    print_warnings(offline_provider_hints([t]))

    asyncio.run(run())


@app.command("gateway")
def gateway_enable(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Set this agent's role to gateway."""
    cfg_path = _config_path_option(config)
    cfg = _require_config(cfg_path)
    cfg.agent.role = "gateway"
    save_config(cfg, cfg_path)
    console.print(f"[green]Gateway role saved[/] → {cfg_path}")
    print_next_steps(
        [
            ("netllm serve", "Restart the agent"),
            ("netllm status", "Confirm role=gateway and backends"),
            ("Add swarm.peers on worker machines", "Optional static peer URLs"),
        ],
        title="Next",
    )


@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Check common misconfigurations."""
    cfg_path = _config_path_option(config)
    issues: list[tuple[str, str]] = []

    if not cfg_path.is_file():
        issues.append(("No config file", "Run `netllm init`"))

    cfg = load_config(cfg_path) if cfg_path.is_file() else NetllmConfig()

    if cfg.agent.listen.startswith("0.0.0.0") and not cfg.swarm.cluster_token:
        issues.append((
            "LAN exposure without cluster token",
            "Set swarm.cluster_token in config when listening on 0.0.0.0",
        ))

    if cfg.agent.role == "gateway" and not cfg.agent.advertise:
        issues.append((
            "Gateway not advertising",
            "Set agent.advertise = true so workers can find the gateway",
        ))

    if cfg.swarm.mdns and cfg.agent.advertise and not mdns_available():
        issues.append((
            "mDNS enabled but zeroconf not installed",
            "Reinstall: uv sync (zeroconf should install with netllm)",
        ))

    if global_netllm_installed() and not global_cli_on_path():
        issues.append((
            "Global CLI installed but not on PATH in this terminal",
            f"Run: {path_export_line()}  — or: source ~/.zshrc",
        ))

    results = asyncio.run(scan_local_providers(cfg))
    if not any(r.get("status") == "online" for r in results):
        issues.append((
            "No local inference servers online",
            "Start oMLX (:8080), Ollama (:11434), or LM Studio (:1234)",
        ))

    has_anthropic_backend = any(
        b.provider == "anthropic" for b in cfg.routing.backends if b.enabled
    )
    if has_anthropic_backend and not os.environ.get("ANTHROPIC_API_KEY"):
        missing_keys = [
            b.api_key_env
            for b in cfg.routing.backends
            if b.enabled and b.provider == "anthropic" and b.api_key_env
        ]
        if missing_keys:
            issues.append((
                "Anthropic cloud failover configured but API key missing",
                f"Set env var: {missing_keys[0]}",
            ))

    from netllm_discovery.lan import local_lan_ip
    from netllm_discovery.mdns import parse_listen_host_port
    from netllm_discovery.runtime import check_listen_port, port_owner_pid

    if cfg.agent.listen.startswith("0.0.0.0") and local_lan_ip() is None:
        issues.append((
            "LAN listen but no LAN IP detected",
            "Swarm discovery may fail — check network interface",
        ))

    conflict = check_listen_port(cfg)
    if conflict:
        pid_hint = f" (pid {conflict.pid})" if conflict.pid else ""
        if conflict.occupied_by_netllm:
            issues.append((
                f"Port {conflict.port} in use by netllm agent{pid_hint}",
                "Run netllm serve --replace or stop the existing agent",
            ))
        else:
            issues.append((
                f"Port {conflict.port} in use by another process{pid_hint}",
                "Free the port or use netllm serve --port <other>",
            ))

    if cfg.swarm.mdns and cfg.agent.advertise and mdns_available():
        _, listen_port = parse_listen_host_port(cfg.agent.listen)
        local_base = listen_url(cfg.agent.listen)
        agent_up = False
        try:
            with httpx.Client(timeout=2.0) as client:
                agent_up = client.get(f"{local_base}/health").status_code == 200
        except httpx.HTTPError:
            agent_up = False
        if agent_up:
            try:
                from netllm_discovery.lan import browse_mdns_peers

                found = browse_mdns_peers(timeout_s=1.0)
                self_found = any(
                    p.get("agent_id") == cfg.agent.agent_id for p in found
                )
                if not self_found and port_owner_pid(listen_port) is not None:
                    issues.append((
                        "mDNS advertise may have failed",
                        "Stale Bonjour registration? Try netllm serve --replace",
                    ))
            except RuntimeError:
                pass

    if as_json:
        payload = {
            "ok": not issues,
            "issues": [{"title": t, "fix": f} for t, f in issues],
        }
        typer.echo(json.dumps(payload))
        return

    if issues:
        console.print("[yellow]Issues found:[/]\n")
        for title, fix in issues:
            console.print(f"  [bold red]×[/] {title}")
            console.print(f"    [dim]→ {fix}[/]")
        raise typer.Exit(1)

    console.print("[green]All checks passed.[/] Run [cyan]netllm serve[/] to start.")


config_app = typer.Typer(help="Import/export config.toml as JSON (settings UI).")
app.add_typer(config_app, name="config")


@config_app.command("export")
def config_export(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Write full config as JSON to stdout."""
    emit_export(_config_path_option(config))


@config_app.command("import")
def config_import_cmd(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Read JSON from stdin and save to config.toml."""
    read_import(_config_path_option(config))


@app.command()
def start(
    timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for agent"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after dispatch"),
) -> None:
    """Start the netllm agent (macOS app or Homebrew service)."""
    raise typer.Exit(lifecycle_command("start", timeout=timeout, no_wait=no_wait))


@app.command()
def stop() -> None:
    """Stop the netllm agent (macOS app or Homebrew service)."""
    raise typer.Exit(lifecycle_command("stop"))


@app.command()
def restart(
    timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for agent"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return after dispatch"),
) -> None:
    """Restart the netllm agent (macOS app or Homebrew service)."""
    raise typer.Exit(lifecycle_command("restart", timeout=timeout, no_wait=no_wait))


@app.command()
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


if __name__ == "__main__":
    app()
