"""Telemetry key-set contract (F-49, plan-f24-f26.md §3 Phase 10).

``docs/telemetry-api.md`` is normative. Three telemetry clients — the web
dashboard (``static/pages/overview.js``) and the two Swift menubar files — used
to
hand-mirror the payload shape and re-derive ``total_tokens`` client-side, which
is the whole of F-49: a server-side key rename or drop showed up as a silently
wrong number in the UI rather than as a failure.

This module makes the doc the gate, in both directions:

* the server must emit **exactly** the documented key set — an undocumented
  new key fails here, and so does a documented key the server stopped emitting;
* every documented key must actually appear in the doc's key tables, so the
  literals below cannot drift away from the prose they claim to encode.

Adding a telemetry field is therefore a three-line change: emit it, add a row
to ``docs/telemetry-api.md``, add it to the frozen set here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.telemetry import TelemetryService
from netllm_core.models import NetllmConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_DOC = REPO_ROOT / "docs" / "telemetry-api.md"
# The dashboard's telemetry panels moved out of the monolithic dashboard.js
# into the Overview page when the UI was split into eleven page modules; the
# Serving tab merged into Overview. Same renderer, new file.
OVERVIEW_JS = (
    REPO_ROOT
    / "packages"
    / "netllm-agent"
    / "src"
    / "netllm_agent"
    / "static"
    / "pages"
    / "overview.js"
)

# --------------------------------------------------------------------------- #
# The documented contract, transcribed from docs/telemetry-api.md.
# --------------------------------------------------------------------------- #

TOP_LEVEL_ALWAYS = {"schema_version", "ts", "host", "subscribers"}
TOP_LEVEL_OPTIONAL = {"router", "omlx", "history"}

ROUTER_KEYS = {
    "session",
    "alltime",
    "routed_requests",
    "capacity_rejections",
    "shardless_fallbacks",
    "in_flight_total",
    "windows",
    "latency",
    "live",
    "backends",
}

# UI-1: the windowed, dimensioned ledger.
ROUTER_WINDOWS_KEYS = {
    "counters_since",
    "spans_s",
    "by_backend",
    "by_model",
    "by_policy",
    "by_source",
    "truncated",
}

ROUTER_WINDOWS_TRUNCATED_KEYS = {"by_backend", "by_model", "by_policy", "by_source"}

ROUTER_SOURCE_KEYS = {"requests", "surfaces", "top_models", "last_seen_at"}

# UI-2: real TTFT percentiles and rolling throughput.
ROUTER_LATENCY_KEYS = {"ttft_p50_ms", "ttft_p95_ms", "ttft_samples", "window_s"}

ROUTER_LIVE_KEYS = {
    "prefill_tps",
    "generation_tps",
    "requests_per_s",
    "window_s",
}

ROUTER_SCOPE_KEYS = {
    "requests",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "avg_prefill_tps",
    "avg_generation_tps",
    "uptime_s",
}

ROUTER_BACKEND_KEYS = {
    "id",
    "provider",
    "base_url",
    "health",
    "in_flight",
    "p50_ms",
    "p95_ms",
    "samples",
    "window_s",
}

OMLX_KEYS = {
    "available",
    "admin_url",
    "session",
    "alltime",
    "live",
    "primary_model",
    "loaded_models",
    "model_memory_used",
}

OMLX_SCOPE_KEYS = {
    "total_prompt_tokens",
    "total_completion_tokens",
    "total_tokens",
    "total_cached_tokens",
    "cache_efficiency_pct",
    "total_requests",
    "avg_prefill_tps",
    "avg_generation_tps",
}

OMLX_LIVE_KEYS = {
    "prefill_tps",
    "generation_tps",
    "active_requests",
    "waiting_requests",
}

HOST_KEYS = {"cpu_percent", "memory_used_gb", "memory_total_gb", "memory_percent"}

HISTORY_KEYS = {"router_rps", "router_tps", "omlx_pp_tps", "omlx_tg_tps"}

ALL_DOCUMENTED_KEYS = (
    TOP_LEVEL_ALWAYS
    | TOP_LEVEL_OPTIONAL
    | ROUTER_KEYS
    | ROUTER_WINDOWS_KEYS
    | ROUTER_SOURCE_KEYS
    | ROUTER_LATENCY_KEYS
    | ROUTER_LIVE_KEYS
    | ROUTER_SCOPE_KEYS
    | ROUTER_BACKEND_KEYS
    | OMLX_KEYS
    | OMLX_SCOPE_KEYS
    | OMLX_LIVE_KEYS
    | HOST_KEYS
    | HISTORY_KEYS
)


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Server emits exactly the documented set
# --------------------------------------------------------------------------- #


def test_top_level_key_set_is_exactly_documented(client: TestClient) -> None:
    data = client.get("/netllm/v1/telemetry?watch=0").json()
    assert set(data) == TOP_LEVEL_ALWAYS | TOP_LEVEL_OPTIONAL


def test_scope_selection_drops_exactly_the_optional_blocks(
    client: TestClient,
) -> None:
    data = client.get("/netllm/v1/telemetry?watch=0&scopes=router&history=0").json()
    assert set(data) == TOP_LEVEL_ALWAYS | {"router"}


def test_router_block_key_set_is_exactly_documented(client: TestClient) -> None:
    router = client.get("/netllm/v1/telemetry?watch=0").json()["router"]
    assert set(router) == ROUTER_KEYS
    assert set(router["session"]) == ROUTER_SCOPE_KEYS
    assert set(router["alltime"]) == ROUTER_SCOPE_KEYS


def test_router_windows_block_key_set_is_exactly_documented(client: TestClient) -> None:
    """UI-1. Asserted on a *populated* ledger: an empty one cannot show the
    per-dimension row shape, which is where the contract actually lives."""
    service = client.app.state.service  # type: ignore[attr-defined]
    service.telemetry.record_usage(
        prompt_tokens=3,
        completion_tokens=4,
        backend_id="alpha",
        model="gemma4:27b",
        source_id="cursor",
        policy_key="0:local-openai",
        surface="openai",
    )
    windows = client.get("/netllm/v1/telemetry?watch=0").json()["router"]["windows"]
    assert set(windows) == ROUTER_WINDOWS_KEYS
    assert set(windows["truncated"]) == ROUTER_WINDOWS_TRUNCATED_KEYS
    assert windows["spans_s"], "spans are server-declared; the list may not be empty"
    span_keys = {str(span) for span in windows["spans_s"]}
    for dimension in ("by_backend", "by_model", "by_policy"):
        assert windows[dimension], f"{dimension} should carry the recorded request"
        for row in windows[dimension].values():
            assert set(row) == span_keys
    source = windows["by_source"]["cursor"]
    assert set(source) == ROUTER_SOURCE_KEYS
    assert set(source["requests"]) == span_keys
    assert source["top_models"] == [{"model": "gemma4:27b", "count": 1}]


def test_router_latency_and_live_key_sets_are_exactly_documented(
    client: TestClient,
) -> None:
    """UI-2. With no traffic every percentile must be null, never 0.0."""
    router = client.get("/netllm/v1/telemetry?watch=0").json()["router"]
    assert set(router["latency"]) == ROUTER_LATENCY_KEYS
    assert set(router["live"]) == ROUTER_LIVE_KEYS
    assert router["latency"]["ttft_samples"] == 0
    assert router["latency"]["ttft_p50_ms"] is None
    assert router["latency"]["ttft_p95_ms"] is None
    assert router["live"]["prefill_tps"] is None
    assert router["live"]["generation_tps"] is None


def test_router_scope_reports_null_not_zero_without_measured_durations(
    client: TestClient,
) -> None:
    """The UI-2 regression guard, stated as a wire assertion: a request that
    reported prompt tokens but no measured prefill leaves the rate absent."""
    service = client.app.state.service  # type: ignore[attr-defined]
    service.telemetry.record_usage(prompt_tokens=100, completion_tokens=50)
    session = client.get("/netllm/v1/telemetry?watch=0").json()["router"]["session"]
    assert session["prompt_tokens"] == 100
    assert session["avg_prefill_tps"] is None
    assert session["avg_generation_tps"] is None


def test_router_backend_rows_key_set_is_exactly_documented(
    client: TestClient,
) -> None:
    """A backend row is the one router sub-object an empty pool cannot show."""
    from netllm_core.pool import Backend

    service = client.app.state.service  # type: ignore[attr-defined]
    service.pool.set_backends(
        [Backend(id="alpha", provider="lmstudio", base_url="http://alpha/v1")]
    )
    rows = client.get("/netllm/v1/telemetry?watch=0").json()["router"]["backends"]
    assert rows, "expected the appended backend row"
    for row in rows:
        assert set(row) == ROUTER_BACKEND_KEYS


def test_host_block_key_set_is_exactly_documented(client: TestClient) -> None:
    host = client.get("/netllm/v1/telemetry?watch=0").json()["host"]
    # psutil is a hard dependency of netllm-agent; `null` means it failed to
    # import, which the doc allows but CI must not silently accept.
    assert host is not None, "psutil import failed — host block is null"
    assert set(host) == HOST_KEYS


def test_history_block_key_set_is_exactly_documented(client: TestClient) -> None:
    history = client.get("/netllm/v1/telemetry?watch=0").json()["history"]
    assert set(history) == HISTORY_KEYS
    for values in history.values():
        assert isinstance(values, list)
        assert len(values) <= 60


def test_omlx_unavailable_shape_is_documented(client: TestClient) -> None:
    omlx = client.get("/netllm/v1/telemetry?watch=0").json()["omlx"]
    assert set(omlx) == {"available"}
    assert omlx["available"] is False


@pytest.mark.asyncio
async def test_omlx_available_block_key_set_is_exactly_documented() -> None:
    """Drive the real normalizers over a synthetic oMLX admin response."""
    from netllm_discovery.local import (
        _normalize_omlx_activity_payload,
        _normalize_omlx_stats_scope,
    )

    scope = _normalize_omlx_stats_scope(
        {
            "total_prompt_tokens": 10,
            "total_completion_tokens": 5,
            "total_cached_tokens": 2,
            "total_requests": 1,
            "avg_prefill_tps": 9.0,
            "avg_generation_tps": 4.0,
        }
    )
    assert set(scope) == OMLX_SCOPE_KEYS
    assert scope["total_tokens"] == 15, "total_tokens must be server-computed"

    live = _normalize_omlx_activity_payload(
        {"prefill_tps": 1.0, "generation_tps": 2.0, "active_requests": 1}
    )
    assert set(live) == OMLX_LIVE_KEYS

    probe = AsyncMock(
        return_value={
            "available": True,
            "admin_url": "http://omlx/admin",
            "session": scope,
            "alltime": scope,
            "live": live,
            "primary_model": "m",
            "loaded_models": ["m"],
            "model_memory_used": 1,
        }
    )
    svc = TelemetryService()
    svc.subscribe()
    fake_service: Any = type(
        "S",
        (),
        {"pool": type("P", (), {"backends": []})(), "_shardless_fallbacks": 0},
    )()
    with patch("netllm_agent.telemetry.probe_omlx_telemetry", probe):
        payload = await svc.build_payload(fake_service, scopes={"omlx"})
    assert set(payload["omlx"]) == OMLX_KEYS


# --------------------------------------------------------------------------- #
# The doc and the literals above cannot drift apart
# --------------------------------------------------------------------------- #


def test_every_documented_key_appears_in_the_doc() -> None:
    doc = TELEMETRY_DOC.read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z_][a-z0-9_]*)`", doc))
    missing = sorted(ALL_DOCUMENTED_KEYS - documented)
    assert not missing, (
        f"keys frozen here but absent from {TELEMETRY_DOC.name}: {missing}"
    )


def test_doc_declares_itself_normative() -> None:
    doc = TELEMETRY_DOC.read_text(encoding="utf-8")
    assert "normative" in doc
    assert "F-49" in doc


def test_dashboard_does_not_re_derive_total_tokens() -> None:
    """F-49's concrete defect: the client-side ``total_tokens`` sum is gone."""
    js = OVERVIEW_JS.read_text(encoding="utf-8")
    assert "total_tokens ??" not in js
    assert "prompt_tokens + scope.completion_tokens" not in js
    # Broader than the literal F-49 expression, which named the one accumulator
    # the old code happened to use: any client-side sum of the two component
    # counters re-derives the key, whatever it is spelled.
    assert "prompt_tokens +" not in js
    # And it still reads the server-supplied key. The scope block is now walked
    # by key name rather than by attribute (``session[key]`` over a ``[label,
    # key, format]`` table), so the assertion is on the key literal.
    assert '"total_tokens"' in js
