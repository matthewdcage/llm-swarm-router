"""Tests for unified telemetry API and oMLX normalizers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.telemetry import TelemetryService
from netllm_core.models import NetllmConfig

FIXTURES = Path(__file__).parent / "fixtures" / "omlx"


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_telemetry_endpoint_schema(client: TestClient) -> None:
    resp = client.get("/netllm/v1/telemetry?watch=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == 1
    assert "router" in data
    assert data["omlx"]["available"] is False
    assert "history" in data
    assert len(data["history"]["router_rps"]) <= 60


def test_telemetry_records_router_usage(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    svc = TelemetryService(stats_path=stats_path)
    svc.record_usage(prompt_tokens=10, completion_tokens=5, prefill_duration=0.1)
    assert svc._session.requests == 1
    assert svc._session.prompt_tokens == 10
    assert svc._alltime.completion_tokens == 5


@pytest.mark.asyncio
async def test_telemetry_lazy_omlx_probe_without_watch() -> None:
    svc = TelemetryService()
    probe = AsyncMock(return_value={"available": True, "live": {"prefill_tps": 1.0}})
    with patch("netllm_agent.telemetry.probe_omlx_telemetry", probe):
        payload = await svc.build_payload(
            type(
                "S",
                (),
                {"pool": type("P", (), {"backends": []})(), "_shardless_fallbacks": 0},
            )(),
            scopes={"omlx"},
        )
    probe.assert_not_called()
    assert payload["omlx"]["available"] is False


@pytest.mark.asyncio
async def test_telemetry_probes_omlx_when_watching() -> None:
    svc = TelemetryService()
    svc.subscribe()
    probe = AsyncMock(
        return_value={
            "available": True,
            "live": {"prefill_tps": 10.0, "generation_tps": 5.0},
            "session": {"avg_prefill_tps": 9.0},
        }
    )
    fake_service = type(
        "S",
        (),
        {
            "pool": type(
                "P",
                (),
                {"backends": [], "routed_counts": {}, "capacity_rejections": {}},
            )(),
            "_shardless_fallbacks": 0,
        },
    )()
    with patch("netllm_agent.telemetry.probe_omlx_telemetry", probe):
        payload = await svc.build_payload(fake_service, scopes={"omlx"})
    probe.assert_called_once()
    assert payload["omlx"]["available"] is True
    svc.unsubscribe()


def test_normalize_omlx_stats_payload() -> None:
    from netllm_discovery.local import _normalize_omlx_stats_payload

    data = json.loads((FIXTURES / "stats_session.json").read_text(encoding="utf-8"))
    out = _normalize_omlx_stats_payload(data)
    assert out["total_prompt_tokens"] == 1200
    assert out["avg_generation_tps"] == 41.0


def test_telemetry_persists_alltime(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    svc = TelemetryService(stats_path=stats_path)
    svc.record_usage(prompt_tokens=5, completion_tokens=3)
    assert stats_path.is_file()
    svc2 = TelemetryService(stats_path=stats_path)
    assert svc2._alltime.prompt_tokens == 5
    assert svc2._alltime.completion_tokens == 3


def test_normalize_omlx_activity_payload() -> None:
    from netllm_discovery.local import _normalize_omlx_activity_payload

    data = json.loads((FIXTURES / "activity.json").read_text(encoding="utf-8"))
    out = _normalize_omlx_activity_payload(data)
    assert out["prefill_tps"] == 120.5
    assert out["generation_tps"] == 45.2


@pytest.mark.asyncio
async def test_probe_omlx_telemetry_parses_admin() -> None:
    from netllm_discovery.local import probe_omlx_telemetry

    session = json.loads((FIXTURES / "stats_session.json").read_text(encoding="utf-8"))
    alltime = json.loads((FIXTURES / "stats_alltime.json").read_text(encoding="utf-8"))
    activity = json.loads((FIXTURES / "activity.json").read_text(encoding="utf-8"))

    class FakeResponse:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self.status_code = status
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            params = kwargs.get("params") or {}
            if url.endswith("/api/server-info") or url.endswith("/api/status"):
                return FakeResponse({"loaded_models": ["demo-model"]})
            if url.endswith("/api/stats") and params.get("scope") == "session":
                return FakeResponse(session)
            if url.endswith("/api/stats") and params.get("scope") == "alltime":
                return FakeResponse(alltime)
            if url.endswith("/api/activity"):
                return FakeResponse(activity)
            raise AssertionError(f"unexpected url {url} params={params}")

    backends = [
        type(
            "B",
            (),
            {
                "provider": "omlx",
                "enabled": True,
                "base_url": "http://127.0.0.1:8099/v1",
                "health": type("H", (), {"status": "online", "model_count": 1})(),
            },
        )()
    ]
    stats = await probe_omlx_telemetry(backends, FakeClient())  # type: ignore[arg-type]
    assert stats is not None
    assert stats["available"] is True
    assert stats["session"]["total_requests"] == 12
    assert stats["alltime"]["total_requests"] == 25600
    assert stats["live"]["generation_tps"] == 45.2
