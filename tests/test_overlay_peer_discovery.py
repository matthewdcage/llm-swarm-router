"""Invariants for VPN-aware overlay peer discovery (gossip enrollment + routing).

Implementation lives in netllm_discovery.lan, netllm_discovery.swarm, and
netllm-agent swarm_tasks. These tests document contracts before/after changes.
"""

from __future__ import annotations

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.lan import peer_candidate_listen_urls
from netllm_discovery.swarm import DISCOVERY_SOURCES, PeerRecord, SwarmRegistry


def test_discovery_sources_include_gossip() -> None:
    assert "gossip" in DISCOVERY_SOURCES


def test_peer_candidate_listen_urls_prefers_lan_over_vpn() -> None:
    rows = peer_candidate_listen_urls(
        "http://10.0.0.32:11400",
        also_reachable_at=["http://100.72.59.239:11400"],
        reachable_at=[
            {"url": "http://10.0.0.32:11400", "kind": "lan", "interface": "en0"},
            {
                "url": "http://100.72.59.239:11400",
                "kind": "vpn",
                "interface": "utun100",
            },
        ],
    )
    assert [kind for _, kind in rows] == ["lan", "vpn"]
    assert rows[0][0] == "http://10.0.0.32:11400"


def test_peer_candidate_listen_urls_skips_loopback_and_container() -> None:
    rows = peer_candidate_listen_urls(
        "http://10.0.0.32:11400",
        reachable_at=[
            {"url": "http://127.0.0.1:11400", "kind": "loopback", "interface": "lo"},
            {
                "url": "http://172.17.0.1:11400",
                "kind": "container",
                "interface": "docker0",
            },
            {"url": "http://10.0.0.32:11400", "kind": "lan", "interface": "eth0"},
        ],
    )
    assert rows == [("http://10.0.0.32:11400", "lan")]


def test_overlay_discovery_defaults_auto() -> None:
    cfg = NetllmConfig()
    assert cfg.swarm.overlay_discovery == "auto"


def test_register_peer_notes_gossip_urls_when_overlay_auto() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-a",
            listen_url="http://10.0.0.32:11400",
            reachable_at=[
                {
                    "url": "http://100.72.59.239:11400",
                    "kind": "vpn",
                    "interface": "wt0",
                },
            ],
        )
    )
    assert "http://100.72.59.239:11400" in registry.known_peer_urls
    assert registry._url_discovery.get("http://100.72.59.239:11400") == "gossip"


def test_register_peer_skips_gossip_urls_when_overlay_off() -> None:
    cfg = NetllmConfig()
    cfg.swarm.overlay_discovery = "off"
    registry = SwarmRegistry(cfg)
    registry.register_peer(
        PeerRecord(
            agent_id="peer-a",
            listen_url="http://10.0.0.32:11400",
            reachable_at=[
                {
                    "url": "http://100.72.59.239:11400",
                    "kind": "vpn",
                    "interface": "wt0",
                },
            ],
        )
    )
    assert "http://100.72.59.239:11400" not in registry.known_peer_urls


@pytest.mark.parametrize(
    ("iface", "ip", "expected"),
    [
        ("wt0", "100.72.128.178", "vpn"),
        ("utun100", "100.72.59.239", "vpn"),
    ],
)
def test_netbird_wt_classified_as_vpn(iface: str, ip: str, expected: str) -> None:
    from netllm_discovery.lan import classify_interface_address

    assert classify_interface_address(iface, ip) == expected
