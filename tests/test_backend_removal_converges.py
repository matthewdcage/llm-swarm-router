"""Removing a backend from config must remove its pool row (no restart).

A user deleted a second vLLM entry from `[[routing.backends]]`; the
Backends page kept listing it. The dashboard was innocent — it renders
`GET /netllm/v1/status` faithfully. Two agent-side defects kept the row
alive:

1. `merge_discovered_provider_urls` learned the override's URL into
   `discovery.provider_urls[<provider>]`. `scan_local_providers` probes
   every enabled override, and those result rows carry the override's
   provider id, so a hand-authored vLLM URL became a permanent vLLM
   discovery candidate. Deleting the override changed nothing: the next
   scan rediscovered the URL and re-created the row.
2. `RouterPool.prune_local_provider_rows` only considered rows whose
   provider was still listed in `discovery.providers`, so a row backed by
   an override with a provider outside that roster (`custom`, `openai`, …)
   could never be pruned at all.

These tests drive the real scanner (`candidate_urls_for_provider`,
`merge_discovered_provider_urls`, `scan_results_to_backends`) with only
the socket-level probe faked, so both defects are exercised end to end.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import (
    Backend,
    BackendOverride,
    NetllmConfig,
    load_config,
    save_config,
)
from netllm_core.pool import RouterPool

URL_A = "http://127.0.0.1:44397/v1"
URL_B = "http://127.0.0.1:34825/v1"


@pytest.fixture
def listening(monkeypatch: pytest.MonkeyPatch) -> Iterator[set[str]]:
    """URLs that answer `GET /v1/models`. Both stay up the whole test:
    the backend must vanish because config says so, not because the
    process died."""
    online = {URL_A, URL_B}

    async def fake_probe(
        base_url: str,
        client: httpx.AsyncClient,
        *,
        api_key: str | None = None,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        if base_url.rstrip("/") in {u.rstrip("/") for u in online}:
            return {
                "status": "online",
                "http_status": 200,
                "model_count": 1,
                "models": ["demo-model"],
            }
        return {
            "status": "offline",
            "http_status": None,
            "model_count": 0,
            "models": [],
        }

    monkeypatch.setattr("netllm_discovery.local.probe_openai_compat", fake_probe)
    yield online


def _two_backend_config(provider: str = "vllm") -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.discovery.providers = [provider] if provider in cfg.discovery.providers else []
    cfg.routing.backends = [
        BackendOverride(base_url=URL_A, provider=provider, enabled=True, local=True),
        BackendOverride(base_url=URL_B, provider=provider, enabled=True, local=True),
    ]
    return cfg


def _status_urls(client: TestClient) -> list[str]:
    body = client.get("/netllm/v1/status?scan=1").json()
    return [b["base_url"] for b in body.get("backends", [])]


@pytest.mark.parametrize("provider", ["vllm", "custom"])
def test_removing_backend_override_drops_the_pool_row(
    tmp_path: Path, listening: set[str], provider: str
) -> None:
    """`vllm` is an enabled discovery provider, `custom` is not — the row
    must disappear either way, and on the very next status read."""
    cfg_path = tmp_path / "config.toml"
    cfg = _two_backend_config(provider)
    save_config(cfg, cfg_path)

    with TestClient(create_app(cfg, config_path=cfg_path)) as client:
        assert sorted(_status_urls(client)) == sorted([URL_A, URL_B])

        resp = client.post(
            "/netllm/v1/admin/config",
            json={
                "routing": {
                    "backends": [
                        {
                            "base_url": URL_A,
                            "provider": provider,
                            "api_format": None,
                            "enabled": True,
                            "local": True,
                            "api_key_env": "",
                            "max_concurrency": 0,
                        }
                    ]
                }
            },
        )
        assert resp.status_code == 200, resp.text

        saved = load_config(cfg_path)
        assert [b.base_url for b in saved.routing.backends] == [URL_A]
        # The override URL must never have been learned as a discovery
        # candidate — that is what made the deletion un-doable.
        assert URL_B not in saved.discovery.provider_urls.get(provider, [])

        assert _status_urls(client) == [URL_A]
        client.post("/netllm/v1/admin/discover")
        assert _status_urls(client) == [URL_A]


def test_disabled_override_row_disappears(tmp_path: Path, listening: set[str]) -> None:
    """`enabled = false` is equivalent to removal for the pool: no row at
    all rather than a row with `enabled: false`. The scan skips disabled
    overrides, so there is nothing to synthesise a disabled row from, and
    /status never claims a backend the router would not route to."""
    cfg_path = tmp_path / "config.toml"
    cfg = _two_backend_config("custom")
    save_config(cfg, cfg_path)

    with TestClient(create_app(cfg, config_path=cfg_path)) as client:
        assert sorted(_status_urls(client)) == sorted([URL_A, URL_B])
        resp = client.post(
            "/netllm/v1/admin/config",
            json={
                "routing": {
                    "backends": [
                        {"base_url": URL_A, "provider": "custom", "enabled": True},
                        {"base_url": URL_B, "provider": "custom", "enabled": False},
                    ]
                }
            },
        )
        assert resp.status_code == 200, resp.text
        assert _status_urls(client) == [URL_A]


def test_prune_local_rows_spares_peer_and_cloud_rows() -> None:
    """The pruner owns local/override rows only; peers and cloud
    providers are pruned by their own lifecycle hooks."""
    pool = RouterPool()
    scanned = Backend(
        id="ollama", base_url="http://127.0.0.1:11434/v1", provider="ollama", local=True
    )
    gone = Backend(
        id="lms", base_url="http://127.0.0.1:1234/v1", provider="lmstudio", local=True
    )
    remote_override = Backend(
        id="remote", base_url="https://gpu.example/v1", provider="custom", local=False
    )
    cloud = Backend(
        id="cloud-openai",
        base_url="https://api.openai.com/v1",
        provider="custom",
        local=False,
        cloud_provider="openai",
    )
    peer = Backend(
        id="peer:abc",
        base_url="http://10.0.0.9:11400/v1",
        provider="custom",
        local=False,
    )
    pool.set_backends([scanned, gone, remote_override, cloud, peer])

    pool.prune_local_rows({scanned.base_url})

    urls = {b.base_url for b in pool.backends}
    assert gone.base_url not in urls
    assert remote_override.base_url not in urls  # a removed remote override goes too
    assert scanned.base_url in urls
    assert cloud.base_url in urls
    assert peer.base_url in urls
