"""Rich formatting, error helpers, and copy-paste blocks for the netllm CLI."""

from __future__ import annotations

import sys
from typing import Any

import httpx
from netllm_core.local_providers import (
    LOCAL_PROVIDERS,
    NON_DISCOVERABLE_LABELS,
    get_local_provider_spec,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# Derived from the single roster (PROGRAM.md Axis B). Note the asymmetry that
# is real rather than an oversight: `custom` is labelled here but is not a
# discoverable provider -- it has no ports, no probe and no key -- so labels
# parameterize over LOCAL_PROVIDERS | NON_DISCOVERABLE_LABELS while port and
# probe logic parameterizes over LOCAL_PROVIDERS alone.
_PROVIDER_LABELS: dict[str, str] = {
    **{spec.id: spec.short_label for spec in LOCAL_PROVIDERS.values()},
    **NON_DISCOVERABLE_LABELS,
}


def enabled_provider_summary(providers: list[str]) -> str:
    """Human-readable list of enabled discovery providers."""
    labels = [_PROVIDER_LABELS.get(p, p) for p in providers]
    if not labels:
        return "configured providers"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"


def default_provider_port_hint() -> str:
    """ "Start X (:port), Y (:port), or Z (:port)" for this platform.

    Derived from the roster, so the platform branch and the four hardcoded
    port numbers are both gone -- they were two of Axis B's eleven maps, and
    the ports were free to drift from the ones actually scanned.
    """
    parts = [
        f"{spec.short_label} (:{spec.hint_port()})"
        for spec in LOCAL_PROVIDERS.values()
        if sys.platform in spec.platforms
    ]
    if not parts:
        return "Start a local inference server"
    if len(parts) == 1:
        return f"Start {parts[0]}"
    return "Start " + ", ".join(parts[:-1]) + f", or {parts[-1]}"


def mdns_platform_hint() -> str:
    if sys.platform == "linux":
        return "Linux LAN discovery uses Avahi via python-zeroconf"
    if sys.platform == "win32":
        return (
            "Windows mDNS may require firewall rules or Bonjour; "
            "use swarm.peers or netllm peers --subnet-scan"
        )
    return "Guest Wi-Fi may block mDNS; use swarm.peers or --subnet-scan"


def firewall_hints() -> list[str]:
    """Per-platform firewall commands for mDNS (UDP 5353) + agent (TCP 11400)."""
    if sys.platform == "linux":
        return [
            "firewalld: [cyan]sudo firewall-cmd --permanent --add-service=mdns "
            "&& sudo firewall-cmd --permanent --add-port=11400/tcp "
            "&& sudo firewall-cmd --reload[/]",
            "ufw: [cyan]sudo ufw allow 5353/udp && sudo ufw allow 11400/tcp[/]",
        ]
    if sys.platform == "win32":
        return [
            'mDNS in: [cyan]netsh advfirewall firewall add rule name="netllm mDNS" '
            "dir=in protocol=UDP localport=5353 action=allow[/]",
            'agent in: [cyan]netsh advfirewall firewall add rule name="netllm agent" '
            "dir=in protocol=TCP localport=11400 action=allow[/]",
        ]
    return [
        "macOS firewall: System Settings → Network → Firewall — "
        "allow incoming connections for python/netllm",
    ]


def _listen_host_port(listen: str) -> tuple[str, str]:
    if listen.startswith("http"):
        from urllib.parse import urlparse

        parsed = urlparse(listen)
        return parsed.hostname or "127.0.0.1", str(parsed.port or 11400)
    host, _, port = listen.partition(":")
    return host or "127.0.0.1", port or "11400"


def listen_urls(listen: str) -> tuple[str, str | None]:
    """Return (local client URL, optional LAN URL when bound to 0.0.0.0)."""
    from netllm_discovery.lan import agent_url_from_listen, local_lan_ip

    host, port = _listen_host_port(listen)
    if host == "0.0.0.0":
        client = f"http://127.0.0.1:{port}"
        lan = agent_url_from_listen(listen, lan_ip=local_lan_ip())
        if lan.rstrip("/") == client:
            return client, None
        return client, lan
    return f"http://{host}:{port}", None


def listen_url(listen: str) -> str:
    """Normalize agent listen address to a full http URL for local clients."""
    client, _ = listen_urls(listen)
    return client


def print_heading(title: str, subtitle: str = "") -> None:
    body = f"[bold cyan]{title}[/]"
    if subtitle:
        body += f"\n[dim]{subtitle}[/]"
    console.print(Panel(body, border_style="cyan", padding=(0, 1)))


def print_next_steps(
    steps: list[tuple[str, str]], *, title: str = "Next steps"
) -> None:
    """Print numbered steps: (command, description)."""
    lines = []
    for i, (cmd, desc) in enumerate(steps, start=1):
        lines.append(f"  [bold]{i}.[/] [cyan]{cmd}[/]")
        if desc:
            lines.append(f"     [dim]{desc}[/]")
    console.print(Panel("\n".join(lines), title=title, border_style="green"))


def print_env_block(base_url: str) -> None:
    console.print(
        Panel(
            f"[cyan]export OPENAI_BASE_URL={base_url}/v1[/]\n"
            f"[cyan]export OPENAI_API_KEY=netllm-local[/]",
            title="Wire OpenAI-compatible clients",
            border_style="blue",
        )
    )


def print_endpoints_table(base_url: str) -> None:
    table = Table(title="Agent endpoints", show_header=True, header_style="bold")
    table.add_column("Use")
    table.add_column("Method")
    table.add_column("Path")
    table.add_row("Health check", "GET", f"{base_url}/health")
    table.add_row("OpenAI models", "GET", f"{base_url}/v1/models")
    table.add_row("OpenAI chat", "POST", f"{base_url}/v1/chat/completions")
    table.add_row("Agent status", "GET", f"{base_url}/netllm/v1/status")
    table.add_row("Prometheus", "GET", f"{base_url}/metrics")
    table.add_row("Browser help", "GET", f"{base_url}/")
    console.print(table)


def print_warnings(warnings: list[str]) -> None:
    if not warnings:
        return
    body = "\n".join(f"  [yellow]![/] {w}" for w in warnings)
    console.print(Panel(body, title="Notes", border_style="yellow"))


def print_error(
    title: str,
    message: str,
    *,
    hints: list[str] | None = None,
) -> None:
    lines = [f"[red]{message}[/]"]
    if hints:
        lines.append("")
        lines.append("[bold]Try:[/]")
        for h in hints:
            lines.append(f"  • {h}")
    console.print(Panel("\n".join(lines), title=f"[red]{title}[/]", border_style="red"))


def agent_unreachable_message(base_url: str, exc: Exception) -> tuple[str, list[str]]:
    """Return (short message, hint list) for failed agent connections."""
    hints = [
        "Start the agent: [cyan]netllm serve[/]",
        f"Check health: [cyan]curl {base_url}/health[/]",
        "Use another agent URL: [cyan]netllm status --url http://HOST:11400[/]",
    ]
    if isinstance(exc, httpx.ConnectError):
        return "Nothing is listening on that address (connection refused).", hints
    if isinstance(exc, httpx.TimeoutException):
        return "The agent did not respond in time.", hints + [
            "If the agent is starting, wait a few seconds and retry.",
        ]
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return f"HTTP {code} from agent.", hints + [
            f"Response: {exc.response.text[:120]}",
        ]
    return str(exc), hints


def providers_table(results: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("URL")
    table.add_column("Status")
    table.add_column("Models")
    table.add_column("Latency")
    table.add_column("Auth")

    for r in results:
        status = r.get("status", "unknown")
        style = "green" if status == "online" else "red"
        models = r.get("models") or []
        lat = r.get("latency_ms")
        auth = r.get("auth_hint", "—")
        table.add_row(
            r.get("name", "?"),
            r.get("base_url", ""),
            f"[{style}]{status}[/{style}]",
            str(len(models)),
            f"{lat}ms" if lat is not None else "—",
            auth,
        )
    console.print(table)


def offline_provider_hints(results: list[dict[str, Any]]) -> list[str]:
    """One "here is how to start it" line per offline provider.

    Was a four-arm `elif` chain, each arm restating the provider's label, its
    env var and (for oMLX) its default ports -- three more of Axis B's
    parallel maps, and the port list was free to drift from the one actually
    scanned. The prose now comes from `spec.offline_hint`; everything around
    it is derived, so a fifth provider gets a correct hint for free.
    """
    hints: list[str] = []
    offline = [r for r in results if r.get("status") != "online"]
    if not offline:
        return hints
    for r in offline:
        spec = get_local_provider_spec(str(r.get("id", "")))
        if spec is None or sys.platform not in spec.platforms:
            continue
        pointers = [f"[cyan]discovery.provider_urls.{spec.id}[/]"]
        if spec.host_env:
            pointers.insert(0, f"[cyan]{spec.host_env}[/]")
        elif spec.port_env:
            pointers.append(f"[cyan]{spec.port_env}[/]")
        line = f"{spec.short_label}: {spec.offline_hint} or set " + " / ".join(pointers)
        # Dedupe: every port is probed on BOTH 127.0.0.1 and localhost, so a
        # naive slice of probed_urls renders "1234, 1234, 41334, 41334".
        seen: list[str] = []
        for url in r.get("probed_urls") or []:
            port = str(url).split(":")[-1].split("/")[0]
            if port and port not in seen:
                seen.append(port)
        ports = ", ".join(seen[:4]) or ", ".join(str(p) for p in spec.default_ports)
        if len(spec.default_ports) > 1:
            line += f" (scanned ports: {ports})"
        hints.append(line)
    return list(dict.fromkeys(hints))


def inference_status_style(status: str | None) -> Text:
    if status in ("online",):
        return Text(status, style="green")
    if status == "online_slow":
        return Text(status, style="yellow")
    if status in ("offline", "timeout", "inference_failed", "model_not_found"):
        return Text(status or "unknown", style="red")
    return Text(status or "—", style="dim")


def mdns_available() -> bool:
    try:
        import zeroconf  # noqa: F401

        return True
    except ImportError:
        return False


def models_table(rows: list[dict[str, str]], *, title: str = "Models") -> None:
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Model")
    table.add_column("Provider")
    table.add_column("Host")
    table.add_column("Scope")
    table.add_column("Backend", overflow="fold")
    for r in rows:
        scope = r.get("scope", "")
        style = "green" if scope == "local" else "cyan"
        table.add_row(
            r.get("model", ""),
            r.get("provider", ""),
            r.get("host", ""),
            f"[{style}]{scope}[/{style}]",
            r.get("backend", ""),
        )
    console.print(table)


def peers_table(peers: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Agent")
    table.add_column("Hostname")
    table.add_column("URL")
    table.add_column("Role")
    table.add_column("Models")
    table.add_column("Found via")
    for p in peers:
        model_count = sum(
            len(b.get("health", {}).get("models") or [])
            for b in (p.get("backends") or [])
        )
        table.add_row(
            p.get("agent_id", "?"),
            p.get("hostname", "—"),
            p.get("listen_url", ""),
            p.get("role", "peer"),
            str(model_count),
            p.get("source", "—"),
        )
    console.print(table)
