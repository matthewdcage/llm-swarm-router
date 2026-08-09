"""Per-URL backend credential helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from netllm_core.backend_credentials import (
    backend_override_for_url,
    discovery_urls,
    normalize_backend_url,
    prune_orphan_backend_overrides,
    resolve_api_key_for_url,
    upsert_backend_credential,
)
from netllm_core.models import BackendOverride, NetllmConfig


def test_normalize_backend_url_appends_v1() -> None:
    assert normalize_backend_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/v1"


def test_resolve_api_key_prefers_url_override() -> None:
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(
            base_url="http://127.0.0.1:8000/v1",
            provider="vllm",
            api_key="key-a",
        ),
        BackendOverride(
            base_url="http://127.0.0.1:8001/v1",
            provider="vllm",
            api_key="key-b",
        ),
    ]
    assert resolve_api_key_for_url("http://127.0.0.1:8000/v1", "vllm", cfg) == "key-a"
    assert resolve_api_key_for_url("http://127.0.0.1:8001/v1", "vllm", cfg) == "key-b"


def test_resolve_api_key_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VLLM_API_KEY", "env-key")
    cfg = NetllmConfig()
    assert resolve_api_key_for_url("http://127.0.0.1:8000/v1", "vllm", cfg) == "env-key"


def test_upsert_backend_credential_updates_existing() -> None:
    cfg = NetllmConfig()
    upsert_backend_credential(cfg, "http://127.0.0.1:8000/v1", "vllm", api_key="first")
    upsert_backend_credential(cfg, "http://127.0.0.1:8000/v1", "vllm", api_key="second")
    assert len(cfg.routing.backends) == 1
    assert cfg.routing.backends[0].api_key == "second"


def test_discovery_urls_lists_provider_and_custom() -> None:
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {"vllm": ["http://127.0.0.1:8001/v1"]}
    cfg.discovery.custom_endpoints = ["http://127.0.0.1:9000"]
    rows = discovery_urls(cfg)
    assert ("http://127.0.0.1:8001/v1", "vllm") in rows
    assert ("http://127.0.0.1:9000/v1", "custom") in rows


def test_prune_orphan_backend_overrides_removes_credential_only() -> None:
    cfg = NetllmConfig()
    cfg.discovery.custom_endpoints = ["http://127.0.0.1:9000/v1"]
    cfg.routing.backends = [
        BackendOverride(
            base_url="http://127.0.0.1:9000/v1",
            provider="custom",
            api_key="gone",
        ),
        BackendOverride(
            base_url="http://127.0.0.1:8000/v1",
            provider="vllm",
            api_key="keep",
            max_concurrency=2,
        ),
    ]
    removed = prune_orphan_backend_overrides(cfg)
    assert removed == 0
    cfg.discovery.custom_endpoints = []
    removed = prune_orphan_backend_overrides(cfg)
    assert removed == 1
    assert len(cfg.routing.backends) == 1
    assert cfg.routing.backends[0].api_key == "keep"


@pytest.mark.asyncio
async def test_custom_endpoint_scan_uses_url_api_key() -> None:
    from netllm_core.models import NetllmConfig
    from netllm_discovery.local import scan_local_providers

    cfg = NetllmConfig()
    cfg.discovery.providers = []
    cfg.discovery.custom_endpoints = ["http://127.0.0.1:9000/v1"]
    cfg.routing.backends = [
        BackendOverride(
            base_url="http://127.0.0.1:9000/v1",
            provider="custom",
            api_key="custom-secret",
        )
    ]

    async def fake_probe(
        url: str, client, api_key: str = "", *, diagnose: bool = False
    ):
        if api_key == "custom-secret" and url == "http://127.0.0.1:9000/v1":
            return {
                "status": "online",
                "model_count": 1,
                "models": ["m1"],
                "http_status": 200,
            }
        return None

    with patch("netllm_discovery.local._probe_url", side_effect=fake_probe):
        results = await scan_local_providers(cfg)

    custom = next(r for r in results if r.get("id") == "custom")
    assert custom["status"] == "online"
    assert custom["api_key"] == "custom-secret"


def test_backend_override_for_url_trailing_slash() -> None:
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(base_url="http://127.0.0.1:8000/v1/", provider="vllm")
    ]
    assert backend_override_for_url(cfg, "http://127.0.0.1:8000/v1") is not None
