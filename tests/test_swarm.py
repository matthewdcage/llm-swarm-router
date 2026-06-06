"""Tests for swarm registry."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.swarm import PeerRecord, SwarmRegistry


@pytest.mark.asyncio
async def test_register_and_list_peers() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-a",
            listen_url="http://192.168.1.10:11400",
            role="peer",
            hostname="mac-a",
        )
    )
    urls = registry.all_peer_urls()
    assert len(urls) == 1
    assert urls[0]["agent_id"] == "peer-a"


@pytest.mark.asyncio
async def test_fetch_peer_from_status() -> None:
    registry = SwarmRegistry(NetllmConfig())
    mock_response = {
        "agent_id": "peer-b",
        "listen_url": "http://192.168.1.11:11400",
        "role": "gateway",
        "hostname": "mac-b",
        "backends": [],
    }

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return mock_response

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(
            self, url: str, headers: dict[str, str] | None = None
        ) -> FakeResponse:
            return FakeResponse()

    with patch("netllm_discovery.swarm.httpx.AsyncClient", FakeClient):
        record = await registry.fetch_peer("http://192.168.1.11:11400")
    assert record is not None
    assert record.agent_id == "peer-b"
    assert record.role == "gateway"
