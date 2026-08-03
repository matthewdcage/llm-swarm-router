"""`netllm connect <tool>` — print editor wiring for a known harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import typer
from netllm_core.config import load_config, save_config
from netllm_core.harness_detection import detect
from netllm_core.known_harnesses import KNOWN_HARNESSES, get_known_harness

from netllm_cli.commands._common import _config_path_option, _normalize_agent_url
from netllm_cli.ui import console, listen_url, print_error, print_warnings


@dataclass(frozen=True)
class _HarnessGuide:
    harness_id: str
    surface: str  # "openai" | "anthropic" | "codex"
    env: tuple[str, ...]
    notes: tuple[str, ...] = ()
    codex_toml: str | None = None


def _guides(
    base_v1: str, base_anthropic: str, virtual_key: str
) -> dict[str, _HarnessGuide]:
    return {
        "claude-code": _HarnessGuide(
            "claude-code",
            "anthropic",
            (
                f"export ANTHROPIC_BASE_URL={base_anthropic}",
                f"export ANTHROPIC_API_KEY={virtual_key}",
            ),
            (
                "Model ID must match `./netllm models` exactly.",
                "Verify: `./netllm test --api anthropic --model <id>`",
            ),
        ),
        "codex": _HarnessGuide(
            "codex",
            "codex",
            (f"export NETLLM_API_KEY={virtual_key}",),
            (
                'Codex requires wire_api = "responses" — add to ~/.codex/config.toml:',
                "Verify: curl the /v1/responses endpoint before using the real CLI.",
            ),
            codex_toml=(
                'model_provider = "netllm"\n'
                'model = "<model id from ./netllm models>"\n\n'
                "[model_providers.netllm]\n"
                f'base_url = "{base_v1}"\n'
                'env_key = "NETLLM_API_KEY"\n'
                'wire_api = "responses"\n'
            ),
        ),
        "cursor": _HarnessGuide(
            "cursor",
            "openai",
            (
                f"export OPENAI_BASE_URL={base_v1}",
                f"export OPENAI_API_KEY={virtual_key}",
            ),
            (
                "Cursor Settings → Models → OpenAI-compatible override "
                "with the values above.",
                "Verify: `./netllm test --model <id>`",
            ),
        ),
        "gemini-cli": _HarnessGuide(
            "gemini-cli",
            "openai",
            (
                f"export OPENAI_BASE_URL={base_v1}",
                f"export OPENAI_API_KEY={virtual_key}",
            ),
            (
                "Gemini CLI native protocol may not support custom OpenAI "
                "endpoints yet — see docs/editor-integration.md; Antigravity "
                "OpenAI Compatible is the practical alternative.",
            ),
        ),
        "honcho": _HarnessGuide(
            "honcho",
            "openai",
            (
                f"export OPENAI_BASE_URL={base_v1}",
                f"export OPENAI_API_KEY={virtual_key}",
            ),
            (
                "Docker: use http://host.docker.internal:11400/v1 — "
                "see docs/honcho-integration.md",
            ),
        ),
        "buzz": _HarnessGuide(
            "buzz",
            "openai",
            (
                f"export OPENAI_BASE_URL={base_v1}",
                f"export OPENAI_API_KEY={virtual_key}",
            ),
            (
                "Point buzz-agent provider at the base URL above with "
                "api_key netllm-buzz.",
                "Anthropic provider: use Messages surface at port root (no /v1).",
            ),
        ),
    }


def _agent_base_urls(agent_url: str) -> tuple[str, str]:
    base = _normalize_agent_url(agent_url).rstrip("/")
    parsed = urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return f"{root}/v1", root


def _check_agent_health(agent_url: str) -> bool:
    base = _normalize_agent_url(agent_url).rstrip("/")
    try:
        with httpx.Client(timeout=2.0) as client:
            return client.get(f"{base}/health").status_code == 200
    except httpx.HTTPError:
        return False


def _maybe_toggle_source(
    harness_id: str, *, config: Path | None, toggle: bool
) -> str | None:
    if not toggle:
        return None
    from netllm_core.config_guards import ConfigGuardError, apply_config_guards
    from netllm_core.config_merge import apply_config_patch
    from netllm_discovery.lan import own_agent_urls

    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    entries = [s.model_dump(mode="json") for s in cfg.routing.sources]
    existing = next((e for e in entries if e["id"] == harness_id), None)
    if existing is not None:
        if existing["enabled"]:
            return "already_enabled"
        existing["enabled"] = True
        state = "enabled"
    else:
        entries.append({"id": harness_id, "enabled": True, "known_id": harness_id})
        state = "registered"
    updated = apply_config_patch(cfg, {"routing": {"sources": entries}})
    try:
        apply_config_guards(
            updated, own_agent_urls=own_agent_urls(updated.agent.listen)
        )
    except ConfigGuardError as exc:
        print_error(f"Could not register source {harness_id!r}", str(exc))
        raise typer.Exit(1) from exc
    save_config(updated, cfg_path)
    return state


def _build_payload(
    harness_id: str,
    *,
    agent_url: str,
    detected: bool,
    install_hint: str,
    agent_healthy: bool,
    toggle_result: str | None,
) -> dict[str, Any]:
    known = get_known_harness(harness_id)
    assert known is not None
    base_v1, base_root = _agent_base_urls(agent_url)
    virtual_key = f"netllm-{harness_id}"
    guide = _guides(base_v1, base_root, virtual_key)[harness_id]
    return {
        "harness_id": harness_id,
        "display_name": known.display_name,
        "detected": detected,
        "install_hint": install_hint,
        "agent_url": base_root,
        "agent_healthy": agent_healthy,
        "virtual_key": virtual_key,
        "surface": guide.surface,
        "env": list(guide.env),
        "notes": list(guide.notes),
        "codex_toml": guide.codex_toml,
        "docs_url": known.docs_url,
        "sources_toggle": f"netllm sources toggle {harness_id}",
        "toggle_result": toggle_result,
    }


def connect_tool(
    tool: str = typer.Argument(
        ..., help="Harness id: claude-code, codex, cursor, gemini-cli, honcho, buzz"
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Agent base URL (default: from config listen or 127.0.0.1:11400)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
    print_env: bool = typer.Option(
        False, "--print-env", help="Print export lines only (no Rich formatting)"
    ),
    toggle: bool = typer.Option(
        False,
        "--toggle",
        help=(
            "Register/enable this harness via routing.sources "
            "(never edits editor configs)"
        ),
    ),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Print wiring steps for a known harness (mirrors netllm-connect-editor skill)."""
    harness_id = tool.strip().lower()
    known = get_known_harness(harness_id)
    if known is None:
        ids = ", ".join(h.id for h in KNOWN_HARNESSES)
        print_error(f"Unknown harness {tool!r}", f"Known tools: {ids}")
        raise typer.Exit(1)

    cfg_path = _config_path_option(config)
    agent_url = url
    if agent_url is None and cfg_path.is_file():
        agent_url = listen_url(load_config(cfg_path).agent.listen)
    if agent_url is None:
        agent_url = "http://127.0.0.1:11400"

    detected = detect(known)
    healthy = _check_agent_health(agent_url)
    toggle_result = _maybe_toggle_source(harness_id, config=config, toggle=toggle)
    base_v1, base_root = _agent_base_urls(agent_url)
    virtual_key = f"netllm-{harness_id}"
    guide = _guides(base_v1, base_root, virtual_key)[harness_id]
    payload = _build_payload(
        harness_id,
        agent_url=agent_url,
        detected=detected,
        install_hint=known.install_hint,
        agent_healthy=healthy,
        toggle_result=toggle_result,
    )

    if json_out:
        typer.echo(json.dumps(payload, indent=2))
        if not healthy:
            raise typer.Exit(1)
        return

    if print_env:
        for line in guide.env:
            console.print(line)
        return

    console.print(f"[bold]{known.display_name}[/] → netllm")
    if not healthy:
        print_warnings(
            [
                f"Agent not reachable at {base_root} — run `./netllm serve` or "
                "`./netllm doctor` first."
            ]
        )
    else:
        console.print(f"[green]Agent OK[/] at {base_root}")

    if detected:
        console.print("[green]CLI detected[/] on PATH")
    elif known.install_hint:
        console.print("[yellow]CLI not detected[/] on PATH")
        console.print(
            f"Install (copy-paste, not run by netllm): [cyan]{known.install_hint}[/]"
        )

    console.print("\n[bold]Environment[/]")
    for line in guide.env:
        console.print(f"  {line}")

    if guide.codex_toml:
        console.print("\n[bold]~/.codex/config.toml[/]")
        console.print(guide.codex_toml)

    for note in guide.notes:
        console.print(f"[dim]• {note}[/]")

    console.print(
        f"\n[dim]Optional: attribute traffic separately with "
        f"[cyan]netllm sources toggle {harness_id}[/][/]"
    )
    if toggle_result == "registered":
        console.print("[green]Registered and enabled[/] source in config.toml")
    elif toggle_result == "enabled":
        console.print("[green]Enabled[/] source in config.toml")
    elif toggle_result == "already_enabled":
        console.print("[dim]Source already enabled in config.toml[/]")

    if known.docs_url:
        console.print(f"[dim]Docs: {known.docs_url}[/]")

    if not healthy:
        raise typer.Exit(1)
