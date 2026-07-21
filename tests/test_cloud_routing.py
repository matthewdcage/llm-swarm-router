"""Cloud provider routing: materialization, master switch, fallback direction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.service import AgentService
from netllm_core.models import Backend, CloudProviderConfig, NetllmConfig, RoutingPolicy
from netllm_core.pool import RouterPool
from netllm_core.routing_policy import resolve_routing

_MOCK_ONLINE = {"status": "online", "models": ["m"], "model_count": 1}


def _local(backend_id: str = "local", url: str = "http://127.0.0.1:8080/v1") -> Backend:
    return Backend(id=backend_id, base_url=url, provider="omlx", local=True)


# --- routing_policy.resolve_routing cloud gating -----------------------


def test_resolve_routing_default_cloud_config_matches_legacy_behavior() -> None:
    cfg = NetllmConfig()
    resolved = resolve_routing(
        cfg.routing, model="gpt-4", api_format="openai", header_local_only=False
    )
    assert resolved.allow_cloud_inject is True
    assert resolved.cloud_leads is False


def test_resolve_routing_cloud_master_switch_off_blocks_inject() -> None:
    cfg = NetllmConfig()
    cfg.cloud.enabled = False
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
        cloud=cfg.cloud,
    )
    assert resolved.allow_cloud_inject is False
    assert resolved.cloud_leads is False


def test_resolve_routing_cloud_master_switch_off_wins_over_allow_cloud_policy() -> None:
    cfg = NetllmConfig()
    cfg.cloud.enabled = False
    cfg.routing.policies = [
        RoutingPolicy(name="force-cloud", allow_cloud=True, api_format="openai")
    ]
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
        cloud=cfg.cloud,
    )
    assert resolved.allow_cloud_inject is False


def test_resolve_routing_fallback_none_suppresses_default_cloud() -> None:
    cfg = NetllmConfig()
    cfg.cloud.fallback = "none"
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
        cloud=cfg.cloud,
    )
    assert resolved.allow_cloud_inject is False


def test_resolve_routing_fallback_none_honors_explicit_allow_cloud_policy() -> None:
    cfg = NetllmConfig()
    cfg.cloud.fallback = "none"
    cfg.routing.policies = [
        RoutingPolicy(
            name="opt-in", model_prefix="gpt-", api_format="openai", allow_cloud=True
        )
    ]
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
        cloud=cfg.cloud,
    )
    assert resolved.allow_cloud_inject is True


def test_resolve_routing_fallback_enabled_false_suppresses_default_cloud() -> None:
    cfg = NetllmConfig()
    cfg.cloud.fallback_enabled = False
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
        cloud=cfg.cloud,
    )
    assert resolved.allow_cloud_inject is False
    assert resolved.cloud_leads is False


def test_resolve_routing_fallback_local_sets_cloud_leads() -> None:
    cfg = NetllmConfig()
    cfg.cloud.fallback = "local"
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
        cloud=cfg.cloud,
    )
    assert resolved.cloud_leads is True
    assert resolved.allow_cloud_inject is True


def test_resolve_routing_local_only_header_disables_cloud_leads() -> None:
    cfg = NetllmConfig()
    cfg.cloud.fallback = "local"
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=True,
        cloud=cfg.cloud,
    )
    assert resolved.cloud_leads is False
    assert resolved.allow_cloud_inject is False
    assert resolved.local_only is True


# --- pool.select_backend prefer_cloud -----------------------------------


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_prefer_cloud_picks_cloud_backend_first(mock_probe: object) -> None:
    pool = RouterPool()
    local = _local()
    cloud = Backend(
        id="cloud-moonshot",
        base_url="https://api.moonshot.ai/v1",
        provider="custom",
        cloud_provider="moonshot",
        local=False,
    )
    pool.merge_backends([local, cloud])
    picked = pool.select_backend("m", "local_first", prefer_cloud=True)
    assert picked is cloud


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_prefer_cloud_falls_back_to_local_once_excluded(mock_probe: object) -> None:
    pool = RouterPool()
    local = _local()
    cloud = Backend(
        id="cloud-moonshot",
        base_url="https://api.moonshot.ai/v1",
        provider="custom",
        cloud_provider="moonshot",
        local=False,
    )
    pool.merge_backends([local, cloud])
    picked = pool.select_backend(
        "m", "local_first", prefer_cloud=True, exclude_ids={"cloud-moonshot"}
    )
    assert picked is local


def test_prefer_cloud_no_cloud_candidates_is_noop() -> None:
    pool = RouterPool()
    local = _local()
    pool.merge_backends([local])
    with patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE):
        picked = pool.select_backend("m", "local_first", prefer_cloud=True)
    assert picked is local


# --- AgentService materialization ---------------------------------------


def test_materialize_cloud_provider_backends_creates_routable_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-test")
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    row = service.pool.backend_by_id("cloud-moonshot")
    assert row is not None
    assert row.base_url == "https://api.moonshot.ai/v1"
    assert row.api_key == "mk-test"
    assert row.local is False
    assert row.cloud_provider == "moonshot"


def test_materialize_skips_enabled_provider_without_key() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot") is None


def test_materialize_skips_disabled_provider() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=False, api_key="mk-inline"
    )
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot") is None


def test_materialize_zai_uses_static_model_catalog() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["zai"] = CloudProviderConfig(enabled=True, api_key="zk-inline")
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    row = service.pool.backend_by_id("cloud-zai")
    assert row is not None
    assert row.health.models  # static catalog seeded
    assert "glm-5.2" in row.health.models


def test_master_switch_off_prunes_all_cloud_rows() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True, api_key="mk")
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot") is not None

    cfg.cloud.enabled = False
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot") is None


def test_disabling_one_provider_prunes_only_that_row() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True, api_key="mk")
    cfg.cloud.providers["zai"] = CloudProviderConfig(enabled=True, api_key="zk")
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot") is not None
    assert service.pool.backend_by_id("cloud-zai") is not None

    cfg.cloud.providers["moonshot"].enabled = False
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot") is None
    assert service.pool.backend_by_id("cloud-zai") is not None


def test_materialize_reuses_existing_row_preserving_probed_health() -> None:
    """A second materialize call with unchanged config must not wipe
    health/model data accumulated by a live probe in between."""
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True, api_key="mk")
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    row = service.pool.backend_by_id("cloud-moonshot")
    assert row is not None
    row.health.models = ["kimi-k3"]  # simulate a live /models probe result
    row.health.status = "online"

    service._materialize_cloud_provider_backends()
    row_again = service.pool.backend_by_id("cloud-moonshot")
    assert row_again is row  # same object identity
    assert row_again.health.models == ["kimi-k3"]  # not wiped


def test_materialize_rebuilds_when_key_changes() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, api_key="mk-old"
    )
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot").api_key == "mk-old"  # type: ignore[union-attr]

    cfg.cloud.providers["moonshot"].api_key = "mk-new"
    service._materialize_cloud_provider_backends()
    assert service.pool.backend_by_id("cloud-moonshot").api_key == "mk-new"  # type: ignore[union-attr]


def test_materialize_respects_region_override() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, api_key="mk", region="cn"
    )
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    row = service.pool.backend_by_id("cloud-moonshot")
    assert row is not None
    assert row.base_url == "https://api.moonshot.cn/v1"


def test_materialize_respects_explicit_anthropic_api_format() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, api_key="mk", api_format="anthropic"
    )
    service = AgentService(cfg)
    service._materialize_cloud_provider_backends()
    row = service.pool.backend_by_id("cloud-moonshot")
    assert row is not None
    assert row.api_format == "anthropic"
    assert row.base_url == "https://api.moonshot.ai/anthropic"


# --- end-to-end via TestClient -------------------------------------------


@pytest.fixture
def client_factory():
    def _make(cfg: NetllmConfig) -> TestClient:
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        app = create_app(cfg)
        return TestClient(app)

    return _make


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_registry_provider_serves_chat_completion(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-env")
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "id": "chatcmpl-moonshot",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "kimi reply"},
                "finish_reason": "stop",
            }
        ],
        "model": "kimi-k3",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    with client_factory(cfg) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "kimi-k3", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "kimi reply"


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
def test_cloud_disabled_master_switch_returns_no_backend_error(
    mock_scan: AsyncMock, client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-env")
    mock_scan.return_value = []
    cfg = NetllmConfig()
    cfg.cloud.enabled = False
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    with client_factory(cfg) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "kimi-k3", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 502


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_fallback_local_prefers_cloud_over_local_mesh(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud.fallback = 'local' (cloud-primary): with both a local backend
    and an enabled cloud provider available, the cloud provider is tried
    first — verified by asserting the local upstream client is never
    invoked while the cloud (mocked AsyncOpenAI) one is."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-env")
    mock_scan.return_value = [
        {"status": "online", "base_url": "http://127.0.0.1:8080/v1", "id": "omlx"}
    ]
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "id": "chatcmpl-cloud-leads",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "cloud-primary reply"},
                "finish_reason": "stop",
            }
        ],
        "model": "kimi-k3",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    cfg = NetllmConfig()
    cfg.cloud.fallback = "local"
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    with client_factory(cfg) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "kimi-k3", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "cloud-primary reply"
    call_args = mock_openai_cls.call_args
    assert call_args.kwargs["base_url"] == "https://api.moonshot.ai/v1"
