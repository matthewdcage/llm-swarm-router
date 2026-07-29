"""Regression: UI-style /netllm/v1/status polling must not HTTP-probe peer agents."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from netllm_core.health import probe_openai_compat_sync


@patch("netllm_core.pool.probe_openai_compat_sync", wraps=probe_openai_compat_sync)
def test_status_poll_never_probes_peer_agent_openai_surface(
    mock_probe: object,
    two_agent_mesh: dict[str, object],
) -> None:
    base_a = str(two_agent_mesh["base_a"])
    base_b = str(two_agent_mesh["base_b"])
    peer_port = base_b.rsplit(":", maxsplit=1)[-1]

    with httpx.Client(timeout=30.0) as client:
        for _ in range(5):
            resp = client.get(f"{base_a}/netllm/v1/status")
            assert resp.status_code == 200
        # probe=1 refreshes local backends only; must not hit peer /v1 surfaces.
        for _ in range(5):
            resp = client.get(f"{base_a}/netllm/v1/status", params={"probe": "1"})
            assert resp.status_code == 200

    probed_urls = [call[0][0] for call in mock_probe.call_args_list if call[0]]
    for url in probed_urls:
        assert peer_port not in url.split("/v1", maxsplit=1)[0], (
            f"status poll must not probe peer agent /v1 surface: {url}"
        )
