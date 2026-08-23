"""Three-agent gateway mesh E2E — coordinator spreads concurrent load."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import httpx
from test_e2e_two_agents import (
    MODEL,
    ServerThread,
    _free_port,
    _register_peers,
    make_agent,
    make_mock_provider,
    make_slow_mock_provider,
)


def test_gateway_coordinator_spreads_across_two_peers() -> None:
    provider_gw: dict[str, Any] = {"hits": 0, "probe_hits": 0}
    provider_p1: dict[str, Any] = {"hits": 0, "probe_hits": 0}
    provider_p2: dict[str, Any] = {"hits": 0, "probe_hits": 0}
    rec_gw: dict[str, Any] = {"chat_inbound": 0, "local_only_headers": []}
    rec_p1: dict[str, Any] = {"chat_inbound": 0, "local_only_headers": []}
    rec_p2: dict[str, Any] = {"chat_inbound": 0, "local_only_headers": []}

    with patch(
        "netllm_discovery.swarm.is_lan_reachable_agent_url",
        lambda url: bool(url),
    ):
        srv_gw_p = ServerThread(make_slow_mock_provider("GW", provider_gw, 0.35), 0)
        srv_p1_p = ServerThread(make_mock_provider("P1", provider_p1), 0)
        srv_p2_p = ServerThread(make_mock_provider("P2", provider_p2), 0)
        for srv in (srv_gw_p, srv_p1_p, srv_p2_p):
            srv.start()

        gw_port, p1_port, p2_port = _free_port(), _free_port(), _free_port()
        servers = [
            srv_gw_p,
            srv_p1_p,
            srv_p2_p,
            ServerThread(
                make_agent(
                    srv_gw_p.port,
                    gw_port,
                    rec_gw,
                    strategy="least_load",
                    mesh_coordinator="gateway",
                    role="gateway",
                    agent_max_concurrency=1,
                ),
                gw_port,
            ),
            ServerThread(
                make_agent(srv_p1_p.port, p1_port, rec_p1, strategy="local_spillover"),
                p1_port,
            ),
            ServerThread(
                make_agent(srv_p2_p.port, p2_port, rec_p2, strategy="local_spillover"),
                p2_port,
            ),
        ]
        for server in servers[3:]:
            server.start()
        try:
            base_gw = f"http://127.0.0.1:{gw_port}"
            base_p1 = f"http://127.0.0.1:{p1_port}"
            base_p2 = f"http://127.0.0.1:{p2_port}"
            with httpx.Client(timeout=90.0) as client:
                _register_peers(client, base_gw, base_p1)
                _register_peers(client, base_gw, base_p2)
                _register_peers(client, base_p1, base_p2)
                start_gw, start_p1, start_p2 = (
                    provider_gw["hits"],
                    provider_p1["hits"],
                    provider_p2["hits"],
                )

                def _post(_: int) -> httpx.Response:
                    with httpx.Client(timeout=90.0) as thread_client:
                        return thread_client.post(
                            f"{base_gw}/v1/chat/completions",
                            json={
                                "model": MODEL,
                                "messages": [{"role": "user", "content": "hi"}],
                            },
                        )

                with ThreadPoolExecutor(max_workers=3) as pool:
                    results = list(pool.map(_post, range(3)))
                for resp in results:
                    assert resp.status_code == 200, resp.text

                remote_hits = (provider_p1["hits"] - start_p1) + (
                    provider_p2["hits"] - start_p2
                )
                assert remote_hits >= 1
                assert provider_gw["hits"] - start_gw >= 1
        finally:
            for server in servers:
                server.stop()
