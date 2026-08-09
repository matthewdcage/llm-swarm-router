"""Per-URL backend credential resolution and routing.backends sync helpers."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from netllm_core.local_providers import api_key_env_for, default_api_key_for
from netllm_core.models import BackendOverride, NetllmConfig, ProviderId


def normalize_backend_url(url: str) -> str:
    """Ensure OpenAI-compatible base URL ends with /v1 (no trailing slash after)."""
    raw = url.strip().rstrip("/")
    if not raw:
        return raw
    if raw.endswith("/v1"):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return raw
    return f"{raw}/v1"


def backend_override_for_url(cfg: NetllmConfig, url: str) -> BackendOverride | None:
    """Return the enabled routing.backends row for a normalized base URL."""
    norm = normalize_backend_url(url)
    if not norm:
        return None
    for override in cfg.routing.backends:
        if not override.enabled or not override.base_url:
            continue
        if normalize_backend_url(override.base_url) == norm:
            return override
    return None


def _api_key_for_provider_id(provider_id: str) -> str:
    """Env + built-in default when no per-URL override applies."""
    env_name = api_key_env_for(provider_id)
    default = default_api_key_for(provider_id)
    if env_name:
        return os.environ.get(env_name, default)
    return default


def resolve_api_key_for_url(
    base_url: str, provider_id: str, config: NetllmConfig
) -> str:
    """Resolve the API key for one discovery/routing URL.

    Priority: enabled ``routing.backends`` row matching ``base_url``,
    then provider env var / built-in default.
    """
    override = backend_override_for_url(config, base_url)
    if override is not None:
        key = override.resolve_api_key()
        if key:
            return key
    return _api_key_for_provider_id(provider_id)


def discovery_urls(cfg: NetllmConfig) -> list[tuple[str, ProviderId]]:
    """All pinned discovery URLs with their provider id."""
    rows: list[tuple[str, ProviderId]] = []
    for provider_id, urls in cfg.discovery.provider_urls.items():
        for url in urls:
            norm = normalize_backend_url(url)
            if norm:
                rows.append((norm, provider_id))  # type: ignore[arg-type]
    for url in cfg.discovery.custom_endpoints:
        norm = normalize_backend_url(url)
        if norm:
            rows.append((norm, "custom"))
    return rows


def upsert_backend_credential(
    cfg: NetllmConfig,
    url: str,
    provider: ProviderId,
    *,
    api_key: str = "",
    api_key_env: str = "",
) -> BackendOverride:
    """Create or update a routing.backends row keyed by normalized base_url."""
    norm = normalize_backend_url(url)
    existing = backend_override_for_url(cfg, norm)
    if existing is not None:
        idx = cfg.routing.backends.index(existing)
        row = cfg.routing.backends[idx]
        if api_key:
            row.api_key = api_key
            row.api_key_env = ""
        elif api_key_env:
            row.api_key_env = api_key_env
            row.api_key = ""
        row.provider = provider
        row.enabled = True
        row.local = True
        return row
    row = BackendOverride(
        base_url=norm,
        provider=provider,
        api_key=api_key,
        api_key_env=api_key_env,
        enabled=True,
        local=True,
    )
    cfg.routing.backends.append(row)
    return row


def _is_credential_only_override(row: BackendOverride) -> bool:
    """True when the row exists only to hold a URL credential."""
    return (
        row.max_concurrency == 0 and row.api_format is None and not row.cloud_provider
    )


def prune_orphan_backend_overrides(cfg: NetllmConfig) -> int:
    """Drop credential-only overrides whose URL left discovery lists."""
    known = {url for url, _ in discovery_urls(cfg)}
    kept: list[BackendOverride] = []
    removed = 0
    for row in cfg.routing.backends:
        norm = normalize_backend_url(row.base_url)
        if (
            norm
            and norm not in known
            and _is_credential_only_override(row)
            and (row.api_key or row.api_key_env)
        ):
            removed += 1
            continue
        kept.append(row)
    cfg.routing.backends = kept
    return removed
