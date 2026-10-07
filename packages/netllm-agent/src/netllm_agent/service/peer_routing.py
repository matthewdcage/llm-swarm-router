"""Per-request peer URL failover (LAN → VPN) without flip-flopping globals."""

from __future__ import annotations

from netllm_core.health import agent_root_from_base_url
from netllm_core.models import Backend


def next_peer_v1_base_url(backend: Backend, failed_v1_base: str) -> str | None:
    """Next agent /v1 base URL after a recoverable failure on ``failed_v1_base``."""
    if not backend.id.startswith("peer:"):
        return None
    candidates = backend.peer_listen_candidates
    if not candidates:
        return None
    failed_root = agent_root_from_base_url(failed_v1_base).rstrip("/")
    past_failed = False
    for root in candidates:
        r = root.rstrip("/")
        if r == failed_root:
            past_failed = True
            continue
        if past_failed:
            return f"{r}/v1"
    return None


def backend_for_v1_attempt(backend: Backend, v1_base: str) -> Backend:
    if v1_base == backend.base_url:
        return backend
    return backend.model_copy(update={"base_url": v1_base})
