"""`netllm init` and `netllm install`."""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import typer
from netllm_core.config import (
    ensure_lan_mesh_defaults,
    load_config,
    save_config,
)
from netllm_core.models import NetllmConfig, listen_port
from netllm_discovery.local import scan_local_providers
from rich.panel import Panel

from netllm_cli.commands._common import _config_path_option
from netllm_cli.install import (
    ensure_global_cli,
    find_repo_root,
    global_cli_on_path,
    print_path_notice,
    suggested_cli,
)
from netllm_cli.ui import (
    console,
    enabled_provider_summary,
    listen_url,
    offline_provider_hints,
    print_error,
    print_heading,
    print_next_steps,
    print_warnings,
    providers_table,
)


def _resolve_init_swarm_mode(*, swarm: bool, single: bool) -> bool:
    """One guided question on a TTY; non-TTY stays single-machine."""
    if swarm and single:
        print_error(
            "Conflicting flags",
            "--swarm and --single are mutually exclusive.",
        )
        raise typer.Exit(1)
    if swarm:
        return True
    if single:
        return False
    if sys.stdin.isatty() and sys.stdout.isatty():
        console.print(
            "\n[bold]Single machine, or LAN swarm?[/]\n"
            "  [dim]Swarm mode binds the agent to your LAN, generates a\n"
            "  cluster token, and spreads same-model load across machines.[/]"
        )
        return typer.confirm("Set up a LAN swarm (mesh with other machines)?")
    return False


def _listen_port_of(listen: str) -> str:
    """Port from a host:port listen string (IPv6-safe)."""
    return str(listen_port(listen))


def _apply_open_swarm_mode(cfg: NetllmConfig) -> None:
    cfg.agent.listen = f"0.0.0.0:{_listen_port_of(cfg.agent.listen)}"
    # Single source of truth for LAN mesh defaults (one-shot strategy
    # upgrade + subnet_scan) — keep policy out of individual commands.
    ensure_lan_mesh_defaults(cfg)


def _apply_secured_swarm_mode(cfg: NetllmConfig) -> None:
    _apply_open_swarm_mode(cfg)
    if not cfg.swarm.cluster_token:
        cfg.swarm.cluster_token = secrets.token_urlsafe(24)
    # A cluster token on its own secures gossip and remote admin but leaves
    # POST /v1/chat/completions open to the whole LAN — which is not what
    # anyone reads "--secure" to mean (F-14). Only new --secure runs are
    # affected; an existing config is never rewritten by this.
    cfg.swarm.require_token_for_inference = True


def _join_command_for(cfg: NetllmConfig) -> str:
    from netllm_discovery.lan import agent_url_from_listen

    lan_url = agent_url_from_listen(cfg.agent.listen)
    return f"netllm join {lan_url} --token {cfg.swarm.cluster_token}"


def _print_swarm_summary(cfg: NetllmConfig) -> None:
    if cfg.swarm.cluster_token:
        console.print(
            Panel(
                "[bold]Run on every other machine:[/]\n"
                f"  [cyan]{_join_command_for(cfg)}[/]\n\n"
                "[dim]Token saved in config (swarm.cluster_token) — show it any "
                "time with[/] [cyan]netllm swarm-token[/]",
                title="Secured LAN swarm enabled",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold]Open trusted-LAN swarm[/] — no cluster token required.\n\n"
                "On other machines: enable LAN in the menubar app or run "
                "[cyan]netllm init --swarm[/]. They will find this agent via "
                "subnet scan / mDNS.\n\n"
                "[dim]Untrusted network?[/] [cyan]netllm init --swarm --secure[/] "
                "or [cyan]netllm swarm-token --create[/]",
                title="LAN swarm enabled",
                border_style="green",
            )
        )


