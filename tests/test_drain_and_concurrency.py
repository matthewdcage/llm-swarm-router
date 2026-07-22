"""Drain toggle + per-machine max_concurrency: status/heartbeat wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.service import AgentService
from netllm_core.models import NetllmConfig
from netllm_discovery.swarm import PeerRecord


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_status_reports_draining_false_by_default(client: TestClient) -> None:
    resp = client.get("/netllm/v1/status")
    assert resp.status_code == 200
    assert resp.json()["draining"] is False


def test_status_reports_configured_max_concurrency() -> None:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.agent.max_concurrency = 6
    app = create_app(cfg)
    with TestClient(app) as client:
        resp = client.get("/netllm/v1/status")
    assert resp.status_code == 200
    assert resp.json()["max_concurrency"] == 6


def test_admin_drain_toggles_status(client: TestClient) -> None:
    resp = client.post("/netllm/v1/admin/drain", json={"draining": True})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "draining": True}
    assert client.get("/netllm/v1/status").json()["draining"] is True

    resp = client.post("/netllm/v1/admin/drain", json={"draining": False})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "draining": False}
    assert client.get("/netllm/v1/status").json()["draining"] is False


def test_admin_drain_requires_draining_key(client: TestClient) -> None:
    resp = client.post("/netllm/v1/admin/drain", json={})
    assert resp.status_code == 400


def test_admin_drain_never_persisted_to_config(tmp_path) -> None:
    """Drain is a runtime-only toggle — the admin/drain handler must not
    write it (or anything) into config.toml. (Startup's own discovery
    scan may legitimately touch discovery.provider_urls, so this checks
    the drain-specific claim rather than byte-for-byte file equality.)"""
    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    from netllm_core.models import save_config

    save_config(cfg, cfg_path)
    app = create_app(cfg, config_path=cfg_path)
    with TestClient(app) as client:
        before = cfg_path.read_text(encoding="utf-8")
        resp = client.post("/netllm/v1/admin/drain", json={"draining": True})
        assert resp.status_code == 200
        after = cfg_path.read_text(encoding="utf-8")
    assert before == after
    assert "draining" not in after


@pytest.mark.asyncio
async def test_handle_heartbeat_parses_max_concurrency_and_draining() -> None:
    cfg = NetllmConfig()
    service = AgentService(cfg)
    with patch.object(service, "refresh_local_backends", new=AsyncMock()):
        await service.handle_heartbeat(
            {
                "agent_id": "peer-x",
                "listen_url": "http://192.168.1.20:11400",
                "max_concurrency": 3,
                "draining": True,
            }
        )
    peer = service.swarm.peers["peer-x"]
    assert peer.max_concurrency == 3
    assert peer.draining is True


@pytest.mark.asyncio
async def test_handle_heartbeat_defaults_when_fields_absent() -> None:
    """Peers running an older netllm version omit these fields entirely —
    must default to unbounded/not-draining, not error."""
    cfg = NetllmConfig()
    service = AgentService(cfg)
    with patch.object(service, "refresh_local_backends", new=AsyncMock()):
        await service.handle_heartbeat(
            {"agent_id": "old-peer", "listen_url": "http://192.168.1.21:11400"}
        )
    peer = service.swarm.peers["old-peer"]
    assert peer.max_concurrency == 0
    assert peer.draining is False


def test_status_payload_is_what_heartbeat_broadcasts() -> None:
    """gossip_loop sends status_payload() verbatim as the heartbeat body —
    confirm max_concurrency/draining ride along without extra wiring."""
    cfg = NetllmConfig()
    cfg.agent.max_concurrency = 5
    service = AgentService(cfg)
    service.draining = True
    payload = service.status_payload()
    assert payload["max_concurrency"] == 5
    assert payload["draining"] is True


def test_draining_peer_excluded_end_to_end() -> None:
    """A registered peer with draining=True never appears as a routable
    backend, exercising the same registry path handle_heartbeat feeds."""
    cfg = NetllmConfig()
    service = AgentService(cfg)
    service.swarm.register_peer(
        PeerRecord(
            agent_id="draining-peer",
            listen_url="http://192.168.1.22:11400",
            draining=True,
        )
    )
    assert service.swarm.peer_agent_backends() == []
