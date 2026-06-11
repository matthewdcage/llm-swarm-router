"""End-to-end two-agent acceptance harness.

Standing acceptance test for the swarm promise: two real netllm agents
(uvicorn, real HTTP) each with their own mock OpenAI provider, registered
as peers of each other. Verifies that

- one endpoint exposes the combined model catalog,
- round_robin spreads same-model requests across both machines,
- agent-hop forwards carry ``x-netllm-local-only`` so a distributing peer
  never ping-pongs the request back into the mesh.

Loopback note: real swarms reject loopback peer URLs; the harness patches
``is_lan_reachable_agent_url`` so two agents on 127.0.0.1 can mesh in CI.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from netllm_agent.app import create_app
from netllm_core.models import BackendOverride, NetllmConfig

MODEL = "shared-model"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerThread:
    """Run an ASGI app under uvicorn in a daemon thread."""

    def __init__(self, app: Any, port: int) -> None:
        self.port = port
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", lifespan="on"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 15.0
        while not self.server.started:
            if time.monotonic() > deadline:
                raise TimeoutError(f"uvicorn on :{self.port} did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10.0)


def make_mock_provider(name: str, record: dict[str, Any]) -> FastAPI:
    """Minimal OpenAI-compatible inference server that records hits.

    Discovery runs a 1-token diagnose probe (max_tokens=1 + stream key)
    against override backends; those are counted as probes, not serves.
    """
    app = FastAPI()

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if payload.get("max_tokens") == 1 and "stream" in payload:
            record["probe_hits"] += 1
        else:
            record["hits"] += 1
        return {
            "id": f"cmpl-{name}-{record['hits']}",
            "object": "chat.completion",
            "created": 0,
            "model": payload.get("model", MODEL),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"served-by:{name}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    return app


def make_agent(provider_port: int, agent_port: int, record: dict[str, Any]) -> FastAPI:
    cfg = NetllmConfig()
    cfg.agent.listen = f"127.0.0.1:{agent_port}"
    cfg.agent.advertise = False
    cfg.swarm.mdns = False
    cfg.discovery.providers = []
    cfg.routing.default_strategy = "round_robin"
    cfg.routing.allow_remote = True
    cfg.routing.backends = [
        BackendOverride(
            base_url=f"http://127.0.0.1:{provider_port}/v1",
            provider="custom",
            local=True,
        )
    ]
    app = create_app(cfg)

    @app.middleware("http")
    async def inbound_recorder(request: Request, call_next: Any) -> Any:
        if request.url.path == "/v1/chat/completions":
            record["chat_inbound"] += 1
            record["local_only_headers"].append(
                request.headers.get("x-netllm-local-only")
            )
        return await call_next(request)

    return app


@pytest.fixture(scope="module")
def two_agent_mesh() -> Iterator[dict[str, Any]]:
    from unittest.mock import patch

    provider_a: dict[str, Any] = {"hits": 0, "probe_hits": 0}
    provider_b: dict[str, Any] = {"hits": 0, "probe_hits": 0}
    agent_a: dict[str, Any] = {"chat_inbound": 0, "local_only_headers": []}
    agent_b: dict[str, Any] = {"chat_inbound": 0, "local_only_headers": []}

    pa_port, pb_port = _free_port(), _free_port()
    aa_port, ab_port = _free_port(), _free_port()

    servers = [
        ServerThread(make_mock_provider("A", provider_a), pa_port),
        ServerThread(make_mock_provider("B", provider_b), pb_port),
        ServerThread(make_agent(pa_port, aa_port, agent_a), aa_port),
        ServerThread(make_agent(pb_port, ab_port, agent_b), ab_port),
    ]
    # Loopback peers are rejected in production; allow them for the harness.
    with patch(
        "netllm_discovery.swarm.is_lan_reachable_agent_url",
        lambda url: bool(url),
    ):
        for server in servers:
            server.start()

        base_a = f"http://127.0.0.1:{aa_port}"
        base_b = f"http://127.0.0.1:{ab_port}"
        with httpx.Client(timeout=30.0) as client:
            status_a = client.get(f"{base_a}/netllm/v1/status").json()
            status_b = client.get(f"{base_b}/netllm/v1/status").json()
            # Cross-register peers (mDNS is off in the harness).
            client.post(f"{base_a}/netllm/v1/heartbeat", json=status_b)
            client.post(f"{base_b}/netllm/v1/heartbeat", json=status_a)

        yield {
            "base_a": base_a,
            "base_b": base_b,
            "provider_a": provider_a,
            "provider_b": provider_b,
            "agent_a": agent_a,
            "agent_b": agent_b,
        }

        for server in servers:
            server.stop()


def test_combined_catalog_via_single_endpoint(two_agent_mesh: dict[str, Any]) -> None:
    with httpx.Client(timeout=30.0) as client:
        data = client.get(f"{two_agent_mesh['base_a']}/v1/models").json()
    model_ids = {m["id"] for m in data["data"]}
    assert MODEL in model_ids

    with httpx.Client(timeout=30.0) as client:
        status = client.get(f"{two_agent_mesh['base_a']}/netllm/v1/status").json()
    peer_rows = [b for b in status["backends"] if b["id"].startswith("peer:")]
    assert len(peer_rows) == 1
    assert MODEL in peer_rows[0]["health"]["models"]


def test_round_robin_spreads_load_without_ping_pong(
    two_agent_mesh: dict[str, Any],
) -> None:
    provider_a = two_agent_mesh["provider_a"]
    provider_b = two_agent_mesh["provider_b"]
    agent_a = two_agent_mesh["agent_a"]
    agent_b = two_agent_mesh["agent_b"]
    base_a = two_agent_mesh["base_a"]

    start_a, start_b = provider_a["hits"], provider_b["hits"]
    inbound_a_start = agent_a["chat_inbound"]
    total = 4

    with httpx.Client(timeout=60.0) as client:
        for _ in range(total):
            resp = client.post(
                f"{base_a}/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200, resp.text

    served_a = provider_a["hits"] - start_a
    served_b = provider_b["hits"] - start_b

    # Load spread: both machines did inference, nothing double-served.
    assert served_a + served_b == total
    assert served_a > 0
    assert served_b > 0

    # Loop safety: agent B is also round_robin and knows agent A as a
    # peer, yet must never bounce a hop back (A only sees the test client).
    assert agent_a["chat_inbound"] - inbound_a_start == total
    hop_headers = [h for h in agent_b["local_only_headers"] if h is not None]
    assert hop_headers, "agent B never saw a forwarded hop"
    assert all(h == "1" for h in hop_headers)
