"""Mesh-wide capacity helpers for gateway-led coordinator mode.

Pure functions — no FastAPI or discovery imports. ``RouterPool`` delegates
spillover / least_load branches here when ``mesh_coordinator_enabled`` and
``agent_role == "gateway"``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from netllm_core.models import Backend


@dataclass(frozen=True)
class PeerCapacityView:
    """Gossiped admission policy for one peer agent."""

    max_concurrency: int = 0
    spillover_max_local_in_flight: int = 0
    max_in_flight_per_backend: int = 0


def effective_backend_cap(
    backend: Backend,
    pool_cap: int,
    *,
    peer_view: PeerCapacityView | None = None,
    coordinator_enabled: bool = False,
) -> int:
    """Return the in-flight ceiling for a backend row (0 = unlimited)."""
    if coordinator_enabled and peer_view is not None and backend.id.startswith("peer:"):
        peer_cap = peer_effective_cap(peer_view, pool_cap)
        if peer_cap > 0:
            return peer_cap
    cap = backend.max_concurrency or pool_cap
    return max(0, cap)


def peer_effective_cap(peer: PeerCapacityView, receiver_pool_cap: int) -> int:
    """Merge gossiped peer caps into one ceiling for its ``peer:`` row."""
    if peer.max_concurrency > 0:
        return peer.max_concurrency
    if peer.max_in_flight_per_backend > 0:
        return peer.max_in_flight_per_backend
    return max(0, receiver_pool_cap)


def backend_has_headroom(backend: Backend, cap: int) -> bool:
    return cap <= 0 or backend.in_flight < cap


def filter_with_headroom(
    candidates: list[Backend],
    cap_for: Callable[[Backend], int],
) -> list[Backend]:
    under = [b for b in candidates if backend_has_headroom(b, cap_for(b))]
    return under if under else candidates


def select_mesh_least_loaded(
    candidates: list[Backend],
    *,
    cap_for: Callable[[Backend], int],
    round_robin_idx: int,
) -> tuple[Backend | None, int]:
    """Pick the least-loaded candidate with headroom; fair tie rotation."""
    if not candidates:
        return None, round_robin_idx
    with_headroom = filter_with_headroom(candidates, cap_for)
    lowest = min(b.in_flight for b in with_headroom)
    tied = [b for b in with_headroom if b.in_flight == lowest]
    if len(tied) == 1:
        return tied[0], round_robin_idx
    picked = tied[round_robin_idx % len(tied)]
    return picked, round_robin_idx + 1


def select_mesh_spillover(
    local_pool: list[Backend],
    remote_pool: list[Backend],
    *,
    local_threshold: int,
    cap_for: Callable[[Backend], int],
    prefer_remotes: bool = False,
) -> Backend | None:
    """Gateway coordinator spillover: respect remote gossiped caps."""
    if not local_pool:
        if not remote_pool:
            return None
        remotes = [b for b in remote_pool if backend_has_headroom(b, cap_for(b))]
        pool = remotes or remote_pool
        return min(pool, key=lambda b: b.in_flight)
    if prefer_remotes and remote_pool:
        remotes = [b for b in remote_pool if backend_has_headroom(b, cap_for(b))]
        pool = remotes or remote_pool
        return min(pool, key=lambda b: b.in_flight)
    best_local = min(local_pool, key=lambda b: b.in_flight)
    local_cap = cap_for(best_local)
    local_at_cap = not backend_has_headroom(best_local, local_cap)
    if best_local.in_flight < local_threshold and not local_at_cap:
        return best_local
    if not remote_pool:
        return best_local
    remotes_with_headroom = [
        b for b in remote_pool if backend_has_headroom(b, cap_for(b))
    ]
    if not remotes_with_headroom:
        return best_local
    best_remote = min(remotes_with_headroom, key=lambda b: b.in_flight)
    if best_remote.in_flight < best_local.in_flight:
        return best_remote
    return best_local


def local_agent_in_flight(backends: list[Backend]) -> int:
    return sum(max(0, b.in_flight) for b in backends if b.local)


def local_agent_saturated(backends: list[Backend], agent_max_concurrency: int) -> bool:
    if agent_max_concurrency > 0:
        return local_agent_in_flight(backends) >= agent_max_concurrency
    return False
