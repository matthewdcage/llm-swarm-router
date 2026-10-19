"""Peer candidate-path probing must never block the event loop.

Regression: a multi-homed Windows peer advertised NordLynx and VMware
host-only addresses as ``lan`` candidates. ``refresh_peer_routing_if_stale``
probed each one with a *synchronous* 5 s GET inside ``refresh_local_backends``
(every proxied request and heartbeat), so three black-holed candidates froze
the gateway's event loop ~15 s out of every 15 s refresh interval: chat
requests timed out, /health took seconds and /netllm/v1/status never answered.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any
from unittest.mock import patch

import httpx
import netllm_discovery.swarm as swarm_mod
from netllm_core.models import NetllmConfig
from netllm_discovery.swarm import PeerRecord, SwarmRegistry

DEAD = [
    "http://10.5.0.2:11400",
    "http://192.168.45.1:11400",
    "http://192.168.56.1:11400",
]
LIVE_LAN = "http://10.0.0.31:11400"
LIVE_VPN = "http://100.72.166.250:11400"
PROBE_DELAY_S = 0.3


def _multihomed_registry() -> SwarmRegistry:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="bdda899d",
            listen_url=LIVE_LAN,
            reachable_at=[
                {"url": LIVE_LAN, "kind": "lan"},
                *({"url": u, "kind": "lan"} for u in DEAD),
                {"url": LIVE_VPN, "kind": "vpn"},
            ],
        )
    )
    return registry


async def _fake_probe(_client: Any, url: str, _headers: Any) -> dict[str, Any]:
    if url in DEAD:
        # Stand-in for a connect timeout: yields to the loop like real I/O.
        await asyncio.sleep(PROBE_DELAY_S)
        return {"status": "timeout"}
    return {"status": "online", "http_status": 200}


async def test_schedule_returns_immediately_and_loop_stays_responsive() -> None:
    registry = _multihomed_registry()
    with patch.object(swarm_mod, "_probe_agent_health", _fake_probe):
        t0 = time.monotonic()
        registry.schedule_peer_routing_refresh()
        assert time.monotonic() - t0 < 0.05

        # The loop keeps ticking while the dead candidates are in flight.
        ticks = 0
        task = registry._peer_route_task
        assert task is not None
        while not task.done():
            await asyncio.sleep(0.01)
            ticks += 1
        assert ticks >= 10
        await task

    peer = registry.peers["bdda899d"]
    assert peer.active_listen_url == LIVE_LAN
    statuses = {row["url"]: row["status"] for row in peer.address_health}
    assert statuses[LIVE_VPN] == "online"
    assert all(statuses[u] == "timeout" for u in DEAD)


async def test_dead_candidates_cost_one_timeout_not_the_sum() -> None:
    registry = _multihomed_registry()
    with patch.object(swarm_mod, "_probe_agent_health", _fake_probe):
        t0 = time.monotonic()
        await registry.refresh_peer_routing()
        elapsed = time.monotonic() - t0
    assert elapsed < PROBE_DELAY_S * 2, elapsed


async def test_schedule_does_not_stack_probes_within_interval() -> None:
    registry = _multihomed_registry()
    with patch.object(swarm_mod, "_probe_agent_health", _fake_probe):
        registry.schedule_peer_routing_refresh()
        first = registry._peer_route_task
        for _ in range(20):
            registry.schedule_peer_routing_refresh()
        assert registry._peer_route_task is first
        await first


async def test_real_probe_times_out_on_unresponsive_candidate() -> None:
    """A candidate that accepts TCP but never answers is bounded by the timeout."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    try:
        with patch.object(swarm_mod, "PEER_ROUTE_PROBE_TIMEOUT_S", 0.2):
            t0 = time.monotonic()
            timeout = swarm_mod.PEER_ROUTE_PROBE_TIMEOUT_S
            async with httpx.AsyncClient(timeout=timeout) as c:
                status = await swarm_mod._probe_agent_health(c, url, {})
            assert time.monotonic() - t0 < 2.0
        assert status["status"] == "timeout"
    finally:
        sock.close()
