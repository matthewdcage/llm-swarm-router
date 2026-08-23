"""Unit tests for gateway mesh capacity selection helpers."""

from __future__ import annotations

from netllm_core.mesh_capacity import (
    PeerCapacityView,
    backend_has_headroom,
    effective_backend_cap,
    local_agent_saturated,
    peer_effective_cap,
    select_mesh_least_loaded,
    select_mesh_spillover,
)
from netllm_core.models import Backend, BackendHealth
from netllm_core.pool import RouterPool


def _local(in_flight: int = 0) -> Backend:
    return Backend(
        id="local:1",
        base_url="http://127.0.0.1:8080/v1",
        provider="vllm",
        local=True,
        in_flight=in_flight,
        health=BackendHealth(models=["m"], status="online"),
    )


def _peer(
    agent_id: str = "p1",
    in_flight: int = 0,
    max_concurrency: int = 0,
) -> Backend:
    return Backend(
        id=f"peer:{agent_id}",
        base_url="http://192.168.1.10:11400/v1",
        provider="custom",
        local=False,
        in_flight=in_flight,
        max_concurrency=max_concurrency,
        health=BackendHealth(models=["m"], status="online"),
    )


def test_peer_effective_cap_prefers_agent_max_concurrency_over_gossip_backend_cap() -> (
    None
):
    peer = PeerCapacityView(max_concurrency=3, max_in_flight_per_backend=8)
    assert peer_effective_cap(peer, receiver_pool_cap=10) == 3


def test_peer_effective_cap_falls_back_when_gossip_absent() -> None:
    peer = PeerCapacityView()
    assert peer_effective_cap(peer, receiver_pool_cap=0) == 0
    assert peer_effective_cap(peer, receiver_pool_cap=5) == 5


def test_effective_backend_cap_uses_peer_view_when_coordinator_enabled() -> None:
    backend = _peer(max_concurrency=0)
    view = PeerCapacityView(max_concurrency=2)
    assert (
        effective_backend_cap(backend, 10, peer_view=view, coordinator_enabled=True)
        == 2
    )


def test_mesh_spillover_spills_when_local_at_threshold_and_remote_has_headroom() -> (
    None
):
    local = _local(in_flight=2)
    remote = _peer(in_flight=0)
    view = PeerCapacityView(max_concurrency=4)

    def cap_for(b: Backend) -> int:
        if b.id.startswith("peer:"):
            return peer_effective_cap(view, 0)
        return 0

    picked = select_mesh_spillover(
        [local],
        [remote],
        local_threshold=2,
        cap_for=cap_for,
    )
    assert picked is remote


def test_mesh_spillover_stays_local_when_remote_at_cap_even_if_local_saturated() -> (
    None
):
    local = _local(in_flight=4)
    remote = _peer(in_flight=2)
    view = PeerCapacityView(max_concurrency=2)

    def cap_for(b: Backend) -> int:
        if b.id.startswith("peer:"):
            return peer_effective_cap(view, 0)
        return 0

    picked = select_mesh_spillover(
        [local],
        [remote],
        local_threshold=2,
        cap_for=cap_for,
    )
    assert picked is local


def test_mesh_least_load_skips_backends_without_headroom() -> None:
    busy = _peer("busy", in_flight=3, max_concurrency=3)
    idle = _peer("idle", in_flight=0, max_concurrency=3)

    def cap_for(b: Backend) -> int:
        return b.max_concurrency or 0

    picked, _ = select_mesh_least_loaded(
        [busy, idle], cap_for=cap_for, round_robin_idx=0
    )
    assert picked is idle


def test_mesh_least_load_fair_tie_rotation_among_peers() -> None:
    a = _peer("a", in_flight=0)
    b = _peer("b", in_flight=0)

    def cap_for(_backend: Backend) -> int:
        return 0

    first, idx = select_mesh_least_loaded([a, b], cap_for=cap_for, round_robin_idx=0)
    second, _ = select_mesh_least_loaded([a, b], cap_for=cap_for, round_robin_idx=idx)
    assert first is not second


def test_coordinator_off_delegates_to_legacy_select_local_spillover() -> None:
    pool = RouterPool(spillover_max_local_in_flight=2, mesh_coordinator="off")
    local = _local(in_flight=0)
    remote = _peer(in_flight=5)
    picked = pool.select_backend(
        "m",
        "local_spillover",
        extra_candidates=[local, remote],
    )
    assert picked is local


def test_local_agent_saturated_respects_agent_max_concurrency() -> None:
    backends = [_local(2), _local(1)]
    assert local_agent_saturated(backends, 3)
    assert not local_agent_saturated(backends, 4)


def test_backend_has_headroom_at_cap_boundary() -> None:
    b = _local(in_flight=2)
    assert backend_has_headroom(b, 3)
    assert not backend_has_headroom(b, 2)
