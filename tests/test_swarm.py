"""Tests for swarm registry."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from netllm_core.models import Backend, BackendHealth, NetllmConfig
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


def test_peer_agent_backends_uses_agent_url_not_loopback() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-remote",
            listen_url="http://192.168.1.11:11400",
            backends=[
                Backend(
                    id="b1",
                    base_url="http://127.0.0.1:8080/v1",
                    provider="omlx",
                    local=True,
                    health=BackendHealth(models=["shared-model"]),
                ).model_dump(mode="json")
            ],
        )
    )
    backends = registry.peer_agent_backends()
    assert len(backends) == 1
    assert backends[0].base_url == "http://192.168.1.11:11400/v1"
    assert backends[0].local is False
    assert backends[0].agent_id == "peer-remote"
    assert "shared-model" in backends[0].health.models
    assert all("127.0.0.1" not in b.base_url for b in backends)


def test_peer_agent_backends_skips_loopback_listen_url() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-loopback",
            listen_url="http://127.0.0.1:11400",
            backends=[],
        )
    )
    assert registry.peer_agent_backends() == []


def test_peer_agent_backends_unions_models_from_peer_backends() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-multi",
            listen_url="http://10.0.0.32:11400",
            backends=[
                Backend(
                    id="a",
                    base_url="http://127.0.0.1:8080/v1",
                    provider="omlx",
                    health=BackendHealth(models=["model-a"]),
                ).model_dump(mode="json"),
                Backend(
                    id="b",
                    base_url="http://127.0.0.1:11434/v1",
                    provider="ollama",
                    health=BackendHealth(models=["model-b"]),
                ).model_dump(mode="json"),
            ],
        )
    )
    backends = registry.peer_agent_backends()
    assert len(backends) == 1
    assert set(backends[0].health.models) == {"model-a", "model-b"}


def test_peer_agent_backends_ignore_peer_remote_rows() -> None:
    """A peer's own remote (peer:) rows must not echo transitively."""
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-gateway",
            listen_url="http://10.0.0.32:11400",
            backends=[
                Backend(
                    id="local-omlx",
                    base_url="http://127.0.0.1:8080/v1",
                    provider="omlx",
                    local=True,
                    health=BackendHealth(models=["served-here"]),
                ).model_dump(mode="json"),
                Backend(
                    id="peer:other-agent",
                    base_url="http://10.0.0.99:11400/v1",
                    provider="custom",
                    local=False,
                    health=BackendHealth(models=["served-elsewhere"]),
                ).model_dump(mode="json"),
            ],
        )
    )
    backends = registry.peer_agent_backends()
    assert len(backends) == 1
    assert backends[0].health.models == ["served-here"]


def test_peer_agent_backends_seed_in_flight_from_local_rows() -> None:
    """Heartbeat-reported peer load feeds local_spillover decisions."""
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="peer-busy",
            listen_url="http://10.0.0.32:11400",
            backends=[
                Backend(
                    id="local-omlx",
                    base_url="http://127.0.0.1:8080/v1",
                    provider="omlx",
                    local=True,
                    in_flight=2,
                    health=BackendHealth(models=["m"]),
                ).model_dump(mode="json"),
                Backend(
                    id="local-ollama",
                    base_url="http://127.0.0.1:11434/v1",
                    provider="ollama",
                    local=True,
                    in_flight=1,
                    health=BackendHealth(models=["m"]),
                ).model_dump(mode="json"),
                Backend(
                    id="peer:other",
                    base_url="http://10.0.0.99:11400/v1",
                    provider="custom",
                    local=False,
                    in_flight=7,
                    health=BackendHealth(models=["m"]),
                ).model_dump(mode="json"),
            ],
        )
    )
    backends = registry.peer_agent_backends()
    assert len(backends) == 1
    # Sum of local rows only (2 + 1); the peer's own remote hops are not ours.
    assert backends[0].in_flight == 3
