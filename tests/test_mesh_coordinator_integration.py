"""Integration tests for opt-in gateway mesh coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from netllm_agent.service import AgentService
from netllm_core.models import Backend, BackendHealth, NetllmConfig


def _local(in_flight: int = 0) -> Backend:
    return Backend(
        id="omlx:1",
        base_url="http://127.0.0.1:8080/v1",
        provider="omlx",
        local=True,
        in_flight=in_flight,
        health=BackendHealth(models=["shared-model"], status="online"),
    )


def _peer_row(in_flight: int = 0, agent_id: str = "peer-b") -> Backend:
    return Backend(
        id=f"peer:{agent_id}",
        base_url="http://192.168.1.20:11400/v1",
        provider="custom",
        local=False,
        in_flight=in_flight,
        health=BackendHealth(models=["shared-model"], status="online"),
    )


@pytest.mark.asyncio
async def test_coordinator_off_and_on_differ_under_saturation() -> None:
    cfg_off = NetllmConfig()
    cfg_off.routing.mesh_coordinator = "off"
    cfg_off.routing.default_strategy = "local_spillover"
    cfg_off.routing.spillover_max_local_in_flight = 2
    service_off = AgentService(cfg_off)
    service_off.config.agent.role = "gateway"
    service_off.pool.set_backends([_local(in_flight=3), _peer_row(in_flight=1)])

    cfg_on = NetllmConfig()
    cfg_on.routing.mesh_coordinator = "gateway"
    cfg_on.routing.default_strategy = "local_spillover"
    cfg_on.routing.spillover_max_local_in_flight = 2
    cfg_on.agent.role = "gateway"
    service_on = AgentService(cfg_on)
    service_on.pool.set_backends([_local(in_flight=3), _peer_row(in_flight=1)])
    from netllm_core.mesh_capacity import PeerCapacityView

    service_on.pool.set_peer_capacity(
        {"peer-b": PeerCapacityView(max_concurrency=1)},
    )

    off_pick = service_off.pool.select_backend("shared-model", "local_spillover")
    on_pick = service_on.pool.select_backend("shared-model", "local_spillover")
    assert off_pick is not None and off_pick.id.startswith("peer:")
    assert on_pick is not None and on_pick.local


@pytest.mark.asyncio
async def test_follow_gateway_capacity_adopts_runtime_pool_knobs() -> None:
    cfg = NetllmConfig()
    cfg.agent.role = "peer"
    cfg.routing.follow_gateway = True
    cfg.routing.follow_gateway_capacity = True
    service = AgentService(cfg)
    with patch.object(service, "refresh_local_backends", new=AsyncMock()):
        await service.handle_heartbeat(
            {
                "agent_id": "gw-1",
                "listen_url": "http://192.168.1.1:11400",
                "role": "gateway",
                "routing_capacity": {
                    "spillover_max_local_in_flight": 5,
                    "max_in_flight_per_backend": 3,
                },
            }
        )
    assert service.pool.spillover_max_local_in_flight == 5
    assert service.pool.max_in_flight_per_backend == 3


@pytest.mark.asyncio
async def test_handle_heartbeat_stores_routing_capacity_on_peer_record() -> None:
    cfg = NetllmConfig()
    service = AgentService(cfg)
    with patch.object(service, "refresh_local_backends", new=AsyncMock()):
        await service.handle_heartbeat(
            {
                "agent_id": "peer-x",
                "listen_url": "http://192.168.1.21:11400",
                "routing_capacity": {
                    "spillover_max_local_in_flight": 4,
                    "max_in_flight_per_backend": 6,
                },
            }
        )
    peer = service.swarm.peers["peer-x"]
    assert peer.peer_spillover_max_local_in_flight == 4
    assert peer.peer_max_in_flight_per_backend == 6


def test_gateway_coordinator_excludes_locals_when_agent_saturated() -> None:
    from netllm_core.mesh_capacity import PeerCapacityView

    cfg = NetllmConfig()
    cfg.routing.mesh_coordinator = "gateway"
    cfg.agent.role = "gateway"
    cfg.agent.max_concurrency = 3
    service = AgentService(cfg)
    service.pool.set_backends([_local(2), _local(1), _peer_row(0)])
    service.pool.set_peer_capacity(
        {"peer-b": PeerCapacityView(max_concurrency=8)},
    )
    picked = service.pool.select_backend("shared-model", "least_load")
    assert picked is not None
    assert picked.id.startswith("peer:")


@pytest.mark.asyncio
async def test_peer_self_admission_rejects_saturated_local_only_hop() -> None:
    from netllm_agent.service import AgentCapacityExceeded

    cfg = NetllmConfig()
    cfg.routing.mesh_coordinator = "gateway"
    cfg.agent.max_concurrency = 1
    service = AgentService(cfg)
    service.pool.set_backends([_local(in_flight=1)])

    with patch.object(service, "refresh_local_backends", new=AsyncMock()):
        with pytest.raises(AgentCapacityExceeded):
            await service.proxy_chat_completion(
                {
                    "model": "shared-model",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={"x-netllm-local-only": "1"},
            )


def test_peer_self_admission_off_skips_capacity_gate() -> None:
    from netllm_agent.service.engine import _assert_mesh_self_admission
    from netllm_agent.service.surfaces.chat import ChatAdapter
    from netllm_agent.taxonomy import Surface

    cfg = NetllmConfig()
    cfg.routing.mesh_coordinator = "off"
    cfg.agent.max_concurrency = 1
    service = AgentService(cfg)
    service.pool.set_backends([_local(in_flight=1)])
    plan = service.build_request_plan(
        {"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        {"x-netllm-local-only": "1"},
        surface=Surface.CHAT,
    )
    adapter = ChatAdapter(service)
    _assert_mesh_self_admission(adapter, plan, _local(in_flight=1))
