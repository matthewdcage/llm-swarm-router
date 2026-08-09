"""Mock helpers for the OpenAI wire chat path in integration tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

_FARM_CREATED = 1700000000


def chat_completion_body(
    content: str,
    *,
    model: str = "m",
    id: str = "cmpl-test",
) -> dict[str, Any]:
    return {
        "id": id,
        "object": "chat.completion",
        "created": _FARM_CREATED,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def wire_mock_chat_json(mock_client: MagicMock, body: dict[str, Any]) -> AsyncMock:
    """Attach wire-path mocks so ``OpenAIUpstream.chat_completion`` succeeds."""
    response = httpx.Response(
        200,
        content=json.dumps(body).encode(),
        request=httpx.Request("POST", "http://127.0.0.1:8080/v1/chat/completions"),
    )
    send = AsyncMock(return_value=response)
    mock_client._build_request = MagicMock(
        side_effect=lambda opts, retries_taken: httpx.Request(
            "POST",
            "http://127.0.0.1:8080/v1/chat/completions",
            json=opts.json_data,
        )
    )
    mock_client._client = MagicMock()
    mock_client._client.send = send
    return send


def wire_mock_chat_json_from_request(mock_client: MagicMock) -> AsyncMock:
    """Wire mock that echoes the requested model into the completion body."""

    async def _send(request: httpx.Request, stream: bool = False) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        body = chat_completion_body(
            "ok",
            model=str(payload.get("model", "m")),
        )
        return httpx.Response(
            200,
            content=json.dumps(body).encode(),
            request=request,
        )

    send = AsyncMock(side_effect=_send)
    mock_client._build_request = MagicMock(
        side_effect=lambda opts, retries_taken: httpx.Request(
            "POST",
            "http://127.0.0.1:8080/v1/chat/completions",
            json=opts.json_data,
        )
    )
    mock_client._client = MagicMock()
    mock_client._client.send = send
    return send
