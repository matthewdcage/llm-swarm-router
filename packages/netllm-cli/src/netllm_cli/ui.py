"""Rich formatting, error helpers, and copy-paste blocks for the netllm CLI."""

from __future__ import annotations

from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


def listen_url(listen: str) -> str:
    """Normalize agent listen address to a full http URL."""
    if listen.startswith("http"):
        return listen.rstrip("/")
    host, _, port = listen.partition(":")
    if not host or host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port or '11400'}"


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
    hints: list[str] = []
    offline = [r for r in results if r.get("status") != "online"]
    if not offline:
        return hints
    for r in offline:
        pid = r.get("id", "")
        if pid == "omlx":
            hints.append(
                "oMLX: open the app or run "
                "[cyan]omlx serve --host 0.0.0.0 --port 8080[/]"
            )
        elif pid == "ollama":
            hints.append("Ollama: run [cyan]ollama serve[/]")
        elif pid == "lmstudio":
            hints.append("LM Studio: enable the local API server in the app")
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
