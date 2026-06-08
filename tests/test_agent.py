"""Tests for netllm-agent HTTP surface."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import Backend, BackendHealth, NetllmConfig, load_config


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_root_help(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "netllm-agent"
    assert "openai_base_url" in data
    assert "/v1/models" in data["endpoints"]["models"]
    assert "/ui/" in data["endpoints"]["dashboard"]


def test_root_redirects_browser_to_ui(client: TestClient) -> None:
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/ui/"


def test_ui_dashboard(client: TestClient) -> None:
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "dashboard" in resp.text.lower()
    assert "llm-swarm-router" in resp.text.lower()
    assert "dashboard.css" in resp.text
    assert "dashboard.js" in resp.text


def test_client_env_endpoint(client: TestClient) -> None:
    resp = client.get("/netllm/v1/client-env")
    assert resp.status_code == 200
    vars_ = resp.json()["vars"]
    assert vars_["OPENAI_API_KEY"] == "netllm-local"
    assert vars_["OPENAI_BASE_URL"].endswith("/v1")


def test_doctor_endpoint(client: TestClient) -> None:
    resp = client.get("/netllm/v1/doctor")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert "issues" in data


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics_endpoint(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"netllm_requests_total" in resp.content


def test_netllm_status(client: TestClient) -> None:
    resp = client.get("/netllm/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "agent_id" in data
    assert "routing_strategy" in data


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
def test_models_list_empty(mock_scan: AsyncMock, client: TestClient) -> None:
    mock_scan.return_value = []
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["object"] == "list"


@patch(
    "netllm_agent.service.AgentService.proxy_chat_completion",
    new_callable=AsyncMock,
)
def test_chat_completion_proxy(mock_proxy: AsyncMock, client: TestClient) -> None:
    mock_proxy.return_value = {
        "id": "cmpl-test",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    }
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hi"


def test_heartbeat_registers_peer(client: TestClient) -> None:
    payload = {
        "agent_id": "remote-1",
        "listen_url": "http://192.168.1.99:11400",
        "role": "peer",
        "hostname": "remote-mac",
        "backends": [
            Backend(
                id="b1",
                base_url="http://192.168.1.99:8080/v1",
                provider="omlx",
                local=False,
                health=BackendHealth(status="online", models=["m1"]),
            ).model_dump(mode="json")
        ],
    }
    resp = client.post("/netllm/v1/heartbeat", json=payload)
    assert resp.status_code == 204
    status = client.get("/netllm/v1/status").json()
    peer_ids = [p["agent_id"] for p in status.get("peers", [])]
    assert "remote-1" in peer_ids


def test_root_lists_messages_endpoint(client: TestClient) -> None:
    resp = client.get("/")
    data = resp.json()
    assert "/v1/messages" in data["endpoints"]["messages"]


@patch(
    "netllm_agent.service.AgentService.proxy_messages",
    new_callable=AsyncMock,
)
def test_messages_proxy(mock_proxy: AsyncMock, client: TestClient) -> None:
    mock_proxy.return_value = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "test-model",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    resp = client.post(
        "/v1/messages",
        json={
            "model": "test-model",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-api-key": "test-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "hello"


async def _fake_messages_stream(*_args, **_kwargs):
    yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'


@patch(
    "netllm_agent.service.AgentService.proxy_messages_stream",
    side_effect=_fake_messages_stream,
)
def test_messages_stream_proxy(_mock_stream, client: TestClient) -> None:
    resp = client.post(
        "/v1/messages",
        json={
            "model": "test-model",
            "max_tokens": 10,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-api-key": "test-key"},
    )
    assert resp.status_code == 200
    assert "message_stop" in resp.text


def test_admin_config_save_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        from netllm_core.models import save_config

        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={"routing": {"default_strategy": "round_robin"}},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["path"] == str(cfg_path)
            reloaded = load_config(cfg_path)
            assert reloaded.routing.default_strategy == "round_robin"
            summary = client.get("/netllm/v1/config").json()
            assert summary["routing"]["default_strategy"] == "round_robin"


def test_admin_config_requires_loopback() -> None:
    from unittest.mock import MagicMock

    from fastapi import HTTPException
    from netllm_agent.admin import require_admin_access

    cfg = NetllmConfig()
    request = MagicMock()
    request.client.host = "203.0.113.1"
    request.headers.get.return_value = ""
    with pytest.raises(HTTPException) as exc:
        require_admin_access(request, cfg)
    assert exc.value.status_code == 403


@patch("netllm_discovery.lan.subnet_scan_agents", new_callable=AsyncMock)
def test_admin_peers_scan(mock_scan: AsyncMock) -> None:
    mock_scan.return_value = [
        {
            "agent_id": "peer-a",
            "listen_url": "http://10.0.0.5:11400",
            "role": "peer",
            "hostname": "other-mac",
        }
    ]
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as client:
        resp = client.post("/netllm/v1/admin/peers-scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["peers"]) == 1
        assert data["peers"][0]["agent_id"] == "peer-a"
