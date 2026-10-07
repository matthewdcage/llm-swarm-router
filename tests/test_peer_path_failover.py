"""Per-request peer URL failover (LAN down, VPN up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from netllm_agent.service.peer_routing import next_peer_v1_base_url
from netllm_core.models import Backend, BackendHealth


def test_next_peer_v1_base_url_skips_failed_lan() -> None:
    backend = Backend(
        id="peer:abc",
        base_url="http://10.0.0.32:11400/v1",
        peer_listen_candidates=[
            "http://10.0.0.32:11400",
            "http://100.72.59.239:11400",
        ],
    )
    nxt = next_peer_v1_base_url(backend, "http://10.0.0.32:11400/v1")
    assert nxt == "http://100.72.59.239:11400/v1"


@pytest.mark.asyncio
async def test_run_attempt_retries_peer_vpn_path() -> None:
    from netllm_agent.request_plan import RequestPlan
    from netllm_agent.service.engine import _run_attempt
    from netllm_agent.service.surfaces.base import SurfaceAdapter

    backend = Backend(
        id="peer:abc",
        base_url="http://10.0.0.32:11400/v1",
        local=False,
        peer_listen_candidates=[
            "http://10.0.0.32:11400",
            "http://100.72.59.239:11400",
        ],
        health=BackendHealth(models=["m1"]),
    )
    plan = MagicMock(spec=RequestPlan)
    plan.model = "m1"
    plan.shard = None
    plan.routing = MagicMock()
    adapter = MagicMock(spec=SurfaceAdapter)
    adapter.log_label = "chat"
    adapter.classify_error = lambda exc: isinstance(exc, httpx.ConnectError)
    adapter.build_invocation = MagicMock(return_value=MagicMock())
    adapter.invoke = AsyncMock(
        side_effect=[
            httpx.ConnectError("lan down"),
            {"ok": True},
        ]
    )
    adapter.restore_model = lambda p, inv, r: r
    adapter.extract_usage = lambda r: (0, 0)
    service = MagicMock()
    service.pool.acquire = MagicMock()
    service.pool.release = MagicMock()
    service.pool.backends = [backend]
    service._update_health_metrics = MagicMock()
    adapter.service = service
    recorder = MagicMock()

    result = await _run_attempt(adapter, plan, backend, recorder, 1)
    assert result == {"ok": True}
    assert adapter.invoke.await_count == 2
    second_url = adapter.build_invocation.call_args_list[1][0][1].base_url
    assert second_url == "http://100.72.59.239:11400/v1"
