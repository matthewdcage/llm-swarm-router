"""Tests for LAN discovery helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.lan import (
    agent_url_from_listen,
    classified_agent_endpoints,
    classify_interface_address,
    default_subnet_cidrs,
    discover_lan_agents,
    filter_own_peer_urls,
    is_lan_reachable_agent_url,
    is_loopback_url,
    models_from_status,
    own_agent_urls,
)

# One host as `ip -o -4 addr` actually reports it: a Wi-Fi LAN address, a
# Tailscale tunnel, Docker's default bridge plus three compose bridges, a
# libvirt bridge, an interface that never got a DHCP lease, and loopback.
# Deliberately in the order the kernel lists them, which is not useful order.
MULTI_HOMED_INTERFACES = [
    ("lo", "127.0.0.1"),
    ("docker0", "172.17.0.1"),
    ("br-1a2b3c4d5e6f", "172.18.0.1"),
    ("br-9f8e7d6c5b4a", "172.19.0.1"),
    ("br-0011223344ff", "172.20.0.1"),
    ("virbr0", "192.168.122.1"),
    ("tailscale0", "100.101.102.103"),
    ("enp0s31f6", "169.254.11.7"),
    ("wlp3s0", "10.0.0.29"),
]


def test_is_loopback_url_detects_local_hosts() -> None:
    assert is_loopback_url("http://127.0.0.1:8080/v1") is True
    assert is_loopback_url("http://localhost:11400") is True
    assert is_loopback_url("http://192.168.1.11:11400") is False


def test_own_agent_urls_includes_lan_and_loopback() -> None:
    with patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"):
        urls = own_agent_urls("0.0.0.0:11400")
    assert "http://10.0.0.32:11400" in urls
    assert "http://127.0.0.1:11400" in urls


def test_rfc1918_membership_is_not_the_classifier() -> None:
    """The address range cannot answer this question and never could.

    10.0.0.29 and 172.17.0.1 are both RFC1918; one is the LAN, the other is a
    Docker bridge gateway. Only the interface distinguishes them — which is
    why the *agent* classifies and the clients do not.
    """
    assert classify_interface_address("wlp3s0", "10.0.0.29") == "lan"
    assert classify_interface_address("docker0", "172.17.0.1") == "container"
    # Same address, ordinary NIC: a home router handing out 172.16/12 is
    # unusual but legal, and this must not be filed as container noise.
    assert classify_interface_address("eth0", "172.17.0.1") == "lan"
    # Same interface, LAN address: a bridge is a bridge whatever it hands out.
    assert classify_interface_address("docker0", "10.0.0.29") == "container"
    # `br-<hash>` is Docker compose. Plain `br0` is a Linux host bridging its
    # own NIC (libvirt, Proxmox) and carries the machine's real LAN address.
    assert classify_interface_address("br-1a2b3c4d5e6f", "172.18.0.1") == "container"
    assert classify_interface_address("br0", "10.0.0.29") == "lan"
    # Address facts win over interface names where the RFC settles it.
    assert classify_interface_address("eth0", "169.254.11.7") == "link_local"
    assert classify_interface_address("eth0", "127.0.0.1") == "loopback"
    # A tunnel reaches peers that are on the tunnel — useful, below the LAN.
    assert classify_interface_address("tailscale0", "100.101.102.103") == "vpn"
    # An interface nobody has heard of is LAN: showing a real address is a far
    # cheaper mistake than hiding one.
    assert classify_interface_address("some-nic-from-2030", "10.0.0.29") == "lan"


def test_classified_agent_endpoints_orders_by_who_can_actually_dial_it() -> None:
    """The reported bug: five Docker bridge gateways above the one address a
    LAN peer can use. Nothing is dropped — the order carries the meaning."""
    endpoints = classified_agent_endpoints(
        11400,
        interfaces=MULTI_HOMED_INTERFACES,
        primary_url="http://10.0.0.29:11400",
    )
    assert [(e["url"], e["kind"]) for e in endpoints] == [
        # Advertised first, whatever kind it is: it is what peers were told.
        ("http://10.0.0.29:11400", "lan"),
        ("http://100.101.102.103:11400", "vpn"),
        ("http://172.17.0.1:11400", "container"),
        ("http://172.18.0.1:11400", "container"),
        ("http://172.19.0.1:11400", "container"),
        ("http://172.20.0.1:11400", "container"),
        ("http://192.168.122.1:11400", "container"),
        ("http://169.254.11.7:11400", "link_local"),
        ("http://127.0.0.1:11400", "loopback"),
    ]
    # The interface is carried so a client can say *which* container network
    # — "the host is reachable from your containers at 172.17.0.1 (docker0)".
    by_url = {e["url"]: e for e in endpoints}
    assert by_url["http://172.17.0.1:11400"]["interface"] == "docker0"


def test_classified_agent_endpoints_is_stable_and_dedupes_aliases() -> None:
    """Polled every 2s by the dashboard: a set-derived order would reshuffle
    the cell under the cursor. One address on two interfaces keeps the most
    useful reading of itself."""
    shuffled = list(reversed(MULTI_HOMED_INTERFACES))
    assert classified_agent_endpoints(
        11400, interfaces=shuffled, primary_url="http://10.0.0.29:11400"
    ) == classified_agent_endpoints(
        11400, interfaces=MULTI_HOMED_INTERFACES, primary_url="http://10.0.0.29:11400"
    )

    aliased = classified_agent_endpoints(
        11400, interfaces=[("docker0", "10.0.0.29"), ("wlp3s0", "10.0.0.29")]
    )
    assert [(e["url"], e["kind"]) for e in aliased] == [
        ("http://10.0.0.29:11400", "lan")
    ]


def test_classified_agent_endpoints_keeps_an_unenumerable_primary() -> None:
    """Interface enumeration needs psutil. Without it the caller still has the
    advertised address, and must not be handed an empty list instead."""
    endpoints = classified_agent_endpoints(
        11400, interfaces=[], primary_url="http://10.0.0.29:11400"
    )
    assert endpoints == [
        {"url": "http://10.0.0.29:11400", "kind": "lan", "interface": ""}
    ]


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
