"""Contract tests for OpenAIUpstream adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from netllm_sdk_openai.client import OpenAIUpstream, OpenAIUpstreamError


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.read_chat_completion_json", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_passes_payload(
    mock_cls: MagicMock, mock_read: AsyncMock
) -> None:
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_read.return_value = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
    }

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    payload = {
        "model": "llama3",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 10,
    }
    result = await upstream.chat_completion(payload)
    assert result == {"id": "chatcmpl-1", "object": "chat.completion"}
    mock_read.assert_awaited_once()
    passed = mock_read.await_args.args[1]
    assert passed["model"] == "llama3"
    assert passed["messages"] == [{"role": "user", "content": "hi"}]
    assert passed["max_tokens"] == 10


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.read_chat_completion_json", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_wraps_errors(
    mock_cls: MagicMock, mock_read: AsyncMock
) -> None:
    mock_cls.return_value = MagicMock()
    mock_read.side_effect = OpenAIUpstreamError("rate limited", status_code=429)

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    with pytest.raises(OpenAIUpstreamError) as exc_info:
        await upstream.chat_completion({"model": "x", "messages": []})
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.iter_chat_completion_sse_lines")
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_stream_sse_format(
    mock_cls: MagicMock, mock_iter: MagicMock
) -> None:
    mock_cls.return_value = MagicMock()

    async def _lines():
        yield (
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": "hi"}}],
                }
            )
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    mock_iter.return_value = _lines()

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    payload = {
        "model": "llama3",
        "messages": [{"role": "user", "content": "hi"}],
    }
    lines = []
    async for line in upstream.chat_completion_stream(payload):
        lines.append(line)

    assert len(lines) == 2
    assert lines[0].startswith("data: ")
    assert json.loads(lines[0].removeprefix("data: ").strip())["object"] == (
        "chat.completion.chunk"
    )
    assert lines[1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.read_chat_completion_json", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_preserves_reasoning_content(
    mock_cls: MagicMock, mock_read: AsyncMock
) -> None:
    mock_cls.return_value = MagicMock()
    mock_read.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": "chain of thought",
                }
            }
        ]
    }

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    result = await upstream.chat_completion(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert result["choices"][0]["message"]["reasoning_content"] == "chain of thought"


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.iter_chat_completion_sse_lines")
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_stream_preserves_reasoning_content(
    mock_cls: MagicMock, mock_iter: MagicMock
) -> None:
    mock_cls.return_value = MagicMock()

    async def _lines():
        yield (
            "data: "
            + json.dumps(
                {
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"reasoning_content": "thinking"}}],
                }
            )
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    mock_iter.return_value = _lines()

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    lines = []
    async for line in upstream.chat_completion_stream(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    ):
        lines.append(line)

    payload = json.loads(lines[0].removeprefix("data: ").strip())
    assert payload["choices"][0]["delta"]["reasoning_content"] == "thinking"


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.read_chat_completion_json", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_moves_top_k_to_extra_body(
    mock_cls: MagicMock, mock_read: AsyncMock
) -> None:
    mock_cls.return_value = MagicMock()
    mock_read.return_value = {"id": "chatcmpl-1", "object": "chat.completion"}

    upstream = OpenAIUpstream("http://127.0.0.1:8012/v1")
    payload = {
        "model": "qwen3-next-80b",
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 40,
    }
    await upstream.chat_completion(payload)
    passed = mock_read.await_args.args[1]
    assert "top_k" not in passed
    assert passed["extra_body"] == {"top_k": 40}


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_embeddings_passes_payload(mock_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.model_dump.return_value = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
    }
    mock_client.embeddings.create = AsyncMock(return_value=mock_resp)

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    payload = {"model": "nomic-embed-text", "input": "hello"}
    result = await upstream.embeddings(payload)
    assert result["object"] == "list"
    mock_client.embeddings.create.assert_awaited_once_with(**payload)


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_embeddings_moves_extension_fields_to_extra_body(
    mock_cls: MagicMock,
) -> None:
    """F-35: client.embeddings() applies the extension-field adaptation."""
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.model_dump.return_value = {"object": "list", "data": []}
    mock_client.embeddings.create = AsyncMock(return_value=mock_resp)

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    payload = {"model": "nomic-embed-text", "input": "hi", "truncate": True}
    await upstream.embeddings(payload)
    call_kwargs = mock_client.embeddings.create.await_args.kwargs
    assert "truncate" not in call_kwargs
    assert call_kwargs["extra_body"] == {"truncate": True}


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.read_chat_completion_json", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_chat_completion_strips_sdk_control_kwargs(
    mock_cls: MagicMock, mock_read: AsyncMock
) -> None:
    """F-42: wire extra_headers/extra_query/timeout never become SDK kwargs."""
    mock_cls.return_value = MagicMock()
    mock_read.return_value = {"id": "c1", "object": "chat.completion"}

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    payload = {
        "model": "llama3",
        "messages": [{"role": "user", "content": "hi"}],
        "extra_headers": {"Authorization": "Bearer stolen"},
        "extra_query": {"debug": "1"},
        "timeout": 0.001,
    }
    await upstream.chat_completion(payload)
    passed = mock_read.await_args.args[1]
    assert "extra_headers" not in passed
    assert "extra_query" not in passed
    assert "timeout" not in passed


@pytest.mark.asyncio
@patch("netllm_sdk_openai.client.AsyncOpenAI")
async def test_embeddings_wraps_errors(mock_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    err = Exception("no such model")
    err.status_code = 404  # type: ignore[attr-defined]
    mock_client.embeddings.create = AsyncMock(side_effect=err)

    upstream = OpenAIUpstream("http://127.0.0.1:11434/v1")
    with pytest.raises(OpenAIUpstreamError) as exc_info:
        await upstream.embeddings({"model": "x", "input": "y"})
    assert exc_info.value.status_code == 404
