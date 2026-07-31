"""F-30 regression: streaming to anthropic-format backends must not pass an
invalid ``stream`` kwarg into the real Anthropic SDK.

These tests drive ``AnthropicUpstream.messages_stream`` through the *real*
``anthropic`` SDK class with an httpx transport stub — the SDK's own method
signatures are exercised, so a bad kwarg fails here exactly as it does in
production (a mocked ``AsyncAnthropic`` cannot catch that class of bug).
"""

from __future__ import annotations

import inspect
import json

import httpx
import pytest
from anthropic.resources.messages import AsyncMessages
from netllm_sdk_anthropic.client import AnthropicUpstream

# A minimal but wire-accurate Anthropic Messages SSE stream.
_SSE_EVENTS: list[tuple[str, dict]] = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_stream_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-haiku-20241022",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        },
    ),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hi"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


def _sse_body() -> bytes:
    return b"".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
        for name, data in _SSE_EVENTS
    )


def _upstream_with_stub_transport(
    captured: list[httpx.Request],
) -> AnthropicUpstream:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_body(),
        )

    upstream = AnthropicUpstream("sk-test", base_url="https://upstream.test")
    # Swap only the transport; the real SDK request/stream machinery still
    # runs. Clear proxy mounts so env HTTPS_PROXY cannot bypass the stub.
    inner = upstream._client._client
    inner._transport = httpx.MockTransport(handler)
    inner._mounts = {}
    return upstream


PAYLOAD = {
    "model": "claude-3-5-haiku-20241022",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "hi"}],
}


def test_f30_sdk_stream_helper_rejects_stream_kwarg() -> None:
    """Documents the F-30 root cause: ``messages.stream()`` has no ``stream``
    parameter, so any implementation forwarding one dies with TypeError."""
    sig = inspect.signature(AsyncMessages.stream)
    assert "stream" not in sig.parameters
    assert not any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


@pytest.mark.asyncio
async def test_f30_messages_stream_reaches_wire_and_yields_raw_sse() -> None:
    """Pre-fix this raised before any I/O (TypeError wrapped as
    AnthropicUpstreamError); post-fix the request hits the wire with
    ``"stream": true`` in the JSON body and yields raw Anthropic SSE."""
    captured: list[httpx.Request] = []
    upstream = _upstream_with_stub_transport(captured)

    chunks = [chunk async for chunk in upstream.messages_stream(dict(PAYLOAD))]

    assert captured, "request never reached the transport"
    body = json.loads(captured[0].content)
    assert body["stream"] is True
    assert body["model"] == PAYLOAD["model"]

    event_names = [c.split("\n", 1)[0].removeprefix("event: ") for c in chunks]
    assert event_names == [name for name, _ in _SSE_EVENTS]
    # Only raw wire events — no SDK helper-synthesized types ("text", ...).
    for chunk in chunks:
        event_line, data_line, trailer = chunk.split("\n", 2)
        assert trailer == "\n"
        data = json.loads(data_line.removeprefix("data: "))
        assert data["type"] == event_line.removeprefix("event: ")


@pytest.mark.asyncio
async def test_f30_caller_supplied_stream_key_is_not_duplicated() -> None:
    """The agent forwards payloads that may already carry ``stream: true``
    (client.py must not turn that into a duplicate/invalid SDK kwarg)."""
    captured: list[httpx.Request] = []
    upstream = _upstream_with_stub_transport(captured)

    payload = {**PAYLOAD, "stream": True}
    chunks = [chunk async for chunk in upstream.messages_stream(payload)]

    assert chunks
    assert json.loads(captured[0].content)["stream"] is True
