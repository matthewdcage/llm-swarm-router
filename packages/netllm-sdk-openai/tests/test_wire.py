"""Tests for raw HTTP wire helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from netllm_sdk_openai.wire import (
    build_chat_wire_body,
    iter_chat_completion_sse_lines,
    read_chat_completion_json,
)


def test_build_chat_wire_body_merges_extra_body() -> None:
    adapted = {
        "model": "qwen3",
        "messages": [],
        "extra_body": {"top_k": 40, "chat_template_kwargs": {"enable_thinking": True}},
    }
    body = build_chat_wire_body(adapted)
    assert body["top_k"] == 40
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert "extra_body" not in body


@pytest.mark.asyncio
async def test_read_chat_completion_json_preserves_reasoning_content() -> None:
    mock_client = MagicMock()
    mock_client._build_request = MagicMock(
        side_effect=lambda opts, retries_taken: httpx.Request(
            "POST",
            "http://127.0.0.1:11434/v1/chat/completions",
            json=opts.json_data,
        )
    )
    response = httpx.Response(
        200,
        content=json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "hi",
                            "reasoning_content": "think first",
                        }
                    }
                ]
            }
        ).encode(),
        request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"),
    )
    mock_httpx = AsyncMock()
    mock_httpx.send = AsyncMock(return_value=response)
    mock_client._client = mock_httpx

    result = await read_chat_completion_json(
        mock_client,
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert result["choices"][0]["message"]["reasoning_content"] == "think first"
    assert result["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
async def test_iter_sse_preserves_reasoning_content_delta() -> None:
    chunk = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "choices": [{"delta": {"reasoning_content": "thought"}}],
    }
    raw_lines = [f"data: {json.dumps(chunk)}", "data: [DONE]"]

    class _StreamCtx:
        def __init__(self, response: httpx.Response) -> None:
            self._response = response

        async def __aenter__(self) -> httpx.Response:
            return self._response

        async def __aexit__(self, *args: object) -> None:
            return None

    async def _aiter_lines():
        for line in raw_lines:
            yield line

    response = MagicMock()
    response.status_code = 200
    response.aiter_lines = _aiter_lines
    response.aread = AsyncMock(return_value=b"")

    mock_httpx = AsyncMock()
    mock_httpx.send = AsyncMock(return_value=response)

    mock_client = MagicMock()
    mock_client._build_request = MagicMock(
        side_effect=lambda opts, retries_taken: httpx.Request(
            "POST",
            "http://127.0.0.1:11434/v1/chat/completions",
            json=opts.json_data,
        )
    )
    mock_client._client = mock_httpx

    lines: list[str] = []
    async for line in iter_chat_completion_sse_lines(
        mock_client,
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    ):
        lines.append(line)

    payload = json.loads(lines[0].removeprefix("data: ").strip())
    assert payload["choices"][0]["delta"]["reasoning_content"] == "thought"
    assert any("[DONE]" in line for line in lines)


@pytest.mark.asyncio
async def test_read_chat_completion_json_status_error_matches_sdk_shape() -> None:
    mock_client = MagicMock()
    mock_client._build_request = MagicMock(
        side_effect=lambda opts, retries_taken: httpx.Request(
            "POST",
            "http://127.0.0.1:11434/v1/chat/completions",
            json=opts.json_data,
        )
    )
    err_body = {
        "error": {
            "message": "farm scripted HTTP 429",
            "type": "farm_error",
            "param": None,
            "code": None,
        }
    }
    response = httpx.Response(
        429,
        content=json.dumps(err_body).encode(),
        request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"),
    )
    mock_httpx = AsyncMock()
    mock_httpx.send = AsyncMock(return_value=response)
    mock_client._client = mock_httpx

    with pytest.raises(Exception) as exc_info:
        await read_chat_completion_json(mock_client, {"model": "m", "messages": []})
    assert str(exc_info.value) == f"Error code: 429 - {err_body}"


@pytest.mark.asyncio
async def test_iter_sse_connect_error_matches_sdk_shape() -> None:
    mock_client = MagicMock()
    mock_client._build_request = MagicMock(
        side_effect=lambda opts, retries_taken: httpx.Request(
            "POST",
            "http://127.0.0.1:11434/v1/chat/completions",
            json=opts.json_data,
        )
    )
    request = httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions")
    mock_httpx = AsyncMock()
    mock_httpx.send = AsyncMock(
        side_effect=httpx.ConnectError(
            "farm: no backend at api.openai.com", request=request
        )
    )
    mock_client._client = mock_httpx

    with pytest.raises(Exception) as exc_info:
        async for _ in iter_chat_completion_sse_lines(
            mock_client,
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        ):
            pass
    assert str(exc_info.value) == "Connection error."
