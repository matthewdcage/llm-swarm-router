"""Tests for LAN discovery helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.lan import (
    agent_url_from_listen,
    default_subnet_cidrs,
    discover_lan_agents,
    filter_own_peer_urls,
    is_lan_reachable_agent_url,
    is_loopback_url,
    models_from_status,
    own_agent_urls,
)


def test_is_loopback_url_detects_local_hosts() -> None:
    assert is_loopback_url("http://127.0.0.1:8080/v1") is True
    assert is_loopback_url("http://localhost:11400") is True
    assert is_loopback_url("http://192.168.1.11:11400") is False


def test_own_agent_urls_includes_lan_and_loopback() -> None:
    with patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"):
        urls = own_agent_urls("0.0.0.0:11400")
    assert "http://10.0.0.32:11400" in urls
    assert "http://127.0.0.1:11400" in urls


def test_filter_own_peer_urls() -> None:
    with patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"):
        kept, rejected = filter_own_peer_urls(
            [
                "http://10.0.0.32:11400",
                "http://10.0.0.5:11400",
            ],
            "0.0.0.0:11400",
        )
    assert kept == ["http://10.0.0.5:11400"]
    assert rejected == ["http://10.0.0.32:11400"]


def test_is_lan_reachable_agent_url() -> None:
    assert is_lan_reachable_agent_url("http://10.0.0.32:11400") is True
    assert is_lan_reachable_agent_url("http://127.0.0.1:11400") is False
    assert is_lan_reachable_agent_url("") is False


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
async def test_discover_lan_agents_keeps_unreachable_rows_without_fetch() -> None:
    """Loopback-bound mDNS peers are surfaced (not silently dropped) and
    their loopback URL is never fetched — that would hit our own agent."""
    cfg = NetllmConfig()
    cfg.agent.agent_id = "me"

    props = {
        "agent_id": "loopback-peer",
        "role": "peer",
        "listen_url": "http://127.0.0.1:11400",
        "reachable": "false",
        "source": "mdns",
    }
    with (
        patch(
            "netllm_discovery.lan.browse_mdns_peers",
            return_value=[props],
        ),
        patch(
            "netllm_discovery.lan.fetch_agent_status",
            new_callable=AsyncMock,
        ) as mock_fetch,
    ):
        peers = await discover_lan_agents(cfg, use_mdns=True, use_subnet=False)

    mock_fetch.assert_not_awaited()
    assert len(peers) == 1
    assert peers[0]["unreachable"] is True
    assert peers[0]["agent_id"] == "loopback-peer"


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


def test_dedupe_agents_by_id_collapses_multi_homed_host() -> None:
    """A dual-interface host answers on several IPs with one agent_id —
    the scan must show one row, preferring its reported listen URL."""
    from netllm_discovery.lan import dedupe_agents_by_id

    rows = [
        {
            "agent_id": "mini",
            "hostname": "mini.local",
            "listen_url": "http://10.0.0.10:11400",
            "reported_listen_url": "http://10.0.0.32:11400",
        },
        {
            "agent_id": "mini",
            "hostname": "mini.local",
            "listen_url": "http://10.0.0.32:11400",
            "reported_listen_url": "http://10.0.0.32:11400",
        },
        {
            "agent_id": "laptop",
            "hostname": "laptop.local",
            "listen_url": "http://10.0.0.9:11400",
            "reported_listen_url": "http://10.0.0.9:11400",
        },
    ]
    deduped = dedupe_agents_by_id(rows)
    assert len(deduped) == 2
    by_id = {r["agent_id"]: r for r in deduped}
    assert by_id["mini"]["listen_url"] == "http://10.0.0.32:11400"
    assert by_id["mini"]["also_reachable_at"] == ["http://10.0.0.10:11400"]
    assert "also_reachable_at" not in by_id["laptop"]


def test_fetch_agent_status_keeps_reported_listen_url() -> None:
    import asyncio

    from netllm_discovery.lan import fetch_agent_status

    class FakeResp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "agent_id": "remote",
                "listen_url": "http://10.0.0.32:11400",
                "backends": [],
            }

    client = AsyncMock()
    client.get = AsyncMock(return_value=FakeResp())
    status = asyncio.run(fetch_agent_status("http://10.0.0.10:11400", client))
    assert status is not None
    assert status["listen_url"] == "http://10.0.0.10:11400"
    assert status["reported_listen_url"] == "http://10.0.0.32:11400"
