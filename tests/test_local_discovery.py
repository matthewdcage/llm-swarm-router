"""Tests for local provider discovery (ports, config overrides, env)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.local import (
    candidate_urls_for_provider,
    merge_discovered_provider_urls,
    normalize_openai_base_url,
    scan_local_providers,
)


def test_normalize_openai_base_url() -> None:
    assert (
        normalize_openai_base_url("http://127.0.0.1:8088") == "http://127.0.0.1:8088/v1"
    )
    assert (
        normalize_openai_base_url("http://127.0.0.1:8088/v1")
        == "http://127.0.0.1:8088/v1"
    )
    assert (
        normalize_openai_base_url("http://127.0.0.1:8088/v1/")
        == "http://127.0.0.1:8088/v1"
    )


def test_candidate_urls_config_override_first() -> None:
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {"omlx": ["http://127.0.0.1:8088/v1"]}
    urls = candidate_urls_for_provider("omlx", cfg)
    assert urls[0] == "http://127.0.0.1:8088/v1"
    assert "http://127.0.0.1:8080/v1" in urls


def test_candidate_urls_ollama_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.10:11435")
    cfg = NetllmConfig()
    urls = candidate_urls_for_provider("ollama", cfg)
    assert urls[0] == "http://192.168.1.10:11435/v1"


def test_candidate_urls_omlx_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMLX_PORT", "8088,9090")
    cfg = NetllmConfig()
    urls = candidate_urls_for_provider("omlx", cfg)
    assert "http://127.0.0.1:8088/v1" in urls
    assert "http://127.0.0.1:9090/v1" in urls


def test_merge_discovered_provider_urls() -> None:
    cfg = NetllmConfig()
    results = [
        {
            "id": "omlx",
            "status": "online",
            "base_url": "http://127.0.0.1:8088/v1",
        },
        {"id": "ollama", "status": "offline", "base_url": ""},
    ]
    merge_discovered_provider_urls(cfg, results)
    assert cfg.discovery.provider_urls["omlx"] == ["http://127.0.0.1:8088/v1"]


@pytest.mark.asyncio
async def test_scan_finds_omlx_on_alternate_port() -> None:
    cfg = NetllmConfig()

    async def fake_probe(url: str, client, api_key: str = "") -> dict | None:
        if url == "http://127.0.0.1:8088/v1":
            return {
                "status": "online",
                "model_count": 2,
                "models": ["a", "b"],
                "latency_ms": 12.0,
                "inference_status": "online",
            }
        return None

    with patch("netllm_discovery.local._probe_url", side_effect=fake_probe):
        results = await scan_local_providers(cfg)

    omlx = next(r for r in results if r["id"] == "omlx")
    assert omlx["status"] == "online"
    assert omlx["base_url"] == "http://127.0.0.1:8088/v1"
    assert omlx["model_count"] == 2


@pytest.mark.asyncio
async def test_scan_finds_vllm() -> None:
    cfg = NetllmConfig()

    async def fake_probe(url: str, client, api_key: str = "") -> dict | None:
        if url == "http://127.0.0.1:8000/v1":
            return {
                "status": "online",
                "model_count": 1,
                "models": ["meta-llama/Llama-3-8B"],
                "latency_ms": 20.0,
                "inference_status": "online",
            }
        return None

    with patch("netllm_discovery.local._probe_url", side_effect=fake_probe):
        results = await scan_local_providers(cfg)

    vllm = next(r for r in results if r["id"] == "vllm")
    assert vllm["status"] == "online"
    assert vllm["base_url"] == "http://127.0.0.1:8000/v1"


def test_linux_default_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from netllm_core.platform import default_discovery_providers

    monkeypatch.setattr("sys.platform", "linux")
    providers = default_discovery_providers()
    assert "vllm" in providers
    assert "omlx" not in providers


@pytest.mark.asyncio
async def test_scan_uses_config_provider_url_before_scan() -> None:
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {"omlx": ["http://127.0.0.1:9999/v1"]}
    seen: list[str] = []

    async def fake_probe(url: str, client, api_key: str = "") -> dict | None:
        seen.append(url)
        if url == "http://127.0.0.1:9999/v1":
            return {
                "status": "online",
                "model_count": 1,
                "models": ["x"],
                "latency_ms": 5.0,
                "inference_status": "online",
            }
        return None

    with patch("netllm_discovery.local._probe_url", side_effect=fake_probe):
        results = await scan_local_providers(cfg)

    omlx = next(r for r in results if r["id"] == "omlx")
    assert omlx["base_url"] == "http://127.0.0.1:9999/v1"
    assert seen[0] == "http://127.0.0.1:9999/v1"
