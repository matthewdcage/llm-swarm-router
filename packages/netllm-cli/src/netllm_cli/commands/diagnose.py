"""`netllm test`, `netllm gateway`, and `netllm doctor`."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import typer
from netllm_core.config import is_lan_listen, load_config, save_config
from netllm_core.config_report import (
    deprecated_key_issues,
    schema_version_issues,
    unknown_cloud_provider_issues,
)
from netllm_core.models import NetllmConfig
from netllm_discovery.local import scan_local_providers

from netllm_cli.commands._common import _config_path_option, _require_config
from netllm_cli.install import (
    global_cli_on_path,
    global_netllm_installed,
    path_export_line,
    suggested_cli,
)
from netllm_cli.lifecycle import control_socket_path
from netllm_cli.ui import (
    agent_unreachable_message,
    console,
    default_provider_port_hint,
    firewall_hints,
    inference_status_style,
    listen_url,
    mdns_available,
    mdns_platform_hint,
    offline_provider_hints,
    print_error,
    print_next_steps,
    print_warnings,
)


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
        msg, hints = agent_unreachable_message(base, exc)
        print_error("Agent unreachable", msg, hints=hints)
        raise typer.Exit(1) from exc


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
                    print_warnings(
                        [
                            f"Model [cyan]{model}[/] not loaded on this server",
                            "List models: curl {}/models".format(url.rstrip("/")),
                        ]
                    )
                elif inf in ("offline", "timeout"):
                    print_warnings(offline_provider_hints([t]))

    asyncio.run(run())


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


def doctor(
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Check common misconfigurations."""
    cfg_path = _config_path_option(config)
    issues: list[tuple[str, str]] = []
    notes: list[str] = []

    if not cfg_path.is_file():
        issues.append(("No config file", "Run `netllm init`"))

    cfg = load_config(cfg_path) if cfg_path.is_file() else NetllmConfig()

    if is_lan_listen(cfg.agent.listen) and not cfg.swarm.cluster_token:
        notes.append(
            "LAN swarm is open (no cluster token). Use "
            "`netllm swarm-token --create` or Settings on untrusted networks."
        )

    if (
        cfg.cloud.enabled
        and cfg.cloud.fallback_enabled
        and cfg.cloud.fallback == "local"
    ):
        # F-46: the value names the fallback *tier*, so "local" reads
        # local-first but means cloud-first. Make the resulting order loud.
        notes.append(
            "cloud.fallback = 'local': cloud is tried FIRST and the local "
            "mesh is the fallback. For local-first routing run "
            "`netllm cloud fallback local-first`."
        )

    if cfg.agent.role == "gateway" and not cfg.agent.advertise:
        issues.append(
            (
                "Gateway not advertising",
                "Set agent.advertise = true so workers can find the gateway",
            )
        )

    # Unknown [cloud.providers.*] ids are preserved on save rather than
    # deleted (models.CloudConfig), so doctor is where they become visible.
    # Same helper the dashboard's doctor panel calls.
    issues.extend(
        (issue["title"], issue["fix"]) for issue in unknown_cloud_provider_issues(cfg)
    )

    # The deprecation clock, read against the file the user actually has --
    # not the model, which carries every field at its default. Same registry
    # the DeprecationWarning and the CI expiry gate read
    # (netllm_core.deprecations).
    issues.extend(
        (issue["title"], issue["fix"]) for issue in deprecated_key_issues(cfg_path)
    )
    issues.extend(
        (issue["title"], issue["fix"]) for issue in schema_version_issues(cfg)
    )

    if cfg.swarm.mdns and cfg.agent.advertise and not mdns_available():
        issues.append(
            (
                "mDNS enabled but zeroconf not installed",
                "Reinstall: uv sync (zeroconf should install with netllm)",
            )
        )

    from netllm_cli.install_detect import skip_global_path_doctor_check

    if (
        global_netllm_installed()
        and not global_cli_on_path()
        and not skip_global_path_doctor_check()
    ):
        issues.append(
            (
                "Global CLI installed but not on PATH in this terminal",
                f"Run: {path_export_line()}  — or: source ~/.zshrc",
            )
        )

    results = asyncio.run(scan_local_providers(cfg))
    if not any(r.get("status") == "online" for r in results):
        issues.append(
            (
                "No local inference servers online",
                default_provider_port_hint(),
            )
        )

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
            issues.append(
                (
                    "Anthropic cloud failover configured but API key missing",
                    f"Set env var: {missing_keys[0]}",
                )
            )

    from netllm_discovery.lan import local_lan_ip
    from netllm_discovery.mdns import parse_listen_host_port
    from netllm_discovery.runtime import check_listen_port, port_owner_pid

    if cfg.agent.listen.startswith("0.0.0.0") and local_lan_ip() is None:
        issues.append(
            (
                "LAN listen but no LAN IP detected",
                "Swarm discovery may fail — check network interface",
            )
        )

    from netllm_cli.install_detect import is_menubar_supervised

    conflict = check_listen_port(cfg)
    if conflict:
        skip_port = (
            is_menubar_supervised()
            and conflict.occupied_by_netllm
            and control_socket_path().exists()
        )
        if skip_port:
            from netllm_cli.lifecycle.darwin import send_app_control

            try:
                app_status = send_app_control("status", timeout=2.0)
                if app_status.get("state") not in {"running", "unresponsive"}:
                    issues.append(
                        (
                            "Menubar supervisor reports agent not running",
                            "Open Settings → Start or Restart Agent (port may be "
                            "held by a stale process)",
                        )
                    )
            except OSError:
                pass
        if not skip_port:
            pid_hint = f" (pid {conflict.pid})" if conflict.pid else ""
            if conflict.occupied_by_netllm:
                if control_socket_path().exists():
                    fix = (
                        "Expected while the menubar app runs the agent — use "
                        f"{suggested_cli('restart')} or Settings → Restart Agent"
                    )
                else:
                    fix = (
                        f"Run {suggested_cli('serve --replace')} or "
                        f"{suggested_cli('restart')}"
                    )
                issues.append(
                    (
                        f"Port {conflict.port} in use by netllm agent{pid_hint}",
                        fix,
                    )
                )
            else:
                issues.append(
                    (
                        f"Port {conflict.port} in use by another process{pid_hint}",
                        "Free the port or use netllm serve --port <other>",
                    )
                )

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
                self_found = any(p.get("agent_id") == cfg.agent.agent_id for p in found)
                if not self_found and port_owner_pid(listen_port) is not None:
                    issues.append(
                        (
                            "mDNS advertise may have failed",
                            f"Try netllm serve --replace. {mdns_platform_hint()}",
                        )
                    )
                if not found and not cfg.agent.listen.startswith("127."):
                    fw = " · ".join(
                        h.replace("[cyan]", "").replace("[/]", "")
                        for h in firewall_hints()
                    )
                    issues.append(
                        (
                            "mDNS silent — multicast may be blocked",
                            f"Check firewall (UDP 5353 in/out, TCP 11400 in). {fw}",
                        )
                    )
            except RuntimeError:
                pass

    if as_json:
        payload: dict[str, Any] = {
            "ok": not issues,
            "issues": [{"title": t, "fix": f} for t, f in issues],
        }
        if notes:
            payload["notes"] = notes
        typer.echo(json.dumps(payload))
        return

    if notes:
        console.print("[dim]Notes:[/]")
        for note in notes:
            console.print(f"  [dim]• {note}[/]")
        console.print()

    if issues:
        console.print("[yellow]Issues found:[/]\n")
        for title, fix in issues:
            console.print(f"  [bold red]×[/] {title}")
            console.print(f"    [dim]→ {fix}[/]")
        raise typer.Exit(1)

    console.print("[green]All checks passed.[/] Run [cyan]netllm serve[/] to start.")
