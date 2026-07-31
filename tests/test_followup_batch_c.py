"""Regression tests for follow-up audit batch C (F-33, F-45, F-47, F-48).

Findings: docs/architecture/09-follow-up-audit-2026-07-31.md.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from netllm_agent.metrics import REQUESTS_TOTAL
from netllm_agent.service import AgentService
from netllm_agent.telemetry import TelemetryService
from netllm_core.models import (
    HOPS_HEADER,
    LOCAL_ONLY_HEADER,
    SOURCE_HEADER,
    Backend,
    NetllmConfig,
    SourceConfig,
)
from netllm_core.source_identity import resolve_source


def _service(sources: list[SourceConfig] | None = None) -> AgentService:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    if sources is not None:
        cfg.routing.sources = sources
    return AgentService(cfg)


def _backend(local: bool = True) -> Backend:
    return Backend(
        id="omlx:http://127.0.0.1:8080/v1" if local else "peer:remote-agent",
        base_url=(
            "http://127.0.0.1:8080/v1" if local else "http://192.168.1.11:11400/v1"
        ),
        provider="omlx" if local else "custom",
        local=local,
    )


def _requests_total_ok(backend: Backend, model: str) -> float:
    return REQUESTS_TOTAL.labels(
        backend=backend.base_url, model=model, status="ok"
    )._value.get()


# ---------------------------------------------------------------------------
# F-33 — streamed Messages traffic must record success accounting
# ---------------------------------------------------------------------------


ANTHROPIC_SSE_CHUNKS = [
    'event: message_start\ndata: {"type": "message_start", "message": '
    '{"id": "msg_1", "usage": {"input_tokens": 25, "output_tokens": 1}}}\n\n',
    'event: content_block_delta\ndata: {"type": "content_block_delta", '
    '"delta": {"type": "text_delta", "text": "hi"}}\n\n',
    'event: message_delta\ndata: {"type": "message_delta", '
    '"usage": {"output_tokens": 50}}\n\n',
    'event: message_stop\ndata: {"type": "message_stop"}\n\n',
]


async def test_f33_proxy_messages_stream_records_success_accounting() -> None:
    """F-33: proxy_messages_stream must mark_success, count the request,
    observe latency, and capture token usage from the SSE stream."""
    service = _service()
    backend = _backend()
    service.pool.backends.append(backend)
    model = "llama3"

    async def fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        for chunk in ANTHROPIC_SSE_CHUNKS:
            yield chunk

    ok_before = _requests_total_ok(backend, model)
    with (
        patch.object(service, "refresh_local_backends", AsyncMock(return_value=[])),
        patch.object(
            service, "_offload_if_probing", AsyncMock(side_effect=[backend, None])
        ),
        patch.object(service, "_messages_stream_on_backend", fake_stream),
        patch.object(
            service.pool, "mark_success", wraps=service.pool.mark_success
        ) as mark_success,
    ):
        chunks = [
            c
            async for c in service.proxy_messages_stream(
                {"model": model, "messages": [], "stream": True}
            )
        ]

    assert chunks == ANTHROPIC_SSE_CHUNKS
    mark_success.assert_called_once()
    assert _requests_total_ok(backend, model) == ok_before + 1
    assert service.telemetry._session.requests == 1
    assert service.telemetry._session.prompt_tokens == 25
    assert service.telemetry._session.completion_tokens == 50


async def test_f33_chat_stream_wrapper_captures_openai_usage_chunk() -> None:
    """F-33: _stream_with_metrics must parse the stream_options
    include_usage chunk (when present) into token telemetry."""
    service = _service()
    backend = _backend()
    model = "llama3"

    chat_chunks = [
        'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n',
        'data: {"choices": [], "usage": '
        '{"prompt_tokens": 7, "completion_tokens": 3}}\n\n',
        "data: [DONE]\n\n",
    ]

    class FakeClient:
        async def chat_completion_stream(
            self, payload: dict[str, Any]
        ) -> AsyncIterator[str]:
            for chunk in chat_chunks:
                yield chunk

    ok_before = _requests_total_ok(backend, model)
    out = [
        c
        async for c in service._stream_with_metrics(
            FakeClient(), {"model": model}, backend, model
        )
    ]
    assert out == chat_chunks
    assert _requests_total_ok(backend, model) == ok_before + 1
    assert service.telemetry._session.requests == 1
    assert service.telemetry._session.prompt_tokens == 7
    assert service.telemetry._session.completion_tokens == 3


# ---------------------------------------------------------------------------
# F-45 — _anthropic_api_key env fallback must reject placeholder keys
# ---------------------------------------------------------------------------


def test_f45_anthropic_env_fallback_rejects_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-45: a netllm-* placeholder in ANTHROPIC_API_KEY must never be
    forwarded upstream, mirroring the OpenAI twin."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "netllm-local")
    assert AgentService._anthropic_api_key({}) == ""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "netllm-claude-code.s3cret")
    assert AgentService._anthropic_api_key({}) == ""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    assert AgentService._anthropic_api_key({}) == "sk-ant-real"


def test_f45_openai_env_fallback_rejects_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "netllm-local")
    assert AgentService._openai_api_key({}) == ""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    assert AgentService._openai_api_key({}) == "sk-real"


# ---------------------------------------------------------------------------
# F-47 — peer hops must forward the resolved source id (attribution only)
# ---------------------------------------------------------------------------


def test_f47_peer_forward_headers_carry_resolved_source() -> None:
    """F-47: x-netllm-source rides along on peer hops so serving-node
    telemetry attributes mesh traffic to the real source."""
    service = _service(sources=[SourceConfig(id="cursor")])
    peer = _backend(local=False)
    fwd = service._peer_forward_headers(peer, {SOURCE_HEADER: "cursor"})
    assert fwd == {
        LOCAL_ONLY_HEADER: "1",
        HOPS_HEADER: "1",
        SOURCE_HEADER: "cursor",
    }


def test_f47_peer_forward_headers_omit_default_and_unresolved() -> None:
    service = _service(sources=[SourceConfig(id="cursor")])
    peer = _backend(local=False)
    local = _backend(local=True)
    # No source signal: loop-guard headers only, no default attribution.
    assert service._peer_forward_headers(peer, {}) == {
        LOCAL_ONLY_HEADER: "1",
        HOPS_HEADER: "1",
    }
    assert service._peer_forward_headers(peer, None) == {
        LOCAL_ONLY_HEADER: "1",
        HOPS_HEADER: "1",
    }
    # Hop count still propagates.
    assert service._peer_forward_headers(peer, {HOPS_HEADER: "1"}) == {
        LOCAL_ONLY_HEADER: "1",
        HOPS_HEADER: "2",
    }
    assert service._peer_forward_headers(local, {SOURCE_HEADER: "cursor"}) is None


def test_f47_secret_gated_source_not_claimable_by_bare_forwarded_header() -> None:
    """F-47 security constraint: the receiving peer must not grant a
    secret-gated identity from the bare forwarded header; a plain source
    still attributes."""
    sources = [
        SourceConfig(id="buzz", secret="s3cret"),
        SourceConfig(id="cursor"),
    ]
    # What the receiving peer sees: just the forwarded attribution header
    # (peer hops authenticate with the cluster token, not the secret).
    gated = resolve_source(headers={SOURCE_HEADER: "buzz"}, sources=sources)
    assert gated.id == "default"
    assert gated.authenticated is False
    plain = resolve_source(headers={SOURCE_HEADER: "cursor"}, sources=sources)
    assert plain.id == "cursor"

    # And the sending side never forwards an identity the caller did not
    # prove: a bare secret-gated header resolves to default, so nothing
    # is forwarded.
    service = _service(sources=sources)
    peer = _backend(local=False)
    fwd = service._peer_forward_headers(peer, {SOURCE_HEADER: "buzz"})
    assert fwd == {LOCAL_ONLY_HEADER: "1", HOPS_HEADER: "1"}
    # A caller that did prove the secret forwards attribution only.
    fwd = service._peer_forward_headers(peer, {"x-api-key": "netllm-buzz.s3cret"})
    assert fwd == {
        LOCAL_ONLY_HEADER: "1",
        HOPS_HEADER: "1",
        SOURCE_HEADER: "buzz",
    }


# ---------------------------------------------------------------------------
# F-48 — stats.json durability
# ---------------------------------------------------------------------------


def test_f48_corrupt_stats_json_warns_and_resets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """F-48: a partial/corrupt stats.json must log a warning instead of
    silently zeroing the all-time counters."""
    stats_path = tmp_path / "stats.json"
    stats_path.write_text('{"requests": 12, "prompt_tok', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="netllm_agent.telemetry"):
        svc = TelemetryService(stats_path=stats_path)
    assert svc._alltime.requests == 0  # graceful reset, service still up
    assert any(
        "stats" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_f48_save_alltime_is_atomic(tmp_path: Path) -> None:
    """F-48: the save path must write a tmp file in the same directory and
    os.replace it over stats.json — never truncate the live file."""
    stats_path = tmp_path / "stats.json"
    svc = TelemetryService(stats_path=stats_path)
    svc.record_usage(prompt_tokens=10, completion_tokens=5)

    with patch("netllm_agent.telemetry.os.replace", wraps=os.replace) as replace:
        svc._save_alltime()

    replace.assert_called_once()
    src, dst = replace.call_args.args[:2]
    assert Path(dst) == stats_path
    assert Path(src).parent == stats_path.parent  # same dir → same filesystem
    assert not Path(src).exists()  # tmp file consumed by the rename
    data = json.loads(stats_path.read_text(encoding="utf-8"))
    assert data["requests"] == 1
    assert data["prompt_tokens"] == 10


def test_usage_parsers_tolerate_non_numeric_values() -> None:
    """Verifier follow-up: malformed backend usage must not raise mid-stream."""
    from netllm_agent.service import AgentService

    chunk = (
        'data: {"usage": {"input_tokens": "not-a-number", "output_tokens": null}}\n\n'
    )
    assert AgentService._usage_from_sse_chunk(chunk) == (0, 0)
    assert AgentService._usage_from_response(
        {"usage": {"prompt_tokens": {"nested": 1}, "completion_tokens": "9"}}
    ) == (0, 9)
