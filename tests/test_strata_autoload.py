"""Strata POST /load before routed inference."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from netllm_agent.service.backends import BackendsMixin
from netllm_core.models import Backend, BackendHealth, NetllmConfig


class _Svc(BackendsMixin):
    def __init__(self) -> None:
        self.config = NetllmConfig()


@pytest.mark.asyncio
async def test_ensure_backend_ready_skips_when_loaded() -> None:
    svc = _Svc()
    backend = Backend(
        id="s",
        base_url="http://127.0.0.1:8080/v1",
        provider="strata",
        health=BackendHealth(engine_loaded=True),
    )
    with patch(
        "netllm_agent.service.backends.ensure_strata_loaded",
        new_callable=AsyncMock,
    ) as load:
        await svc.ensure_backend_ready(backend)
    load.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_backend_ready_calls_load_when_unloaded() -> None:
    svc = _Svc()
    backend = Backend(
        id="s",
        base_url="http://127.0.0.1:8080/v1",
        provider="strata",
        health=BackendHealth(engine_loaded=False),
    )
    with patch(
        "netllm_agent.service.backends.ensure_strata_loaded",
        new_callable=AsyncMock,
    ) as load:
        await svc.ensure_backend_ready(backend)
    load.assert_awaited_once()
