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
                base_url="http://127.0.0.1:8080/v1",
                provider="omlx",
                local=True,
                health=BackendHealth(status="online", models=["m1"]),
            ).model_dump(mode="json")
        ],
    }
    resp = client.post("/netllm/v1/heartbeat", json=payload)
    assert resp.status_code == 204
    status = client.get("/netllm/v1/status").json()
    peer_ids = [p["agent_id"] for p in status.get("peers", [])]
    assert "remote-1" in peer_ids
    remote_urls = [
        b["base_url"]
        for b in status.get("backends", [])
        if not b.get("local")
    ]
    assert "http://192.168.1.99:11400/v1" in remote_urls
    assert not any("127.0.0.1:8080" in u for u in remote_urls)


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


def test_admin_config_save_strips_self_peer_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.agent.listen = "0.0.0.0:11400"
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        from netllm_core.models import save_config

        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with (
            patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "swarm": {
                        "peers": [
                            "http://10.0.0.32:11400",
                            "http://10.0.0.5:11400",
                        ]
                    }
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert "warnings" in data
            reloaded = load_config(cfg_path)
            assert reloaded.swarm.peers == ["http://10.0.0.5:11400"]


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


def test_admin_config_rejects_remote_client() -> None:
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


@patch(
    "netllm_agent.admin.local_admin_client_hosts",
    return_value=frozenset({"127.0.0.1", "10.0.0.9"}),
)
def test_admin_allows_same_host_lan_ip(_mock_hosts: object) -> None:
    from unittest.mock import MagicMock

    from netllm_agent.admin import require_admin_access

    cfg = NetllmConfig()
    request = MagicMock()
    request.client.host = "10.0.0.9"
    request.headers.get.return_value = ""
    require_admin_access(request, cfg)


@patch("netllm_discovery.lan.subnet_scan_agents", new_callable=AsyncMock)
def test_admin_peers_scan_skips_self_on_save(mock_scan: AsyncMock) -> None:
    mock_scan.return_value = [
        {
            "agent_id": "self-agent",
            "listen_url": "http://10.0.0.32:11400",
            "role": "peer",
        },
        {
            "agent_id": "peer-b",
            "listen_url": "http://10.0.0.5:11400",
            "role": "peer",
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.agent.listen = "0.0.0.0:11400"
        cfg.swarm.mdns = False
        from netllm_core.models import save_config

        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with (
            patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"),
            TestClient(app) as client,
        ):
            resp = client.post("/netllm/v1/admin/peers-scan?save=true")
            assert resp.status_code == 200
            data = resp.json()
            assert any("Skipped" in w for w in data.get("warnings", []))
            reloaded = load_config(cfg_path)
            assert reloaded.swarm.peers == ["http://10.0.0.5:11400"]


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


def test_netllm_logs(client: TestClient) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        log_file = log_dir / "agent.log"
        log_file.write_text("line one\nline two\nline three\n", encoding="utf-8")
        cfg = NetllmConfig()
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        cfg.ui.log_dir = str(log_dir)
        app = create_app(cfg)
        with TestClient(app) as logs_client:
            resp = logs_client.get("/netllm/v1/logs?tail=2")
            assert resp.status_code == 200
            data = resp.json()
            assert data["exists"] is True
            assert data["log_file"] == str(log_file)
            assert data["tail"] == ["line two", "line three"]
            assert data["truncated"] is True
            assert data["size_bytes"] == log_file.stat().st_size


def test_netllm_logs_missing_file(client: TestClient) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = NetllmConfig()
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        cfg.ui.log_dir = str(Path(tmp) / "missing")
        app = create_app(cfg)
        with TestClient(app) as logs_client:
            resp = logs_client.get("/netllm/v1/logs")
            assert resp.status_code == 200
            data = resp.json()
            assert data["exists"] is False
            assert data["tail"] == []
            assert data["size_bytes"] == 0


def test_netllm_version(client: TestClient) -> None:
    resp = client.get("/netllm/v1/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"]
    assert data["platform"]
    assert "install_method" in data
    sdk = data.get("sdk_versions") or {}
    assert sdk.get("openai")
    assert sdk.get("anthropic")


@patch("netllm_agent.app.build_update_check_payload", new_callable=AsyncMock)
def test_netllm_update_check(mock_build: AsyncMock, client: TestClient) -> None:
    mock_build.return_value = {
        "current": "0.2.3.5",
        "latest": "0.2.4.0",
        "update_available": True,
        "prerelease": False,
        "release_notes_url": "https://github.com/matthewdcage/llm-swarm-router/releases/tag/v0.2.4.0",
        "download_url": "https://example.com/llm-swarm-router.dmg",
        "asset_name": "llm-swarm-router.dmg",
        "asset_size": 12345,
        "sha256": "abc",
        "upgrade_hint": None,
        "can_auto_install": False,
        "error": None,
    }
    resp = client.get("/netllm/v1/update/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["update_available"] is True
    assert data["latest"] == "0.2.4.0"
    mock_build.assert_awaited_once_with(force=False)


@patch("netllm_agent.app.build_update_check_payload", new_callable=AsyncMock)
def test_netllm_update_check_force(mock_build: AsyncMock, client: TestClient) -> None:
    mock_build.return_value = {
        "current": "0.2.3.5",
        "latest": "0.2.3.5",
        "update_available": False,
        "prerelease": False,
        "release_notes_url": "https://github.com/matthewdcage/llm-swarm-router/releases/latest",
        "download_url": None,
        "asset_name": None,
        "asset_size": None,
        "sha256": None,
        "upgrade_hint": None,
        "can_auto_install": False,
        "error": None,
    }
    resp = client.get("/netllm/v1/update/check?force=1")
    assert resp.status_code == 200
    mock_build.assert_awaited_once_with(force=True)


def test_status_includes_omlx_admin_url(client: TestClient) -> None:
    service = client.app.state.service
    service.pool.set_backends(
        [
            Backend(
                id="omlx-8080",
                base_url="http://127.0.0.1:8080/v1",
                provider="omlx",
                health=BackendHealth(status="online", model_count=1, models=["m0"]),
            ),
            Backend(
                id="omlx-8088",
                base_url="http://127.0.0.1:8088/v1",
                provider="omlx",
                health=BackendHealth(
                    status="online", model_count=2, models=["m1", "m2"]
                ),
            ),
        ]
    )
    data = service.status_payload()
    assert data.get("omlx_admin_url") == "http://127.0.0.1:8088/admin"


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_round_robin_routes_to_peer_agent_url(
    mock_openai_cls: object,
    mock_probe: object,
    mock_scan: AsyncMock,
) -> None:
    from unittest.mock import MagicMock

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.routing.default_strategy = "round_robin"
    cfg.routing.allow_remote = True

    mock_scan.return_value = [
        {
            "id": "omlx",
            "status": "online",
            "base_url": "http://127.0.0.1:8080/v1",
            "model_count": 1,
            "models": ["shared-model"],
        }
    ]
    mock_probe.return_value = {
        "status": "online",
        "models": ["shared-model"],
        "model_count": 1,
    }

    called_base_urls: list[str] = []

    mock_client = MagicMock()

    def track_openai_client(*_args: object, **kwargs: object) -> MagicMock:
        base = str(kwargs.get("base_url", "")).rstrip("/")
        if base:
            called_base_urls.append(base)
        return mock_client

    mock_openai_cls.side_effect = track_openai_client

    async def record_create(**kwargs: object) -> MagicMock:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "id": "cmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "model": kwargs.get("model", "shared-model"),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return mock_response

    mock_client.chat.completions.create = record_create

    app = create_app(cfg)
    with TestClient(app) as client:
        client.post(
            "/netllm/v1/heartbeat",
            json={
                "agent_id": "peer-remote",
                "listen_url": "http://192.168.1.11:11400",
                "role": "peer",
                "hostname": "mac-mini",
                "backends": [
                    Backend(
                        id="b1",
                        base_url="http://127.0.0.1:8080/v1",
                        provider="omlx",
                        local=True,
                        health=BackendHealth(
                            status="online", models=["shared-model"]
                        ),
                    ).model_dump(mode="json")
                ],
            },
        )
        for _ in range(2):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "shared-model",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200

    assert "http://127.0.0.1:8080/v1" in called_base_urls
    assert "http://192.168.1.11:11400/v1" in called_base_urls


def test_wants_local_only_header() -> None:
    from netllm_agent.service import AgentService

    assert AgentService._wants_local_only({"x-netllm-local-only": "1"})
    assert AgentService._wants_local_only({"x-netllm-local-only": "true"})
    assert not AgentService._wants_local_only({})
    assert not AgentService._wants_local_only({"x-netllm-local-only": "0"})
