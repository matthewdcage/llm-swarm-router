"""Local inference server discovery (oMLX, Ollama, LM Studio)."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from netllm_core.health import diagnose_backend, probe_openai_compat
from netllm_core.models import Backend, BackendHealth, NetllmConfig, infer_api_format

# Display name + default scan ports (localhost); config provider_urls are tried first.
KNOWN_PROVIDERS: list[tuple[str, str, list[int]]] = [
    ("omlx", "oMLX (Apple Silicon)", [8080, 8088, 8081]),
    ("ollama", "Ollama", [11434]),
    ("lmstudio", "LM Studio", [1234, 41334]),
    ("vllm", "vLLM", [8000, 8001]),
]

DEFAULT_API_KEYS: dict[str, str] = {
    "omlx": "omlx-local",
}


def loopback_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """HTTP client for localhost probes (bundled macOS Python may lack CA bundle)."""
    return httpx.AsyncClient(verify=False, **kwargs)


def normalize_openai_base_url(url: str) -> str:
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


def _urls_for_host_port(host: str, port: int) -> list[str]:
    return [normalize_openai_base_url(f"http://{host}:{port}")]


def _dedupe_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        norm = normalize_openai_base_url(url)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _ollama_env_candidates() -> list[str]:
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return []
    if raw.startswith("http://") or raw.startswith("https://"):
        return [normalize_openai_base_url(raw)]
    host = "127.0.0.1"
    port = "11434"
    if raw.startswith(":"):
        port = raw.lstrip(":") or port
    elif ":" in raw:
        host, port = raw.split(":", 1)
        host = host or "127.0.0.1"
        port = port or "11434"
    else:
        host = raw
    return _urls_for_host_port(host, int(port))


def _env_port_candidates(provider_id: str) -> list[str]:
    env_name = {
        "omlx": "OMLX_PORT",
        "ollama": "OLLAMA_PORT",
        "lmstudio": "LMSTUDIO_PORT",
        "vllm": "VLLM_PORT",
    }.get(provider_id, "")
    if not env_name:
        return []
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return []
    urls: list[str] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            urls.extend(_urls_for_host_port("127.0.0.1", int(part)))
            urls.extend(_urls_for_host_port("localhost", int(part)))
    return urls


def candidate_urls_for_provider(provider_id: str, config: NetllmConfig) -> list[str]:
    """Build probe list: saved overrides, env, then default port scan."""
    urls: list[str] = []
    urls.extend(config.discovery.provider_urls.get(provider_id, []))
    if provider_id == "ollama":
        urls.extend(_ollama_env_candidates())
    urls.extend(_env_port_candidates(provider_id))
    for _pid, _name, ports in KNOWN_PROVIDERS:
        if _pid != provider_id:
            continue
        for port in ports:
            urls.extend(_urls_for_host_port("127.0.0.1", port))
            urls.extend(_urls_for_host_port("localhost", port))
        break
    return _dedupe_preserve_order(urls)


def merge_discovered_provider_urls(
    config: NetllmConfig, results: list[dict[str, Any]]
) -> NetllmConfig:
    """Persist online provider base URLs into discovery.provider_urls."""
    urls = dict(config.discovery.provider_urls)
    known_ids = {pid for pid, _, _ in KNOWN_PROVIDERS}
    for row in results:
        if row.get("status") != "online":
            continue
        pid = row.get("id")
        base = row.get("base_url")
        if pid not in known_ids or not base:
            continue
        norm = normalize_openai_base_url(str(base))
        existing = urls.get(str(pid), [])
        if norm not in existing:
            urls[str(pid)] = [norm, *[u for u in existing if u != norm]]
    config.discovery.provider_urls = urls
    return config


def _api_key_for_provider(provider_id: str, config: NetllmConfig) -> str:
    for override in config.routing.backends:
        if override.provider == provider_id:
            return override.resolve_api_key()
    env_map = {
        "omlx": "OMLX_API_KEY",
        "ollama": "OLLAMA_API_KEY",
        "lmstudio": "LMSTUDIO_API_KEY",
        "vllm": "VLLM_API_KEY",
    }
    env_name = env_map.get(provider_id, "")
    if env_name:
        return os.environ.get(env_name, DEFAULT_API_KEYS.get(provider_id, ""))
    return DEFAULT_API_KEYS.get(provider_id, "")


async def _probe_url(
    base_url: str,
    client: httpx.AsyncClient,
    api_key: str,
    *,
    diagnose: bool = False,
) -> dict[str, Any] | None:
    result = await probe_openai_compat(base_url, client, api_key=api_key or None)
    if result.get("status") != "online":
        return None
    if not diagnose:
        # Routine scans must stay read-only: the 1-token inference test
        # forces the provider to LOAD a chat model, which on a
        # memory-constrained host evicts the resident model every scan
        # cycle. Only explicit CLI commands (discover/test) opt in.
        return {**result, "latency_ms": None, "inference_status": None}
    diag = await diagnose_backend(base_url, client, api_key=api_key or None)
    return {
        **result,
        "latency_ms": diag.get("latency_ms"),
        "inference_status": diag.get("inference_status"),
    }


async def _probe_provider(
    provider_id: str,
    display_name: str,
    candidate_urls: list[str],
    client: httpx.AsyncClient,
    api_key: str = "",
    *,
    diagnose: bool = False,
) -> dict[str, Any]:
    if not candidate_urls:
        return {
            "id": provider_id,
            "name": display_name,
            "base_url": "",
            "status": "offline",
            "model_count": 0,
            "models": [],
        }

    hits = await asyncio.gather(
        *[_probe_url(url, client, api_key, diagnose=diagnose) for url in candidate_urls]
    )
    for url, hit in zip(candidate_urls, hits, strict=True):
        if hit is None:
            continue
        return {
            "id": provider_id,
            "name": display_name,
            "base_url": url,
            "api_key": api_key,
            "auth_hint": (
                "omlx-local"
                if provider_id == "omlx" and api_key == "omlx-local"
                else ("configured" if api_key else "none")
            ),
            **hit,
        }
    return {
        "id": provider_id,
        "name": display_name,
        "base_url": candidate_urls[0],
        "status": "offline",
        "model_count": 0,
        "models": [],
        "probed_urls": candidate_urls,
    }


async def scan_local_providers(
    config: NetllmConfig | None = None,
    *,
    include_custom: bool = True,
    diagnose: bool = False,
) -> list[dict[str, Any]]:
    """Probe configured URLs, env hints, and default local ports.

    diagnose=True adds a 1-token inference latency test per provider —
    this can force the provider to load a model, so it is reserved for
    explicit CLI commands, never routine agent refreshes.
    """
    cfg = config or NetllmConfig()
    enabled = set(cfg.discovery.providers)
    results: list[dict[str, Any]] = []

    async with loopback_async_client() as client:
        tasks = []
        for pid, pname, _ports in KNOWN_PROVIDERS:
            if pid not in enabled:
                continue
            urls = candidate_urls_for_provider(pid, cfg)
            key = _api_key_for_provider(pid, cfg)
            tasks.append(
                _probe_provider(pid, pname, urls, client, key, diagnose=diagnose)
            )
        if include_custom:
            for url in cfg.discovery.custom_endpoints:
                norm = normalize_openai_base_url(url)
                tasks.append(
                    _probe_provider(
                        "custom", "Custom", [norm], client, "", diagnose=diagnose
                    )
                )
        for override in cfg.routing.backends:
            if override.enabled and override.base_url:
                norm = normalize_openai_base_url(override.base_url)
                tasks.append(
                    _probe_provider(
                        override.provider,
                        override.provider,
                        [norm],
                        client,
                        override.resolve_api_key(),
                        diagnose=diagnose,
                    )
                )
        if tasks:
            results = list(await asyncio.gather(*tasks))

    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in results:
        url = r.get("base_url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(r)
    return deduped


def scan_results_to_backends(
    results: list[dict[str, Any]],
    *,
    agent_id: str = "",
    local: bool = True,
    config: NetllmConfig | None = None,
) -> list[Backend]:
    cfg = config or NetllmConfig()
    backends: list[Backend] = []
    for r in results:
        if r.get("status") != "online":
            continue
        url = r["base_url"]
        pid = r.get("id", "custom")
        api_key = r.get("api_key") or _api_key_for_provider(str(pid), cfg)
        provider = pid  # type: ignore[arg-type]
        backends.append(
            Backend(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, url)),
                base_url=url,
                provider=provider,
                api_format=infer_api_format(provider),
                api_key=api_key,
                enabled=True,
                local=local,
                agent_id=agent_id,
                health=BackendHealth(
                    status=r.get("status", "online"),
                    http_status=r.get("http_status"),
                    model_count=r.get("model_count", 0),
                    models=r.get("models") or [],
                    detail=r.get("detail"),
                    latency_p50_ms=float(r["latency_ms"])
                    if r.get("latency_ms") is not None
                    else None,
                ),
            )
        )
    return backends


def omlx_admin_url(base_url: str) -> str:
    """Derive oMLX admin UI URL from a discovered OpenAI-compatible base URL."""
    raw = base_url.strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3]
    return f"{raw}/admin"


def _best_omlx_base_url(backends: list[Any]) -> str | None:
    """Best enabled oMLX backend base URL (online first, then most models)."""
    best: tuple[int, str] | None = None
    for backend in backends:
        provider = getattr(backend, "provider", None) or (
            backend.get("provider") if isinstance(backend, dict) else None
        )
        if provider != "omlx":
            continue
        enabled = getattr(backend, "enabled", True)
        if isinstance(backend, dict):
            enabled = backend.get("enabled", True)
        if not enabled:
            continue
        base_url = getattr(backend, "base_url", None) or (
            backend.get("base_url") if isinstance(backend, dict) else None
        )
        if not base_url:
            continue
        health = getattr(backend, "health", None)
        status = "unknown"
        model_count = 0
        if health is not None:
            status = getattr(health, "status", None) or (
                health.get("status") if isinstance(health, dict) else "unknown"
            )
            model_count = int(
                getattr(health, "model_count", 0)
                or (health.get("model_count", 0) if isinstance(health, dict) else 0)
            )
        elif isinstance(backend, dict):
            health_dict = backend.get("health") or {}
            status = health_dict.get("status", "unknown")
            model_count = int(health_dict.get("model_count", 0) or 0)
        if status == "offline":
            continue
        score = model_count + (1000 if status == "online" else 0)
        if best is None or score > best[0]:
            best = (score, str(base_url))
    return best[1] if best else None


def find_omlx_admin_url(backends: list[Any]) -> str | None:
    """Return admin URL for the best enabled oMLX backend (online, most models)."""
    base = _best_omlx_base_url(backends)
    return omlx_admin_url(base) if base else None


def _omlx_service_base(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3]
    return raw.rstrip("/")


def _normalize_omlx_admin_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    loaded: list[str] = []
    if isinstance(data.get("loaded_models"), list):
        for item in data["loaded_models"]:
            if isinstance(item, str):
                loaded.append(item)
            elif isinstance(item, dict):
                name = item.get("id") or item.get("name")
                if name:
                    loaded.append(str(name))
    elif isinstance(data.get("active_models"), list):
        for item in data["active_models"]:
            if isinstance(item, str):
                loaded.append(item)
            elif isinstance(item, dict):
                name = item.get("id") or item.get("name")
                if name:
                    loaded.append(str(name))
    primary = data.get("primary_model") or data.get("current_model")
    if isinstance(primary, dict):
        primary = primary.get("id") or primary.get("name")
    if not primary and loaded:
        primary = loaded[0]
    if not primary and not loaded:
        return None
    return {
        "loaded_models": loaded,
        "primary_loaded_model": str(primary) if primary else None,
    }


async def probe_omlx_admin(
    base_url: str,
    client: Any,
) -> dict[str, Any] | None:
    """Probe oMLX admin HTTP API for loaded-model stats (best-effort)."""
    service_base = _omlx_service_base(base_url)
    admin_url = f"{service_base}/admin"
    for path in ("/api/server-info", "/api/status", "/api/models"):
        try:
            response = await client.get(f"{service_base}{path}", timeout=2.0)
            if response.status_code != 200:
                continue
            payload = response.json()
            if not isinstance(payload, dict):
                continue
            stats = _normalize_omlx_admin_payload(payload)
            if stats is None:
                continue
            stats["admin_url"] = admin_url
            stats["probe_path"] = path
            return stats
        except Exception:
            continue
    return None


async def probe_omlx_admin_for_backends(
    backends: list[Any],
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Probe the best enabled oMLX backend for admin stats."""
    base = _best_omlx_base_url(backends)
    if base is None:
        return None

    if client is not None:
        return await probe_omlx_admin(base, client)

    import httpx

    async with httpx.AsyncClient() as probe_client:
        return await probe_omlx_admin(base, probe_client)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_omlx_stats_scope(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize oMLX /admin/api/stats payload for one scope."""
    prompt = _int_or_zero(data.get("total_prompt_tokens"))
    completion = _int_or_zero(data.get("total_completion_tokens"))
    cached = _int_or_zero(data.get("total_cached_tokens"))
    requests = _int_or_zero(data.get("total_requests"))
    cache_efficiency = _float_or_zero(data.get("cache_efficiency"))
    if cache_efficiency <= 0 and prompt > 0 and cached > 0:
        cache_efficiency = cached / prompt * 100.0
    return {
        "total_prompt_tokens": prompt,
        "total_completion_tokens": completion,
        "total_tokens": prompt + completion,
        "total_cached_tokens": cached,
        "cache_efficiency_pct": round(cache_efficiency, 2),
        "total_requests": requests,
        "avg_prefill_tps": round(_float_or_zero(data.get("avg_prefill_tps")), 2),
        "avg_generation_tps": round(_float_or_zero(data.get("avg_generation_tps")), 2),
    }


def _normalize_omlx_stats_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize oMLX /admin/api/stats response."""
    primary = data.get("primary_model") or data.get("current_model")
    if isinstance(primary, dict):
        primary = primary.get("id") or primary.get("name")
    active = data.get("active_models") or {}
    model_memory_used = 0
    if isinstance(active, dict):
        model_memory_used = _int_or_zero(active.get("model_memory_used"))
    return {
        "total_prompt_tokens": _int_or_zero(data.get("total_prompt_tokens")),
        "total_completion_tokens": _int_or_zero(data.get("total_completion_tokens")),
        "total_cached_tokens": _int_or_zero(data.get("total_cached_tokens")),
        "total_requests": _int_or_zero(data.get("total_requests")),
        "cache_efficiency_pct": round(_float_or_zero(data.get("cache_efficiency")), 2),
        "avg_prefill_tps": round(_float_or_zero(data.get("avg_prefill_tps")), 2),
        "avg_generation_tps": round(_float_or_zero(data.get("avg_generation_tps")), 2),
        "primary_model": str(primary) if primary else None,
        "model_memory_used": model_memory_used,
        "total_active_requests": _int_or_zero(data.get("total_active_requests")),
        "total_waiting_requests": _int_or_zero(data.get("total_waiting_requests")),
    }


def _normalize_omlx_activity_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize oMLX /admin/api/activity live throughput."""
    return {
        "prefill_tps": round(
            _float_or_zero(
                data.get("prefill_tps")
                or data.get("avg_prefill_tps")
                or data.get("live_prefill_tps")
            ),
            2,
        ),
        "generation_tps": round(
            _float_or_zero(
                data.get("generation_tps")
                or data.get("avg_generation_tps")
                or data.get("live_generation_tps")
            ),
            2,
        ),
        "active_requests": _int_or_zero(data.get("active_requests")),
        "waiting_requests": _int_or_zero(data.get("waiting_requests")),
    }


async def probe_omlx_telemetry(
    backends: list[Any],
    client: Any,
) -> dict[str, Any] | None:
    """Probe oMLX admin for serving stats + live activity (best-effort)."""
    base = _best_omlx_base_url(backends)
    if base is None:
        return None
    service_base = _omlx_service_base(base)
    admin_url = f"{service_base}/admin"
    loaded = await probe_omlx_admin(base, client)
    session: dict[str, Any] | None = None
    alltime: dict[str, Any] | None = None
    live: dict[str, Any] | None = None
    try:
        resp = await client.get(
            f"{service_base}/admin/api/stats",
            params={"scope": "session"},
            timeout=2.0,
        )
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            session = _normalize_omlx_stats_scope(resp.json())
    except Exception:
        pass
    try:
        resp = await client.get(
            f"{service_base}/admin/api/stats",
            params={"scope": "alltime"},
            timeout=2.0,
        )
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            alltime = _normalize_omlx_stats_scope(resp.json())
    except Exception:
        pass
    try:
        resp = await client.get(f"{service_base}/admin/api/activity", timeout=2.0)
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            live = _normalize_omlx_activity_payload(resp.json())
    except Exception:
        pass
    if not any((session, alltime, live, loaded)):
        return {"available": False, "admin_url": admin_url}
    primary_model = None
    model_memory_used = 0
    loaded_models: list[str] = []
    if loaded:
        loaded_models = loaded.get("loaded_models", [])
        primary_model = loaded.get("primary_loaded_model")
    if session:
        primary_model = primary_model or None
    return {
        "available": True,
        "admin_url": admin_url,
        "session": session,
        "alltime": alltime,
        "live": live or {"prefill_tps": 0.0, "generation_tps": 0.0},
        "primary_model": primary_model,
        "loaded_models": loaded_models,
        "model_memory_used": model_memory_used,
    }
