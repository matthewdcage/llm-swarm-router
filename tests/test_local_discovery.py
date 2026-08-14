"""Tests for local provider discovery (ports, config overrides, env)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from netllm_core.models import Backend, BackendHealth, NetllmConfig
from netllm_discovery.local import (
    _normalize_omlx_admin_payload,
    candidate_urls_for_provider,
    find_omlx_admin_url,
    merge_discovered_provider_urls,
    normalize_openai_base_url,
    omlx_admin_url,
    probe_omlx_admin,
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
            "model_count": 1,
        },
        {"id": "ollama", "status": "offline", "base_url": ""},
    ]
    merge_discovered_provider_urls(cfg, results)
    assert cfg.discovery.provider_urls["omlx"] == ["http://127.0.0.1:8088/v1"]


@pytest.mark.asyncio
async def test_scan_finds_omlx_on_alternate_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = NetllmConfig()
    # omlx is macOS-default only; include explicitly so scan runs on Linux/Windows CI.
    cfg.discovery.providers = ["omlx", "ollama", "lmstudio", "vllm"]
    monkeypatch.setenv("OMLX_PORT", "8088")

    async def fake_probe(
        url: str, client, api_key: str = "", *, diagnose: bool = False
    ) -> dict | None:
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
async def test_scan_prefers_vllm_url_with_models_over_auth_only_port() -> None:
    """Colibri on :8000 can look 'online' (401) while vLLM serves on :8013."""
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {
        "vllm": ["http://127.0.0.1:8000/v1", "http://127.0.0.1:8013/v1"],
    }

    async def fake_probe(
        url: str, client, api_key: str = "", *, diagnose: bool = False
    ) -> dict | None:
        if url == "http://127.0.0.1:8000/v1":
            return {
                "status": "online",
                "model_count": 0,
                "models": [],
                "detail": "authentication required",
            }
        if url == "http://127.0.0.1:8013/v1":
            return {
                "status": "online",
                "model_count": 1,
                "models": ["qwen3.6-35b"],
            }
        return None

    with patch("netllm_discovery.local._probe_url", side_effect=fake_probe):
        results = await scan_local_providers(cfg)

    vllm = next(r for r in results if r["id"] == "vllm")
    assert vllm["base_url"] == "http://127.0.0.1:8013/v1"
    assert vllm["model_count"] == 1


def test_merge_discovered_provider_urls_skips_zero_model_online() -> None:
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {"vllm": ["http://127.0.0.1:8013/v1"]}
    results = [
        {
            "id": "vllm",
            "status": "online",
            "base_url": "http://127.0.0.1:8000/v1",
            "model_count": 0,
        },
    ]
    merge_discovered_provider_urls(cfg, results)
    assert cfg.discovery.provider_urls["vllm"] == ["http://127.0.0.1:8013/v1"]


@pytest.mark.asyncio
async def test_scan_finds_vllm() -> None:
    cfg = NetllmConfig()

    async def fake_probe(
        url: str, client, api_key: str = "", *, diagnose: bool = False
    ) -> dict | None:
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
    cfg.discovery.providers = ["omlx", "ollama", "lmstudio", "vllm"]
    cfg.discovery.provider_urls = {"omlx": ["http://127.0.0.1:9999/v1"]}
    seen: list[str] = []

    async def fake_probe(
        url: str, client, api_key: str = "", *, diagnose: bool = False
    ) -> dict | None:
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


def test_omlx_admin_url_from_base() -> None:
    assert omlx_admin_url("http://127.0.0.1:8088/v1") == "http://127.0.0.1:8088/admin"
    assert omlx_admin_url("http://127.0.0.1:8080/v1/") == "http://127.0.0.1:8080/admin"


def test_find_omlx_admin_url_from_backends() -> None:
    backends = [
        Backend(
            id="1",
            base_url="http://127.0.0.1:8088/v1",
            provider="omlx",
            health=BackendHealth(status="online"),
        ),
        Backend(
            id="2",
            base_url="http://127.0.0.1:11434/v1",
            provider="ollama",
            health=BackendHealth(status="online"),
        ),
    ]
    assert find_omlx_admin_url(backends) == "http://127.0.0.1:8088/admin"


def test_normalize_omlx_admin_payload_active_models() -> None:
    data = {"active_models": [{"id": "qwen3-4b"}, {"name": "llama"}]}
    stats = _normalize_omlx_admin_payload(data)
    assert stats is not None
    assert stats["primary_loaded_model"] == "qwen3-4b"
    assert stats["loaded_models"] == ["qwen3-4b", "llama"]


def test_normalize_omlx_stats_scope() -> None:
    from netllm_discovery.local import _normalize_omlx_stats_scope

    data = {
        "total_prompt_tokens": 100,
        "total_completion_tokens": 40,
        "total_cached_tokens": 50,
        "total_requests": 3,
        "avg_prefill_tps": 10.5,
        "avg_generation_tps": 4.2,
    }
    out = _normalize_omlx_stats_scope(data)
    assert out["total_tokens"] == 140
    assert out["cache_efficiency_pct"] == 50.0


@pytest.mark.asyncio
async def test_probe_omlx_admin_parses_server_info() -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"loaded_models": ["demo-model"]}

    class FakeClient:
        async def get(self, url: str, timeout: float = 2.0) -> FakeResponse:
            if url.endswith("/api/server-info"):
                return FakeResponse()
            raise AssertionError(f"unexpected url {url}")

    stats = await probe_omlx_admin("http://127.0.0.1:8099/v1", FakeClient())  # type: ignore[arg-type]
    assert stats is not None
    assert stats["primary_loaded_model"] == "demo-model"
    assert stats["admin_url"] == "http://127.0.0.1:8099/admin"


@pytest.mark.asyncio
async def test_routine_scan_never_runs_inference_diagnose() -> None:
    """Routine scans must not 1-token-probe: with chat-capable model
    selection the diagnose forces the provider to LOAD a model, which
    evicts the resident model on memory-tight hosts every scan cycle."""
    cfg = NetllmConfig()
    cfg.discovery.providers = ["omlx"]
    calls: list[str] = []

    async def fake_diagnose(base_url: str, client, *, api_key=None, model=None):
        calls.append(base_url)
        return {"latency_ms": 1, "inference_status": "online"}

    async def fake_health(base_url: str, client, *, api_key=None):
        return {"status": "online", "models": ["m"], "model_count": 1}

    with (
        patch("netllm_discovery.local.probe_openai_compat", side_effect=fake_health),
        patch("netllm_discovery.local.diagnose_backend", side_effect=fake_diagnose),
    ):
        await scan_local_providers(cfg)
        assert calls == []
        await scan_local_providers(cfg, diagnose=True)
        assert calls  # explicit opt-in still diagnoses


# --- F-06: TLS verification is only waived for loopback --------------------


async def test_remote_override_probe_uses_a_verifying_client() -> None:
    """verify=False is defensible for localhost (the bundled macOS Python may
    have no trust store) but the same scan also probes custom_endpoints and
    [[routing.backends]] overrides, which can be remote https:// URLs whose
    probe responses become the catalog that drives routing."""
    from unittest.mock import patch

    from netllm_core.models import BackendOverride, NetllmConfig
    from netllm_discovery import local as local_mod

    cfg = NetllmConfig()
    cfg.discovery.providers = []
    cfg.routing.backends = [
        BackendOverride(base_url="https://remote.example/v1", provider="custom")
    ]

    seen: list[bool] = []
    real_local = local_mod.loopback_async_client
    real_remote = local_mod.verifying_async_client

    def spy_local(**kw):
        seen.append(False)
        return real_local(**kw)

    def spy_remote(**kw):
        seen.append(True)
        return real_remote(**kw)

    with patch.object(local_mod, "loopback_async_client", spy_local):
        with patch.object(local_mod, "verifying_async_client", spy_remote):
            with patch.object(local_mod, "_probe_provider") as probe:

                async def noop(*a, **k):
                    return {"id": "custom", "status": "offline", "base_url": ""}

                probe.side_effect = noop
                await local_mod.scan_local_providers(cfg)

    assert True in seen, "a verifying client must be constructed for the scan"
    # The remote override was routed through the verifying client.
    used_clients = [c.args[3] for c in probe.call_args_list]
    assert used_clients, "the override should have been probed"


def test_loopback_targets_are_detected() -> None:
    from netllm_discovery.local import _is_loopback_target

    assert _is_loopback_target("http://127.0.0.1:8080/v1") is True
    assert _is_loopback_target("http://localhost:1234/v1") is True
    assert _is_loopback_target("https://remote.example/v1") is False
    assert _is_loopback_target("http://192.168.1.50:8000/v1") is False
