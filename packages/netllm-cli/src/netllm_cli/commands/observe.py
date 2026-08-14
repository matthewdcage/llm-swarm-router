"""`netllm models`, `peers`, `env`, `drain`, and `status`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import typer
from netllm_core.config import load_config, save_config
from netllm_discovery.lan import discover_lan_agents, models_from_status
from netllm_discovery.local import scan_local_providers
from rich.panel import Panel
from rich.table import Table

from netllm_cli.commands._common import _config_path_option
from netllm_cli.install import (
    global_cli_on_path,
    global_netllm_binary,
    global_netllm_installed,
    path_export_line,
    suggested_cli,
)
from netllm_cli.ui import (
    agent_unreachable_message,
    console,
    firewall_hints,
    listen_url,
    mdns_available,
    models_table,
    offline_provider_hints,
    peers_table,
    print_error,
    print_heading,
    print_next_steps,
    print_warnings,
)


def _telemetry_window_span(
    windows: dict[str, object], preferred: int = 300
) -> int | None:
    raw_spans = windows.get("spans_s") or []
    spans: list[int] = []
    for value in raw_spans:
        if isinstance(value, int):
            spans.append(value)
        elif isinstance(value, float):
            spans.append(int(value))
    if not spans:
        return None
    return preferred if preferred in spans else spans[0]


def _telemetry_span_label(span: int) -> str:
    if span >= 86400:
        return f"{round(span / 86400)} d"
    if span >= 3600:
        return f"{round(span / 3600)} h"
    if span >= 60:
        return f"{round(span / 60)} min"
    return f"{span} s"


def _telemetry_traffic_row(raw_row: dict[str, object], span: int) -> dict[str, object]:
    span_key = str(span)
    requests_map = raw_row.get("requests")
    if not isinstance(requests_map, dict):
        requests_map = raw_row
    requests = int(requests_map.get(span_key, 0) or 0)

    def _optional_tps(key: str) -> float | None:
        bucket = raw_row.get(key)
        if not isinstance(bucket, dict):
            return None
        value = bucket.get(span_key)
        if value is None:
            return None
        return float(value)

    return {
        "requests": requests,
        "avg_prefill_tps": _optional_tps("avg_prefill_tps"),
        "avg_generation_tps": _optional_tps("avg_generation_tps"),
    }


def _telemetry_backend_p50(
    telemetry: dict[str, object], backend_id: str
) -> float | None:
    router = telemetry.get("router")
    if not isinstance(router, dict):
        return None
    backends = router.get("backends")
    if not isinstance(backends, list):
        return None
    for row in backends:
        if not isinstance(row, dict) or row.get("id") != backend_id:
            continue
        p50 = row.get("p50_ms")
        return float(p50) if p50 is not None else None
    return None


def _format_optional_tps(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.1f}k tok/s"
    return f"{value:.1f} tok/s"


def _print_traffic_window_table(
    client: httpx.Client, base: str, telemetry: dict[str, object] | None = None
) -> None:
    if telemetry is None:
        try:
            resp = client.get(
                f"{base.rstrip('/')}/netllm/v1/telemetry?watch=0&history=0"
            )
            resp.raise_for_status()
            telemetry = resp.json()
        except Exception:
            return
    windows = (telemetry.get("router") or {}).get("windows") or {}
    if not isinstance(windows, dict):
        return
    span = _telemetry_window_span(windows)
    if span is None:
        return
    by_backend = windows.get("by_backend")
    if not isinstance(by_backend, dict) or not by_backend:
        return

    rows: list[tuple[str, dict[str, object]]] = []
    for backend_id, raw in by_backend.items():
        if not isinstance(raw, dict):
            continue
        row = _telemetry_traffic_row(raw, span)
        if int(row["requests"]) <= 0:
            continue
        rows.append((str(backend_id), row))
    if not rows:
        return

    total = sum(int(row["requests"]) for _, row in rows)
    span_label = _telemetry_span_label(span)
    table = Table(
        title=f"Traffic by backend (last {span_label})",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Backend")
    table.add_column("Requests", justify="right")
    table.add_column("Share", justify="right")
    table.add_column("p50", justify="right")
    table.add_column("Prefill", justify="right")
    table.add_column("Generation", justify="right")
    for backend_id, row in sorted(
        rows, key=lambda item: int(item[1]["requests"]), reverse=True
    ):
        requests = int(row["requests"])
        share = int((requests / total) * 100) if total else 0
        label = backend_id[5:] if backend_id.startswith("peer:") else backend_id
        p50 = _telemetry_backend_p50(telemetry, backend_id)
        p50_text = f"{p50:.0f} ms" if p50 is not None else "—"
        table.add_row(
            label,
            str(requests),
            f"{share}%",
            p50_text,
            _format_optional_tps(row["avg_prefill_tps"]),  # type: ignore[arg-type]
            _format_optional_tps(row["avg_generation_tps"]),  # type: ignore[arg-type]
        )
    console.print()
    console.print(table)


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
                rows.append(
                    {
                        "model": mid,
                        "provider": provider,
                        "host": host,
                        "scope": "local",
                        "backend": base,
                    }
                )
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
        console.print(f"\n[dim]{len(rows)} model(s) across {len(peers)} agent(s)[/]")
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
                    rows.append(
                        {
                            "model": item.get("id", ""),
                            "provider": item.get("owned_by", "?"),
                            "host": status.get("hostname", "agent"),
                            "scope": "routed",
                            "backend": "—",
                        }
                    )
    except Exception as exc:
        msg, hints = agent_unreachable_message(base, exc)
        print_error("Agent unreachable", msg, hints=hints)
        raise typer.Exit(1) from exc

    if not rows:
        print_warnings(
            [
                "Agent is up but no models registered — start oMLX/Ollama on this host",
                "Run [cyan]netllm discover[/] then restart [cyan]netllm serve[/]",
            ]
        )
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


def peers(
    config: Path | None = typer.Option(None, "--config"),
    mdns: bool = typer.Option(True, "--mdns/--no-mdns", help="Browse mDNS"),
    subnet_scan: bool = typer.Option(
        False,
        "--subnet-scan",
        help="Probe local /24 for agents on :11400 (slow; use if mDNS blocked)",
    ),
    timeout: float = typer.Option(3.0, "--timeout", "-t", help="mDNS browse seconds"),
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
            "mDNS unavailable — [cyan]uv sync[/] or use [cyan]--subnet-scan[/]"
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

    unreachable = [p for p in peers_found if p.get("unreachable")]
    peers_found = [p for p in peers_found if not p.get("unreachable")]
    for p in unreachable:
        who = p.get("agent_id") or p.get("listen_url", "?")
        warnings.append(
            f"Found agent [bold]{who}[/] but it is bound to loopback — "
            f"on that machine run [cyan]netllm serve --host 0.0.0.0[/] "
            f"(or enable LAN in the menubar app / [cyan]netllm init --swarm[/])"
        )

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "peers": peers_found,
                    "unreachable": unreachable,
                    "warnings": warnings,
                }
            )
        )
        return

    print_heading(
        "LAN agent discovery",
        "Finding other netllm routers on your network",
    )
    if warnings:
        print_warnings(warnings)

    if not peers_found and unreachable:
        print_error(
            "Agents found, none reachable",
            f"{len(unreachable)} agent(s) are loopback-bound and cannot "
            "accept LAN traffic (see notes above).",
            hints=[
                "On each unreachable machine: "
                "Enable LAN in the menubar app, [cyan]netllm init --swarm[/], or "
                "[cyan]netllm serve --host 0.0.0.0[/]",
            ],
        )
        raise typer.Exit(1)
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
            ]
            + firewall_hints(),
        )
        raise typer.Exit(1)

    peers_table(peers_found, title="LAN netllm agents")

    if save:
        from netllm_discovery.lan import own_agent_urls

        cfg = load_config(cfg_path)
        own = own_agent_urls(cfg.agent.listen)
        existing = {u.rstrip("/") for u in cfg.swarm.peers}
        added = 0
        skipped_self = 0
        for p in peers_found:
            url = p.get("listen_url", "").rstrip("/")
            if not url or url in existing:
                continue
            if url in own:
                skipped_self += 1
                continue
            cfg.swarm.peers.append(url)
            existing.add(url)
            added += 1
        if skipped_self:
            console.print(
                f"[yellow]Skipped {skipped_self} URL(s) matching this agent[/]"
            )
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


def drain(
    state: str = typer.Argument(
        "on", help="on: stop receiving new swarm work | off: rejoin routing"
    ),
    config: Path | None = typer.Option(None, "--config"),
    url: str | None = typer.Option(None, "--url", help="Agent base URL"),
) -> None:
    """Ask peers to stop routing new work here (existing requests finish
    normally). Runtime-only — resets on restart; run `netllm drain off`
    to rejoin before it does."""
    if state not in ("on", "off"):
        print_error("Invalid state", f"{state!r} — expected 'on' or 'off'.")
        raise typer.Exit(1)
    cfg = load_config(_config_path_option(config))
    base = (url or listen_url(cfg.agent.listen)).rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{base}/netllm/v1/admin/drain", json={"draining": state == "on"}
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        msg, hints = agent_unreachable_message(base, exc)
        print_error("Agent unreachable", msg, hints=hints)
        raise typer.Exit(1) from exc
    if data.get("draining"):
        console.print(
            "[yellow]Draining.[/] Peers stop routing new work here on their next "
            "heartbeat; requests already in flight finish normally. "
            "[cyan]netllm drain off[/] to rejoin."
        )
    else:
        console.print("[green]Rejoined routing.[/] No longer draining.")


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
            _print_traffic_window_table(client, base)
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
    if data.get("draining"):
        info += "\n[yellow bold]Draining[/] — not receiving new swarm work"
    max_concurrency = data.get("max_concurrency") or 0
    if max_concurrency:
        info += f"\n[bold]Max concurrency[/] {max_concurrency}"
    console.print(Panel(info, title="netllm agent", border_style="green"))

    cloud = data.get("cloud") or {}
    if cloud.get("enabled"):
        providers = cloud.get("enabled_providers") or []
        providers_str = ", ".join(providers) if providers else "none configured"
        console.print(
            f"\n[bold]Cloud[/] enabled — fallback={cloud.get('fallback')} "
            f"({'on' if cloud.get('fallback_enabled') else 'off'})  "
            f"providers: {providers_str}"
        )
    else:
        console.print("\n[bold]Cloud[/] disabled")

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
        print_warnings(
            [
                "No backends registered — run [cyan]netllm discover[/] on this host",
            ]
        )

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
        print_warnings(
            [
                f"No swarm peers yet — {lan_hint}, or add swarm.peers in config",
                f"Gateway mode: [cyan]{suggested_cli('gateway')}[/] then restart serve",
            ]
        )
