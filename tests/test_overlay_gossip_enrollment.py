"""Gossip-driven overlay URL enrollment (swarm.overlay_discovery)."""

from __future__ import annotations

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.swarm import PeerRecord, SwarmRegistry


@pytest.mark.asyncio
async def test_heartbeat_vpn_listen_url_registers_without_static_peers() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.swarm.peers = []
    cfg.swarm.overlay_discovery = "auto"
    service = AgentService(cfg)
    await service.handle_heartbeat(
        {
            "agent_id": "vpn-only-peer",
            "listen_url": "http://100.72.59.239:11400",
            "role": "peer",
            "hostname": "mac-remote",
            "backends": [],
            "reachable_at": [
                {
                    "url": "http://100.72.59.239:11400",
                    "kind": "vpn",
                    "interface": "utun100",
                },
            ],
        }
    )
    assert "vpn-only-peer" in service.swarm.peers
    assert "http://100.72.59.239:11400" in service.swarm.known_peer_urls


def test_gossip_url_enrolled_from_lan_peer_heartbeat_body() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-b",
            listen_url="http://10.0.0.32:11400",
            reachable_at=[
                {"url": "http://10.0.0.32:11400", "kind": "lan", "interface": "en0"},
                {
                    "url": "http://100.72.59.239:11400",
                    "kind": "vpn",
                    "interface": "wt0",
                },
            ],
        )
    )
    assert registry._url_discovery.get("http://100.72.59.239:11400") == "gossip"
