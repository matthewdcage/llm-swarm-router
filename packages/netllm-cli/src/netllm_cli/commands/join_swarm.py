"""`netllm discover`, `netllm join`, and `netllm swarm-token`."""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

import httpx
import typer
from netllm_core.config import (
    ensure_lan_mesh_defaults,
    is_lan_listen,
    load_config,
    save_config,
)
from netllm_core.models import NetllmConfig
from netllm_discovery.local import merge_discovered_provider_urls, scan_local_providers

from netllm_cli.commands._common import (
    _config_path_option,
    _normalize_agent_url,
    _require_config,
)
from netllm_cli.commands.init_install import _join_command_for, _listen_port_of
from netllm_cli.install import suggested_cli
from netllm_cli.ui import (
    agent_unreachable_message,
    console,
    offline_provider_hints,
    print_error,
    print_heading,
    print_next_steps,
    print_warnings,
    providers_table,
)


def discover(
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    save_urls: bool = typer.Option(
        False,
        "--save-urls",
        help="Persist online provider base URLs to discovery.provider_urls",
    ),
) -> None:
    """Scan localhost for oMLX, Ollama, LM Studio, and vLLM."""
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    # Explicit command: the 1-token latency diagnose is opted in here
    # (it can make a provider load a model, so routine scans skip it).
    results = asyncio.run(scan_local_providers(cfg, diagnose=True))

    if save_urls:
        cfg = merge_discovered_provider_urls(cfg, results)
        save_config(cfg, cfg_path)

    if as_json:
        payload: dict[str, Any] = {"providers": results}
        if save_urls:
            payload["provider_urls"] = cfg.discovery.provider_urls
        typer.echo(json.dumps(payload))
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
    if save_urls:
        console.print("[dim]Saved online provider URLs to config[/]")

    hints = offline_provider_hints(results)
    if hints:
        print_warnings(hints)
    if online == 0:
        raise typer.Exit(1)


