"""Local inference server discovery (oMLX, Ollama, LM Studio)."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx
from netllm_core.health import diagnose_backend, probe_openai_compat
from netllm_core.models import Backend, BackendHealth, NetllmConfig, infer_api_format

KNOWN_PROVIDERS: list[tuple[str, str, list[str]]] = [
    (
        "omlx",
        "oMLX (Apple Silicon)",
        [
            "http://127.0.0.1:8080/v1",
            "http://localhost:8080/v1",
        ],
    ),
    (
        "ollama",
        "Ollama",
        [
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434/v1",
        ],
    ),
    (
        "lmstudio",
        "LM Studio",
        [
            "http://127.0.0.1:1234/v1",
            "http://localhost:1234/v1",
        ],
    ),
]

DEFAULT_API_KEYS: dict[str, str] = {
    "omlx": "omlx-local",
}


def _api_key_for_provider(provider_id: str, config: NetllmConfig) -> str:
    for override in config.routing.backends:
        if override.provider == provider_id:
            return override.resolve_api_key()
    env_map = {
        "omlx": "OMLX_API_KEY",
        "ollama": "OLLAMA_API_KEY",
    }
    env_name = env_map.get(provider_id, "")
    if env_name:
        return os.environ.get(env_name, DEFAULT_API_KEYS.get(provider_id, ""))
    return DEFAULT_API_KEYS.get(provider_id, "")


async def _probe_provider(
    provider_id: str,
    display_name: str,
    candidate_urls: list[str],
    client: httpx.AsyncClient,
    api_key: str = "",
) -> dict[str, Any]:
    for base_url in candidate_urls:
        result = await probe_openai_compat(base_url, client, api_key=api_key or None)
        if result.get("status") == "online":
            diag = await diagnose_backend(base_url, client, api_key=api_key or None)
            return {
                "id": provider_id,
                "name": display_name,
                "base_url": base_url,
                "api_key": api_key,
                "auth_hint": (
                    "omlx-local"
                    if provider_id == "omlx" and api_key == "omlx-local"
                    else ("configured" if api_key else "none")
                ),
                **result,
                "latency_ms": diag.get("latency_ms"),
                "inference_status": diag.get("inference_status"),
            }
    return {
        "id": provider_id,
        "name": display_name,
        "base_url": candidate_urls[0],
        "status": "offline",
        "model_count": 0,
        "models": [],
    }


async def scan_local_providers(
    config: NetllmConfig | None = None,
    *,
    include_custom: bool = True,
) -> list[dict[str, Any]]:
    """Probe known local ports and custom endpoints."""
    cfg = config or NetllmConfig()
    enabled = set(cfg.discovery.providers)
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        tasks = []
        for pid, pname, urls in KNOWN_PROVIDERS:
            if pid not in enabled:
                continue
            key = _api_key_for_provider(pid, cfg)
            tasks.append(_probe_provider(pid, pname, urls, client, key))
        if include_custom:
            for url in cfg.discovery.custom_endpoints:
                tasks.append(_probe_provider("custom", "Custom", [url], client, ""))
        for override in cfg.routing.backends:
            if override.enabled and override.base_url:
                tasks.append(
                    _probe_provider(
                        override.provider,
                        override.provider,
                        [override.base_url],
                        client,
                        override.resolve_api_key(),
                    )
                )
        if tasks:
            results = list(await asyncio.gather(*tasks))

    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in results:
        url = r.get("base_url", "")
        if url in seen_urls:
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
                    model_count=r.get("model_count", 0),
                    models=r.get("models") or [],
                    latency_p50_ms=float(r["latency_ms"])
                    if r.get("latency_ms") is not None
                    else None,
                ),
            )
        )
    return backends