def _swarm_next_steps(cfg: NetllmConfig, base: str) -> list[tuple[str, str]]:
    steps: list[tuple[str, str]] = [
        (suggested_cli("serve"), "Start the router (binds your LAN)"),
        (
            f"export OPENAI_BASE_URL={base}/v1",
            "Point OpenAI clients at netllm",
        ),
        (suggested_cli("peers"), "Verify machines found each other"),
        (suggested_cli("models"), "Combined model catalog"),
    ]
    if cfg.swarm.cluster_token:
        steps.insert(1, (_join_command_for(cfg), "Run on every other machine"))
    else:
        steps.insert(
            1,
            (
                suggested_cli("init --swarm"),
                "On other machines — enable LAN mesh (open pairing)",
            ),
        )
    return steps


def _run_init_post_save(
    cfg: NetllmConfig, cfg_path: Path, *, swarm_mode: bool, upgraded: bool = False
) -> None:
    base = listen_url(cfg.agent.listen)
    title = "LAN swarm settings applied" if upgraded else "netllm initialized"
    print_heading(title, f"Config written to {cfg_path}")
    if swarm_mode:
        _print_swarm_summary(cfg)

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
            f"netllm could not reach "
            f"{enabled_provider_summary(cfg.discovery.providers)} on this machine.",
            hints=offline_provider_hints(results)
            + [
                "Start a server, then run [cyan]netllm discover[/]",
                "You can still run [cyan]netllm serve[/] — backends appear when online",
            ],
        )

    if swarm_mode:
        print_next_steps(_swarm_next_steps(cfg, base))
    else:
        print_next_steps(
            [
                (suggested_cli("serve"), "Start the router (this terminal)"),
                (
                    suggested_cli("init --swarm"),
                    "LAN swarm — mesh with other machines (open trusted LAN)",
                ),
                (f"export OPENAI_BASE_URL={base}/v1", "Point OpenAI clients at netllm"),
                (suggested_cli("status"), "New terminal — backends, peers, health"),
                (suggested_cli("models"), "List all routed models"),
            ]
        )


def init(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
    global_cli: bool = typer.Option(
        True,
        "--global-cli/--no-global-cli",
        help="Install `netllm` globally via uv and update shell PATH",
    ),
    swarm: bool = typer.Option(
        False,
        "--swarm",
        help="LAN swarm mode: bind 0.0.0.0, local_spillover, subnet scan "
        "(open trusted LAN by default)",
    ),
    secure: bool = typer.Option(
        False,
        "--secure",
        help="With --swarm: also generate swarm.cluster_token for secured pairing",
    ),
    single: bool = typer.Option(
        False,
        "--single",
        help="Single-machine mode (loopback bind, local-only routing)",
    ),
) -> None:
    """Write default config, scan local providers, optionally install global CLI."""
    if global_cli and find_repo_root() is not None:
        installed = ensure_global_cli()
        print_path_notice(installed=installed)

    cfg_path = _config_path_option(config)
    swarm_mode = _resolve_init_swarm_mode(swarm=swarm, single=single)
    if secure and not swarm_mode:
        print_error(
            "Conflicting flags",
            "--secure requires --swarm (or answer yes to the swarm prompt).",
        )
        raise typer.Exit(1)

    def _apply_init_swarm(cfg: NetllmConfig) -> None:
        if secure:
            _apply_secured_swarm_mode(cfg)
        else:
            _apply_open_swarm_mode(cfg)

    if cfg_path.is_file() and not force:
        if swarm_mode:
            cfg = load_config(cfg_path)
            _apply_init_swarm(cfg)
            save_config(cfg, cfg_path)
            _run_init_post_save(cfg, cfg_path, swarm_mode=True, upgraded=True)
            return
        print_error(
            "Config already exists",
            f"[cyan]{cfg_path}[/] is already present.",
            hints=[
                "Scan providers: [cyan]netllm discover[/]",
                "LAN swarm upgrade: [cyan]netllm init --swarm[/]",
                "Overwrite: [cyan]netllm init --force[/]",
                "Join a swarm without re-init: [cyan]netllm join URL --token T[/]",
                "Edit config: [cyan]netllm config-edit[/]",
            ],
        )
        raise typer.Exit(0)

    cfg = NetllmConfig()
    if swarm_mode:
        _apply_init_swarm(cfg)
    save_config(cfg, cfg_path)
    _run_init_post_save(cfg, cfg_path, swarm_mode=swarm_mode)


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