def _fetch_join_status(base: str) -> dict[str, Any]:
    """GET the target agent's status; raises typer.Exit on failure."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base}/netllm/v1/status")
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        message, hints = agent_unreachable_message(base, exc)
        print_error("Swarm agent unreachable", message, hints=hints)
        raise typer.Exit(1) from exc


def _validate_join_token(base: str, token: str, agent_id: str) -> None:
    """POST a heartbeat with the token — 401 means the token is wrong."""
    payload = {
        "agent_id": agent_id,
        "listen_url": "",
        "role": "peer",
        "hostname": "joining",
        "backends": [],
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{base}/netllm/v1/heartbeat", json=payload, headers=headers
            )
    except Exception as exc:
        message, hints = agent_unreachable_message(base, exc)
        print_error("Swarm agent unreachable", message, hints=hints)
        raise typer.Exit(1) from exc
    if resp.status_code == 401:
        print_error(
            "Invalid cluster token",
            "The other agent rejected this token.",
            hints=[
                "Show the token on the other machine: [cyan]netllm swarm-token[/]",
                "Copy the full join command printed by [cyan]netllm init --swarm[/]",
            ],
        )
        raise typer.Exit(1)
    if resp.status_code not in (200, 204):
        print_error(
            "Swarm handshake failed",
            f"Heartbeat probe returned HTTP {resp.status_code}.",
            hints=[
                "Check the other agent's logs",
                "Verify both machines run a compatible netllm version",
            ],
        )
        raise typer.Exit(1)


def join(
    url: str = typer.Argument(
        ...,
        help="Any agent already in the swarm, e.g. http://192.168.1.20:11400",
    ),
    token: str = typer.Option(
        "",
        "--token",
        help="Cluster token from `netllm init --swarm` / `netllm swarm-token` "
        "on the other machine",
    ),
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
) -> None:
    """Join this machine to an existing LAN swarm."""
    from netllm_discovery.lan import filter_own_peer_urls

    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)

    base = _normalize_agent_url(url)
    status = _fetch_join_status(base)
    if token and not status.get("cluster_token_set", False):
        print_error(
            "Token mismatch",
            "You passed --token but the other agent has no cluster token set "
            "— its heartbeats would be rejected by this machine.",
            hints=[
                "On the other machine: [cyan]netllm swarm-token --rotate[/], "
                "then re-run join with that token",
                "Or join an open swarm without [cyan]--token[/]",
            ],
        )
        raise typer.Exit(1)
    _validate_join_token(base, token, cfg.agent.agent_id)

    cfg.swarm.cluster_token = token
    _apply_swarm_join_listen(cfg)
    ensure_lan_mesh_defaults(cfg)
    kept, rejected = filter_own_peer_urls([*cfg.swarm.peers, base], cfg.agent.listen)
    if rejected:
        print_error(
            "Cannot join yourself",
            f"[cyan]{base}[/] is this machine's own agent URL.",
            hints=["Run join with the *other* machine's URL"],
        )
        raise typer.Exit(1)
    cfg.swarm.peers = list(dict.fromkeys(kept))
    save_config(cfg, cfg_path)

    hostname = status.get("hostname") or status.get("agent_id") or base
    print_heading(
        "Joined swarm",
        f"Peer: {hostname} @ {base} — config updated at {cfg_path}",
    )
    print_next_steps(
        [
            (suggested_cli("serve"), "Start the agent (binds your LAN)"),
            (suggested_cli("peers"), "Verify the mesh sees both machines"),
            (suggested_cli("models"), "Combined model catalog"),
        ]
    )


def _apply_swarm_join_listen(cfg: NetllmConfig) -> None:
    cfg.agent.listen = f"0.0.0.0:{_listen_port_of(cfg.agent.listen)}"


def swarm_token(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    create: bool = typer.Option(
        False, "--create", help="Generate and save a cluster token if none is set"
    ),
    rotate: bool = typer.Option(
        False, "--rotate", help="Generate and save a new cluster token"
    ),
) -> None:
    """Show (create or rotate) the cluster token other machines use to join."""
    cfg_path = _config_path_option(config)
    cfg = _require_config(cfg_path)

    if rotate:
        cfg.swarm.cluster_token = secrets.token_urlsafe(24)
        save_config(cfg, cfg_path)
        console.print("[green]New cluster token saved.[/]")
        print_warnings(
            [
                "Update every other machine: re-run "
                f"[cyan]{_join_command_for(cfg)}[/] there.",
            ]
        )
    elif create and not cfg.swarm.cluster_token:
        if not is_lan_listen(cfg.agent.listen):
            print_error(
                "Not in LAN swarm mode",
                "Enable LAN bind before creating a cluster token.",
                hints=[
                    "Enable swarm: [cyan]netllm init --swarm[/]",
                    "Or bind LAN: [cyan]netllm serve --host 0.0.0.0[/]",
                ],
            )
            raise typer.Exit(1)
        cfg.swarm.cluster_token = secrets.token_urlsafe(24)
        ensure_lan_mesh_defaults(cfg)
        save_config(cfg, cfg_path)
        console.print(
            "[green]Cluster token created for secured LAN swarm.[/] "
            "Run the join command on your other machines."
        )
    elif not cfg.swarm.cluster_token:
        if is_lan_listen(cfg.agent.listen):
            console.print(
                "[green]Open LAN swarm[/] — no cluster token required on a "
                "trusted home LAN."
            )
            console.print(
                "[dim]Secured pairing:[/] [cyan]netllm swarm-token --create[/] "
                "or [cyan]netllm init --swarm --secure[/]"
            )
            raise typer.Exit(0)
        print_error(
            "No cluster token set",
            "This machine is not in LAN swarm mode yet.",
            hints=[
                "Enable swarm: [cyan]netllm init --swarm[/]",
                "Or bind LAN: [cyan]netllm serve --host 0.0.0.0[/]",
            ],
        )
        raise typer.Exit(1)

    console.print(f"[bold]Cluster token:[/] [cyan]{cfg.swarm.cluster_token}[/]")
    console.print(f"[bold]Join command:[/] [cyan]{_join_command_for(cfg)}[/]")
