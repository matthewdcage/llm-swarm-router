"""Tests for LAN discovery helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.lan import (
    agent_url_from_listen,
    default_subnet_cidrs,
    discover_lan_agents,
    models_from_status,
)


def test_agent_url_from_listen_uses_lan_for_wildcard() -> None:
    url = agent_url_from_listen("0.0.0.0:11400", lan_ip="192.168.1.10")
    assert url == "http://192.168.1.10:11400"


def test_models_from_status_flattens_backends() -> None:
    status = {
        "agent_id": "abc",
        "hostname": "macbook",
        "listen_url": "http://192.168.1.10:11400",
        "backends": [
            {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "local": True,
                "health": {"models": ["llama3", "mistral"]},
            }
        ],
    }
    rows = models_from_status(status)
    assert len(rows) == 2
    assert rows[0]["model"] == "llama3"
    assert rows[0]["host"] == "macbook"


@pytest.mark.asyncio
async def test_discover_lan_agents_static_peer() -> None:
    cfg = NetllmConfig()
    cfg.swarm.peers = ["http://192.168.1.99:11400"]
    cfg.swarm.mdns = False

    mock_status = {
        "agent_id": "remote",
        "hostname": "remote-mac",
        "listen_url": "http://192.168.1.99:11400",
        "role": "peer",
        "backends": [],
    }

    with patch(
        "netllm_discovery.lan.fetch_agent_status",
        new_callable=AsyncMock,
        return_value=mock_status,
    ):
        peers = await discover_lan_agents(cfg, use_mdns=False, use_subnet=False)

    assert len(peers) == 1
    assert peers[0]["agent_id"] == "remote"


def test_default_subnet_cidrs_from_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "netllm_discovery.lan.local_lan_ip",
        lambda: "10.0.0.5",
    )
    cidrs = default_subnet_cidrs()
    assert cidrs == ["10.0.0.0/24"]


@pytest.mark.asyncio
async def test_discover_lan_agents_filters_by_agent_id_not_listen_url() -> None:
    cfg = NetllmConfig()
    cfg.agent.agent_id = "local-agent"
    cfg.swarm.peers = ["http://127.0.0.1:11400"]
    cfg.swarm.mdns = False

    remote_status = {
        "agent_id": "remote-agent",
        "hostname": "remote-mac",
        "listen_url": "http://127.0.0.1:11400",
        "role": "peer",
        "backends": [],
    }

    with patch(
        "netllm_discovery.lan.fetch_agent_status",
        new_callable=AsyncMock,
        return_value=remote_status,
    ):
        peers = await discover_lan_agents(cfg, use_mdns=False, use_subnet=False)

    assert len(peers) == 1
    assert peers[0]["agent_id"] == "remote-agent"


@pytest.mark.asyncio
async def test_fetch_agent_status_uses_probe_url_as_listen_url() -> None:
    from netllm_discovery.lan import fetch_agent_status

    class FakeResp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "agent_id": "remote",
                "hostname": "mini",
                "listen_url": "http://127.0.0.1:11400",
                "backends": [],
            }

    client = AsyncMock()
    client.get = AsyncMock(return_value=FakeResp())
    status = await fetch_agent_status("http://10.0.0.32:11400", client)
    assert status is not None
    assert status["listen_url"] == "http://10.0.0.32:11400"
