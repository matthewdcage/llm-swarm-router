"""Disambiguate local providers that share the same default port."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery.local import _classify_provider_at_url, _dedupe_scan_results


@pytest.mark.asyncio
async def test_classify_strata_by_health_loaded_key() -> None:
    cfg = NetllmConfig()
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=type(
            "R",
            (),
            {
                "status_code": 200,
                "json": lambda self: {"status": "ok", "loaded": False},
            },
        )()
    )
    with patch(
        "netllm_discovery.local.probe_omlx_admin",
        new_callable=AsyncMock,
        return_value=None,
    ):
        pid = await _classify_provider_at_url(
            "http://127.0.0.1:8080/v1",
            client,
            {"strata", "omlx"},
            cfg,
        )
    assert pid == "strata"


@pytest.mark.asyncio
async def test_dedupe_prefers_fingerprint_over_scan_order() -> None:
    cfg = NetllmConfig()
    cfg.discovery.providers = ["omlx", "strata"]
    client = AsyncMock()
    rows = [
        {
            "id": "omlx",
            "name": "oMLX",
            "base_url": "http://127.0.0.1:8080/v1",
            "status": "online",
            "model_count": 1,
            "models": ["m"],
        },
        {
            "id": "strata",
            "name": "Strata",
            "base_url": "http://127.0.0.1:8080/v1",
            "status": "online",
            "model_count": 1,
            "models": ["strata"],
        },
    ]
    with patch(
        "netllm_discovery.local._classify_provider_at_url",
        new_callable=AsyncMock,
        return_value="strata",
    ):
        enabled = set(cfg.discovery.providers)
        out = await _dedupe_scan_results(rows, client, enabled, cfg)
    online = [r for r in out if r.get("status") == "online"]
    assert len(online) == 1
    assert online[0]["id"] == "strata"
