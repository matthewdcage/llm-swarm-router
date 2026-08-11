"""Per-URL backend credential resolution, routing.backends sync helpers, and
the `discovery.ignored_urls` denylist.

The ignore-list lives here rather than in `netllm-discovery` because
`normalize_backend_url` — the comparison key every one of those questions is
asked in — already lives here, and because `netllm-cli` and `netllm-agent`
both need to answer "is this URL ignored?" without importing the scanner.
"""

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


def configured_backend_urls(cfg: NetllmConfig) -> set[str]:
    """Normalized base URLs of every ``[[routing.backends]]`` row.

    Disabled rows count. A row a user hand-authored and then switched off is
    still an explicit statement about that URL, and the ignore-list must not
    quietly change what it means.
    """
    return {
        norm
        for norm in (normalize_backend_url(o.base_url) for o in cfg.routing.backends)
        if norm
    }


def ignored_url_keys(cfg: NetllmConfig) -> set[str]:
    """Normalized ``discovery.ignored_urls`` entries that actually take effect.

    **Conflict rule: the explicit configuration wins.** A URL named in both
    ``discovery.ignored_urls`` and ``[[routing.backends]]`` is *not* ignored —
    the override keeps working exactly as before and the ignore entry is
    inert, reported by `ignored_url_conflicts` so the surfaces can say so.

    The alternative (ignore wins) was rejected: `routing.backends` is the only
    place a user states "always route to this endpoint", it carries the
    per-URL API key, and it is the row `scan_local_providers` synthesises a
    backend from whether or not a probe succeeds. Letting a denylist entry
    silently delete one would be a data-loss-grade surprise — the user's
    configured endpoint would vanish from /status with nothing to point at.
    Ignoring is for endpoints discovery *guessed* at (default port scan, env
    hints, custom_endpoints); it is not a way to disable configuration.
    """
    explicit = configured_backend_urls(cfg)
    return {
        norm
        for norm in (normalize_backend_url(u) for u in cfg.discovery.ignored_urls)
        if norm and norm not in explicit
    }


def is_url_ignored(cfg: NetllmConfig, url: str) -> bool:
    """True when discovery must not register `url` as a backend."""
    norm = normalize_backend_url(url)
    return bool(norm) and norm in ignored_url_keys(cfg)


def ignored_url_conflicts(cfg: NetllmConfig) -> list[str]:
    """Ignore entries overruled by an explicit ``[[routing.backends]]`` row.

    Returned normalized, so the caller reports the URL the two settings
    actually agree on rather than whichever spelling was typed last.
    """
    explicit = configured_backend_urls(cfg)
    seen: set[str] = set()
    conflicts: list[str] = []
    for raw in cfg.discovery.ignored_urls:
        norm = normalize_backend_url(raw)
        if norm and norm in explicit and norm not in seen:
            seen.add(norm)
            conflicts.append(norm)
    return conflicts


def add_ignored_url(cfg: NetllmConfig, url: str) -> bool:
    """Add `url` to ``discovery.ignored_urls``. False when already present.

    Stores the normalized form so the list on disk is the same shape the
    comparison uses; never touches ``routing.backends`` or
    ``discovery.provider_urls`` (an ignored endpoint must stay recoverable by
    removing exactly one line).
    """
    norm = normalize_backend_url(url)
    if not norm:
        return False
    if any(normalize_backend_url(u) == norm for u in cfg.discovery.ignored_urls):
        return False
    cfg.discovery.ignored_urls = [*cfg.discovery.ignored_urls, norm]
    return True


def remove_ignored_url(cfg: NetllmConfig, url: str) -> bool:
    """Drop every entry matching `url`. False when nothing matched."""
    norm = normalize_backend_url(url)
    if not norm:
        return False
    kept = [u for u in cfg.discovery.ignored_urls if normalize_backend_url(u) != norm]
    if len(kept) == len(cfg.discovery.ignored_urls):
        return False
    cfg.discovery.ignored_urls = kept
    return True


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
