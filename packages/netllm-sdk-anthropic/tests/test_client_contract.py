"""Contract tests for AnthropicUpstream adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from netllm_sdk_anthropic.client import AnthropicUpstream, AnthropicUpstreamError


@pytest.mark.asyncio
@patch("netllm_sdk_anthropic.client.AsyncAnthropic")
async def test_messages_create_passes_payload(mock_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.model_dump.return_value = {"id": "msg_1", "type": "message"}
    mock_client.messages.create = AsyncMock(return_value=mock_resp)

    upstream = AnthropicUpstream("sk-test", base_url="https://api.anthropic.com")
    payload = {
        "model": "claude-3-5-haiku-20241022",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
    }
    result = await upstream.messages_create(payload)
    assert result == {"id": "msg_1", "type": "message"}
    mock_client.messages.create.assert_awaited_once_with(**payload)


@pytest.mark.asyncio
@patch("netllm_sdk_anthropic.client.AsyncAnthropic")
async def test_messages_create_wraps_errors(mock_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    err = Exception("rate limited")
    err.status_code = 429  # type: ignore[attr-defined]
    mock_client.messages.create = AsyncMock(side_effect=err)

    upstream = AnthropicUpstream("sk-test")
    with pytest.raises(AnthropicUpstreamError) as exc_info:
        await upstream.messages_create({"model": "x", "max_tokens": 1, "messages": []})
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
@patch("netllm_sdk_anthropic.client.AsyncAnthropic")
async def test_messages_stream_sse_format(mock_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_cls.return_value = mock_client

    event = MagicMock()
    event.type = "content_block_delta"
    event.model_dump.return_value = {"type": "content_block_delta", "index": 0}

    async def stream_ctx(**_kwargs):
        class Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        ctx = Ctx()
        ctx.__aiter__ = lambda: iter([event])  # type: ignore[method-assign]
        return ctx

    mock_stream = MagicMock()

    async def _aiter():
        yield event

    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    class StreamMgr:
        async def __aenter__(self):
            return _Stream()

        async def __aexit__(self, *args):
            return None

    class _Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_done"):
                self._done = True
                return event
            raise StopAsyncIteration

    mock_client.messages.stream = MagicMock(return_value=StreamMgr())

    upstream = AnthropicUpstream("sk-test")
    payload = {
        "model": "claude-3-5-haiku-20241022",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
    }
    lines = []
    async for line in upstream.messages_stream(payload):
        lines.append(line)
    assert lines
    assert lines[0].startswith("event: content_block_delta")
    assert "data: " in lines[0]
