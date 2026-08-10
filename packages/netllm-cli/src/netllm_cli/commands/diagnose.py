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
from netllm_core.doctor_checks import (
    doctor_check,
    doctor_report,
    extend_or_pass,
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


#: Every check id `netllm doctor` can emit, in emission order.
#:
#: Shares its vocabulary with `netllm_agent.admin.DOCTOR_CHECK_IDS` -- an id
#: present in both means the same thing on both surfaces -- but the two rosters
#: are deliberately not the same set. The CLI runs before the agent exists and
#: can probe the port it is about to bind and the PATH it was launched from;
#: the agent runs inside a process that already holds the singleton lock and so
#: could only ever report a tautology about it. `agent.port_conflict` is
#: therefore CLI-only, which is also where §5 of
#: docs/ui-redesign-feature-spec.md puts its remediation.
#:
#: `swarm.mdns_advertise` / `swarm.mdns_multicast` are emitted only when their
#: live probe actually ran; a row is a check that executed, not a check that
#: exists.
DOCTOR_CHECK_IDS = (
    "config.present",
    "swarm.open_lan_no_token",
    "cloud.fallback_order",
    "agent.gateway_advertise",
    "cloud.unknown_provider",
    "config.deprecated_key",
    "config.schema_version",
    "swarm.mdns_available",
    "cli.global_path",
    "backends.local_online",
    "cloud.anthropic_key",
    "agent.lan_ip",
    "agent.supervisor",
    "agent.port_conflict",
    "swarm.mdns_advertise",
    "swarm.mdns_multicast",
)

#: Rich markup per severity for the check inventory.
_SEVERITY_MARK = {
    "error": "[bold red]×[/]",
    "warn": "[yellow]![/]",
    "info": "[green]✓[/]",
}


def _safe(text: str) -> str:
    """Finding text is data, not markup.

    Several findings legitimately contain square brackets -- "add an api_key
    under [cloud.providers.openai]", "[[routing.backends]]" -- and Rich reads
    those as style tags and eats them, so the remediation printed to the user
    was missing exactly the thing they had to type.
    """
    from rich.markup import escape

    return escape(str(text))


def _print_doctor_checks(checks: list[dict[str, Any]]) -> None:
    """The full check inventory, passed rows included.

    Only reachable via `--verbose`: the default output stays exactly what it
    was, because `netllm doctor` is in scripts and its quiet-when-healthy
    behaviour is the useful part.
    """
    passed = sum(1 for c in checks if c["ok"])
    console.print(
        f"[bold]{len(checks)} checks[/] · [green]{passed} passed[/] · "
        f"[dim]{len(checks) - passed} to look at[/]\n"
    )
    for check in checks:
        mark = _SEVERITY_MARK[check["severity"]]
        subject = check["subject"]
        # The subject is only worth a column when it is not already in the
        # title -- the port-conflict title names its own port.
        suffix = (
            f" [dim]({_safe(subject)})[/]"
            if subject and subject not in check["title"]
            else ""
        )
        console.print(
            f"  {mark} {_safe(check['title'])}{suffix}  [dim]{check['id']}[/]"
        )
        if not check["ok"]:
            if check.get("detail") and check["detail"] != check["title"]:
                console.print(f"    [dim]{_safe(check['detail'])}[/]")
            if check.get("fix"):
                console.print(f"    [dim]→ {_safe(check['fix'])}[/]")
    console.print()


def doctor(  # noqa: PLR0912, PLR0915 - one check per branch by design
    config: Path | None = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="List every check, including the ones that passed",
    ),
) -> None:
    """Check common misconfigurations."""
    cfg_path = _config_path_option(config)
    checks: list[dict[str, Any]] = []

    have_config = cfg_path.is_file()
    checks.append(
        doctor_check(
            "config.present",
            ok=have_config,
            title="No config file" if have_config is False else "Config file found",
            detail=str(cfg_path),
            fix="" if have_config else "Run `netllm init`",
        )
    )

    cfg = load_config(cfg_path) if have_config else NetllmConfig()

    open_lan = is_lan_listen(cfg.agent.listen) and not cfg.swarm.cluster_token
    checks.append(
        doctor_check(
            "swarm.open_lan_no_token",
            ok=not open_lan,
            severity="warn",
            title=(
                "LAN swarm is open (no cluster token)"
                if open_lan
                else "LAN exposure is gated by a cluster token"
            ),
            # warn-severity `detail` is the legacy note string verbatim.
            detail=(
                "LAN swarm is open (no cluster token). Use "
                "`netllm swarm-token --create` or Settings on untrusted networks."
                if open_lan
                else f"agent.listen is {cfg.agent.listen}"
            ),
        )
    )

    # F-46: the value names the fallback *tier*, so "local" reads
    # local-first but means cloud-first. Make the resulting order loud.
    cloud_first = (
        cfg.cloud.enabled
        and cfg.cloud.fallback_enabled
        and cfg.cloud.fallback == "local"
    )
    checks.append(
        doctor_check(
            "cloud.fallback_order",
            ok=not cloud_first,
            severity="warn",
            title=(
                "Cloud is tried before the local mesh"
                if cloud_first
                else "Cloud fallback order is local-first"
            ),
            detail=(
                "cloud.fallback = 'local': cloud is tried FIRST and the local "
                "mesh is the fallback. For local-first routing run "
                "`netllm cloud fallback local-first`."
                if cloud_first
                else f"cloud.enabled={cfg.cloud.enabled}, "
                f"cloud.fallback={cfg.cloud.fallback!r}"
            ),
        )
    )

    gateway_silent = cfg.agent.role == "gateway" and not cfg.agent.advertise
    checks.append(
        doctor_check(
            "agent.gateway_advertise",
            ok=not gateway_silent,
            title=(
                "Gateway not advertising"
                if gateway_silent
                else f"Role {cfg.agent.role} advertises correctly"
            ),
            detail=f"agent.role={cfg.agent.role}, "
            f"agent.advertise={cfg.agent.advertise}",
            fix=(
                "Set agent.advertise = true so workers can find the gateway"
                if gateway_silent
                else ""
            ),
        )
    )

    # Unknown [cloud.providers.*] ids are preserved on save rather than
    # deleted (models.CloudConfig), so doctor is where they become visible.
    # Same helper the dashboard's doctor panel calls.
    extend_or_pass(
        checks,
        "cloud.unknown_provider",
        unknown_cloud_provider_issues(cfg),
        ok_title="Every [cloud.providers.*] id is recognised",
        ok_detail="No inert provider sections in this config.",
    )

    # The deprecation clock, read against the file the user actually has --
    # not the model, which carries every field at its default. Same registry
    # the DeprecationWarning and the CI expiry gate read
    # (netllm_core.deprecations).
    extend_or_pass(
        checks,
        "config.deprecated_key",
        deprecated_key_issues(cfg_path),
        ok_title="No deprecated config keys",
        ok_detail="Nothing in this config.toml is on the deprecation clock.",
    )
    extend_or_pass(
        checks,
        "config.schema_version",
        schema_version_issues(cfg),
        ok_title=f"config.toml generation {cfg.schema_version} is understood",
        ok_detail="This build can apply every migration the file needs.",
    )

    mdns_wanted = cfg.swarm.mdns and cfg.agent.advertise
    mdns_installed = mdns_available() if mdns_wanted else True
    checks.append(
        doctor_check(
            "swarm.mdns_available",
            ok=mdns_installed,
            title=(
                "mDNS enabled but zeroconf not installed"
                if not mdns_installed
                else "mDNS advertising is available"
                if mdns_wanted
                else "mDNS advertising is off"
            ),
            detail=f"swarm.mdns={cfg.swarm.mdns}, "
            f"agent.advertise={cfg.agent.advertise}",
            fix=(
                "Reinstall: uv sync (zeroconf should install with netllm)"
                if not mdns_installed
                else ""
            ),
        )
    )

    from netllm_cli.install_detect import skip_global_path_doctor_check

    path_broken = (
        global_netllm_installed()
        and not global_cli_on_path()
        and not skip_global_path_doctor_check()
    )
    checks.append(
        doctor_check(
            "cli.global_path",
            ok=not path_broken,
            title=(
                "Global CLI installed but not on PATH in this terminal"
                if path_broken
                else "netllm resolves on PATH"
            ),
            detail="",
            fix=(
                f"Run: {path_export_line()}  — or: source ~/.zshrc"
                if path_broken
                else ""
            ),
        )
    )

    results = asyncio.run(scan_local_providers(cfg))
    online = [r for r in results if r.get("status") == "online"]
    checks.append(
        doctor_check(
            "backends.local_online",
            ok=bool(online),
            title=(
                f"{len(online)} local inference server(s) online"
                if online
                else "No local inference servers online"
            ),
            detail=", ".join(str(r.get("name", "?")) for r in online),
            fix="" if online else default_provider_port_hint(),
        )
    )

    has_anthropic_backend = any(
        b.provider == "anthropic" for b in cfg.routing.backends if b.enabled
    )
    missing_keys = [
        b.api_key_env
        for b in cfg.routing.backends
        if b.enabled and b.provider == "anthropic" and b.api_key_env
    ]
    anthropic_broken = (
        has_anthropic_backend
        and not os.environ.get("ANTHROPIC_API_KEY")
        and bool(missing_keys)
    )
    checks.append(
        doctor_check(
            "cloud.anthropic_key",
            ok=not anthropic_broken,
            title=(
                "Anthropic cloud failover configured but API key missing"
                if anthropic_broken
                else "Anthropic failover has a key or is not configured"
            ),
            detail=(
                "An enabled anthropic backend names an api_key_env that is unset."
                if anthropic_broken
                else f"anthropic backends configured: {has_anthropic_backend}"
            ),
            fix=f"Set env var: {missing_keys[0]}" if anthropic_broken else "",
        )
    )

    from netllm_discovery.agent_lock import agent_lock_path, read_lock_info
    from netllm_discovery.lan import local_lan_ip
    from netllm_discovery.mdns import parse_listen_host_port
    from netllm_discovery.runtime import check_listen_port, port_owner_pid

    lan_broken = cfg.agent.listen.startswith("0.0.0.0") and local_lan_ip() is None
    checks.append(
        doctor_check(
            "agent.lan_ip",
            ok=not lan_broken,
            title=(
                "LAN listen but no LAN IP detected"
                if lan_broken
                else "Listen address resolves"
            ),
            detail=f"agent.listen is {cfg.agent.listen}",
            fix=(
                "Swarm discovery may fail — check network interface"
                if lan_broken
                else ""
            ),
        )
    )

    from netllm_cli.install_detect import is_menubar_supervised

    conflict = check_listen_port(cfg)
    port_row: dict[str, Any] | None = None
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
                    checks.append(
                        doctor_check(
                            "agent.supervisor",
                            ok=False,
                            title="Menubar supervisor reports agent not running",
                            detail=f"control socket state: {app_status.get('state')!r}",
                            fix="Open Settings → Start or Restart Agent (port may be "
                            "held by a stale process)",
                        )
                    )
            except OSError:
                pass
        if not skip_port:
            pid_hint = f" (pid {conflict.pid})" if conflict.pid else ""
            lock_path = agent_lock_path(cfg)
            lock_info = read_lock_info(lock_path)
            lock_hint = ""
            if lock_info is not None and lock_info.pid:
                lock_hint = f"; singleton lock {lock_path} (holder pid {lock_info.pid})"
            elif lock_path.is_file():
                lock_hint = f"; singleton lock file at {lock_path}"
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
                msg = (
                    f"Port {conflict.port} in use by netllm agent{pid_hint}{lock_hint}"
                )
            else:
                msg = (
                    f"Port {conflict.port} in use by another process"
                    f"{pid_hint}{lock_hint}"
                )
                fix = "Free the port or use netllm serve --port <other>"
            port_row = doctor_check(
                "agent.port_conflict",
                ok=False,
                title=msg,
                detail=f"pid {conflict.pid}" if conflict.pid else "",
                fix=fix,
                subject=str(conflict.port),
            )
    if port_row is None:
        port_row = doctor_check(
            "agent.port_conflict",
            ok=True,
            title="Listen port is free or held by this install",
            detail=f"agent.listen is {cfg.agent.listen}",
        )
    checks.append(port_row)

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
                advertise_broken = (
                    not self_found and port_owner_pid(listen_port) is not None
                )
                checks.append(
                    doctor_check(
                        "swarm.mdns_advertise",
                        ok=not advertise_broken,
                        title=(
                            "mDNS advertise may have failed"
                            if advertise_broken
                            else "This agent is visible over mDNS"
                        ),
                        detail=f"{len(found)} agent(s) answered the browse",
                        fix=(
                            f"Try netllm serve --replace. {mdns_platform_hint()}"
                            if advertise_broken
                            else ""
                        ),
                    )
                )
                multicast_broken = not found and not cfg.agent.listen.startswith("127.")
                fw = " · ".join(
                    h.replace("[cyan]", "").replace("[/]", "") for h in firewall_hints()
                )
                checks.append(
                    doctor_check(
                        "swarm.mdns_multicast",
                        ok=not multicast_broken,
                        title=(
                            "mDNS silent — multicast may be blocked"
                            if multicast_broken
                            else "mDNS multicast reaches this host"
                        ),
                        detail=f"{len(found)} agent(s) answered the browse",
                        fix=(
                            f"Check firewall (UDP 5353 in/out, TCP 11400 in). {fw}"
                            if multicast_broken
                            else ""
                        ),
                    )
                )
            except RuntimeError:
                pass

    payload = doctor_report(checks)
    issues = payload["issues"]
    notes = payload.get("notes", [])

    if as_json:
        typer.echo(json.dumps(payload))
        return

    if verbose:
        _print_doctor_checks(checks)

    if notes:
        console.print("[dim]Notes:[/]")
        for note in notes:
            console.print(f"  [dim]• {_safe(note)}[/]")
        console.print()

    if issues:
        console.print("[yellow]Issues found:[/]\n")
        for issue in issues:
            # Escaped: several remediations name a TOML table, and Rich was
            # swallowing "[cloud.providers.openai]" as a style tag.
            console.print(f"  [bold red]×[/] {_safe(issue['title'])}")
            console.print(f"    [dim]→ {_safe(issue['fix'])}[/]")
        if not verbose:
            passed = sum(1 for c in checks if c["ok"])
            console.print(
                f"\n[dim]{len(checks)} checks · {passed} passed · "
                "run with --verbose to list them[/]"
            )
        raise typer.Exit(1)

    console.print("[green]All checks passed.[/] Run [cyan]netllm serve[/] to start.")
