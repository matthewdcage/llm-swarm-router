"""The `netllm cloud` sub-app."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import typer
from netllm_core.config import load_config, save_config
from rich.table import Table

from netllm_cli.commands._common import _config_path_option
from netllm_cli.ui import console, print_error, print_warnings

cloud_app = typer.Typer(help="Manage pre-configured cloud providers.")


def _cloud_provider_id_or_exit(provider_id: str) -> str:
    from netllm_core.cloud_providers import get_provider_spec

    if get_provider_spec(provider_id) is None:
        from netllm_core.cloud_providers import all_provider_ids

        print_error(
            f"Unknown cloud provider {provider_id!r}",
            f"Known providers: {', '.join(all_provider_ids())}",
        )
        raise typer.Exit(1)
    return provider_id


@cloud_app.command("list")
def cloud_list(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Show all pre-configured cloud providers and their state."""
    from netllm_core.cloud_providers import CLOUD_PROVIDERS

    cfg = load_config(_config_path_option(config))
    console.print(
        f"[bold]Cloud[/] enabled={cfg.cloud.enabled}  fallback={cfg.cloud.fallback}  "
        f"fallback_enabled={cfg.cloud.fallback_enabled}"
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Enabled")
    table.add_column("Region")
    table.add_column("API format")
    table.add_column("Key")
    for provider_id, spec in CLOUD_PROVIDERS.items():
        provider_cfg = cfg.cloud.providers.get(provider_id)
        enabled = provider_cfg.enabled if provider_cfg else False
        region = (provider_cfg.region if provider_cfg else "") or spec.default_region()
        api_format = (
            provider_cfg.api_format if provider_cfg else None
        ) or spec.default_api_format
        has_key = bool(
            provider_cfg
            and (provider_cfg.api_key or provider_cfg.api_key_env)
            or os.environ.get(spec.api_key_env)
        )
        table.add_row(
            spec.display_name,
            "[green]yes[/]" if enabled else "[dim]no[/]",
            region,
            api_format,
            "[green]set[/]" if has_key else "[yellow]missing[/]",
        )
    console.print(table)


@cloud_app.command("enable")
def cloud_enable(
    provider: str = typer.Argument(..., help="Provider id, e.g. moonshot"),
    config: Path | None = typer.Option(None, "--config"),
    region: str | None = typer.Option(None, "--region"),
    auth: str | None = typer.Option(
        None,
        "--auth",
        help="api_key (default) | oauth_pkce (openrouter only) | "
        "plan_token (anthropic only, unofficial third-party use)",
    ),
) -> None:
    """Enable a pre-configured cloud provider."""
    from netllm_core.cloud_providers import get_provider_spec
    from netllm_core.models import CloudProviderConfig

    provider = _cloud_provider_id_or_exit(provider)
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    provider_cfg = cfg.cloud.providers.get(provider) or CloudProviderConfig()
    provider_cfg.enabled = True
    if region:
        provider_cfg.region = region
    if auth:
        spec = get_provider_spec(provider)
        if spec is not None and auth not in spec.auth_modes:
            print_error(
                f"{auth!r} not supported by {provider!r}",
                f"Supported auth modes: {', '.join(spec.auth_modes)}",
            )
            raise typer.Exit(1)
        provider_cfg.auth = auth  # type: ignore[assignment]
    cfg.cloud.providers[provider] = provider_cfg
    save_config(cfg, cfg_path)
    console.print(f"[green]Enabled[/] cloud provider {provider!r}.")
    if not (provider_cfg.api_key or provider_cfg.api_key_env):
        from netllm_core.cloud_providers import get_provider_spec

        spec = get_provider_spec(provider)
        env_name = spec.api_key_env if spec else ""
        print_warnings(
            [
                f"No API key set — export {env_name} or run "
                f"[cyan]netllm cloud set-key {provider}[/]"
            ]
        )
    console.print(
        "[dim]Restart or re-apply config for the running agent to pick this up.[/]"
    )


@cloud_app.command("disable")
def cloud_disable(
    provider: str = typer.Argument(...),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Disable a pre-configured cloud provider."""
    provider = _cloud_provider_id_or_exit(provider)
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    if provider in cfg.cloud.providers:
        cfg.cloud.providers[provider].enabled = False
        save_config(cfg, cfg_path)
    console.print(f"[yellow]Disabled[/] cloud provider {provider!r}.")


@cloud_app.command("set-key")
def cloud_set_key(
    provider: str = typer.Argument(...),
    config: Path | None = typer.Option(None, "--config"),
    env: str | None = typer.Option(
        None, "--env", help="Reference an env var instead of storing the key inline"
    ),
) -> None:
    """Set (or reference) the API key for a cloud provider."""
    import getpass

    from netllm_core.models import CloudProviderConfig

    provider = _cloud_provider_id_or_exit(provider)
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    provider_cfg = cfg.cloud.providers.get(provider) or CloudProviderConfig()
    if env:
        provider_cfg.api_key_env = env
        provider_cfg.api_key = ""
    else:
        key = getpass.getpass(f"API key for {provider} (input hidden): ").strip()
        if not key:
            print_error("No key entered", "Aborting — key was not changed.")
            raise typer.Exit(1)
        provider_cfg.api_key = key
        provider_cfg.api_key_env = ""
    cfg.cloud.providers[provider] = provider_cfg
    save_config(cfg, cfg_path)
    console.print(f"[green]Key stored[/] for cloud provider {provider!r}.")


# The stored `cloud.fallback` value names the fallback *tier*, but users
# read it as the priority (F-46): `fallback=local` is cloud-first. The
# aliases say what the user means; the stored values stay unchanged for
# config compat.
_FALLBACK_ALIASES = {"local-first": "cloud", "cloud-first": "local"}


def _fallback_order_line(fallback: str, fallback_enabled: bool) -> str:
    """One explicit sentence stating the resulting try order."""
    if not fallback_enabled or fallback == "none":
        return "Order: local mesh only — no automatic cloud fallback."
    if fallback == "local":
        return "Order: cloud tried FIRST, local mesh is the fallback."
    return "Order: local mesh tried FIRST, cloud is the fallback."


@cloud_app.command("fallback")
def cloud_fallback(
    mode: str = typer.Argument(
        ...,
        help="local-first | cloud-first | cloud | local | none | on | off — "
        "direction (local-first stores 'cloud': cloud is the fallback tier; "
        "cloud-first stores 'local'), or on/off for the fallback toggle",
    ),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Set the cloud fallback direction, or toggle it on/off."""
    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    mode = _FALLBACK_ALIASES.get(mode, mode)
    if mode in ("on", "off"):
        cfg.cloud.fallback_enabled = mode == "on"
    elif mode in ("cloud", "local", "none"):
        cfg.cloud.fallback = mode  # type: ignore[assignment]
    else:
        print_error(
            "Invalid fallback mode",
            f"{mode!r} — expected local-first, cloud-first, cloud, local, "
            "none, on, or off.",
        )
        raise typer.Exit(1)
    save_config(cfg, cfg_path)
    console.print(
        f"[green]Cloud fallback[/] = {cfg.cloud.fallback} "
        f"({'on' if cfg.cloud.fallback_enabled else 'off'})"
    )
    console.print(_fallback_order_line(cfg.cloud.fallback, cfg.cloud.fallback_enabled))


@cloud_app.command("test")
def cloud_test(
    provider: str = typer.Argument(...),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Probe a cloud provider's base URL with the configured key."""
    from netllm_core.cloud_providers import get_provider_spec
    from netllm_core.health import diagnose_backend

    provider = _cloud_provider_id_or_exit(provider)
    cfg = load_config(_config_path_option(config))
    spec = get_provider_spec(provider)
    assert spec is not None
    provider_cfg = cfg.cloud.providers.get(provider)
    api_format = (provider_cfg.api_format if provider_cfg else None) or (
        spec.default_api_format
    )
    endpoint = spec.endpoint(provider_cfg.region if provider_cfg else None)
    base_url = (provider_cfg.base_url if provider_cfg else "") or (
        endpoint.anthropic_base_url
        if api_format == "anthropic"
        else endpoint.openai_base_url
    )
    if not base_url:
        print_error(
            f"{spec.display_name} has no {api_format} endpoint",
            "Pick a different region/api_format.",
        )
        raise typer.Exit(1)
    api_key = ""
    if provider_cfg is not None:
        api_key = provider_cfg.api_key or (
            os.environ.get(provider_cfg.api_key_env, "")
            if provider_cfg.api_key_env
            else ""
        )
    api_key = api_key or os.environ.get(spec.api_key_env, "")
    console.print(f"\n[bold]Testing[/] {spec.display_name} ({api_format})")
    console.print(f"  [dim]{base_url}[/]")
    if not api_key:
        print_warnings([f"No API key found — set {spec.api_key_env} first"])

    async def run() -> None:
        async with httpx.AsyncClient() as client:
            diag = await diagnose_backend(
                base_url, client, api_key=api_key or None, model=None
            )
        console.print(f"  Reachability: {diag.get('status')}")
        console.print(f"  Models: {len(diag.get('models') or [])}")

    asyncio.run(run())


@cloud_app.command("connect")
def cloud_connect(
    provider: str = typer.Argument(
        ..., help="Provider id — only openrouter supports OAuth today"
    ),
    config: Path | None = typer.Option(None, "--config"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the authorize URL instead of opening it"
    ),
) -> None:
    """OAuth PKCE sign-in for providers that support it (OpenRouter)."""
    from netllm_core.cloud_providers import get_provider_spec
    from netllm_core.models import CloudProviderConfig

    from netllm_cli import oauth_pkce

    provider = _cloud_provider_id_or_exit(provider)
    spec = get_provider_spec(provider)
    if spec is None or "oauth_pkce" not in spec.auth_modes:
        print_error(
            f"{provider!r} does not support OAuth",
            "Only openrouter has a sanctioned third-party OAuth flow today "
            "— use [cyan]netllm cloud set-key[/] for the others.",
        )
        raise typer.Exit(1)
    if provider != "openrouter":
        print_error(
            "Not yet wired",
            f"OAuth PKCE is only implemented for openrouter, not {provider!r}.",
        )
        raise typer.Exit(1)

    verifier, challenge = oauth_pkce.generate_pkce_pair()
    port, thread, server = oauth_pkce.start_local_callback_server()
    callback_url = f"http://127.0.0.1:{port}/callback"
    auth_url = oauth_pkce.build_authorize_url(
        callback_url=callback_url, code_challenge=challenge
    )
    console.print("\n[bold]Sign in to OpenRouter[/] to authorize netllm.")
    if no_browser:
        console.print(f"  Open this URL:\n  [cyan]{auth_url}[/]")
    else:
        console.print("  Opening your browser…")
        oauth_pkce.open_browser(auth_url)
    console.print("  Waiting for authorization…")

    try:
        code = oauth_pkce.wait_for_callback(thread, server)
    except oauth_pkce.PKCEFlowError as exc:
        print_error("OpenRouter sign-in failed", str(exc))
        raise typer.Exit(1) from exc

    async def exchange() -> str:
        return await oauth_pkce.exchange_code_for_key(code, verifier)

    try:
        key = asyncio.run(exchange())
    except oauth_pkce.PKCEFlowError as exc:
        print_error("OpenRouter sign-in failed", str(exc))
        raise typer.Exit(1) from exc
    except httpx.HTTPError as exc:
        print_error("OpenRouter token exchange failed", str(exc))
        raise typer.Exit(1) from exc

    cfg_path = _config_path_option(config)
    cfg = load_config(cfg_path)
    provider_cfg = cfg.cloud.providers.get(provider) or CloudProviderConfig()
    provider_cfg.enabled = True
    provider_cfg.auth = "oauth_pkce"
    provider_cfg.api_key = key
    cfg.cloud.providers[provider] = provider_cfg
    save_config(cfg, cfg_path)
    console.print("[green]Connected.[/] OpenRouter key stored and provider enabled.")
