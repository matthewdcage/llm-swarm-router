"""Codex wiring: Responses API <-> Chat Completions translation.

Codex CLI requires wire_api = "responses" for every custom provider (Chat
Completions support was fully removed from Codex in Feb 2026); netllm's
internal pipeline only speaks Chat Completions. See
netllm_core.openai_responses_bridge and docs/cli-source-routing-plan.md.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig
from netllm_core.openai_responses_bridge import (
    chat_to_responses_response,
    responses_to_chat_request,
    translate_chat_stream_to_responses,
)


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


# --- Request translation ---


def test_simple_string_input_becomes_user_message() -> None:
    result = responses_to_chat_request({"model": "gpt-5.6", "input": "hello"})
    assert result["model"] == "gpt-5.6"
    assert result["messages"] == [{"role": "user", "content": "hello"}]


def test_instructions_become_system_message() -> None:
    result = responses_to_chat_request(
        {"model": "m", "instructions": "Talk like a pirate.", "input": "hi"}
    )
    assert result["messages"][0] == {
        "role": "system",
        "content": "Talk like a pirate.",
    }
    assert result["messages"][1] == {"role": "user", "content": "hi"}


def test_message_array_input_preserves_roles() -> None:
    result = responses_to_chat_request(
        {
            "model": "m",
            "input": [
                {"role": "developer", "content": "be terse"},
                {"role": "user", "content": "hi"},
            ],
        }
    )
    assert result["messages"] == [
        {"role": "developer", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]


def test_content_block_array_flattens_to_text() -> None:
    result = responses_to_chat_request(
        {
            "model": "m",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
        }
    )
    assert result["messages"] == [{"role": "user", "content": "hello"}]


def test_function_call_output_becomes_tool_message() -> None:
    result = responses_to_chat_request(
        {
            "model": "m",
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "42",
                }
            ],
        }
    )
    assert result["messages"] == [
        {"role": "tool", "tool_call_id": "call_1", "content": "42"}
    ]


def test_prior_function_call_replayed_as_assistant_tool_call() -> None:
    """Codex resends its own history each turn, including prior
    function_call items, rather than relying on previous_response_id."""
    result = responses_to_chat_request(
        {
            "model": "m",
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "shell",
                    "arguments": '{"cmd":"ls"}',
                }
            ],
        }
    )
    assert result["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": '{"cmd":"ls"}'},
                }
            ],
        }
    ]


def test_flat_function_tool_gets_chat_completions_wrapper() -> None:
    result = responses_to_chat_request(
        {
            "model": "m",
            "input": "hi",
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
    )
    assert result["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_max_output_tokens_maps_to_max_tokens() -> None:
    result = responses_to_chat_request(
        {"model": "m", "input": "hi", "max_output_tokens": 256}
    )
    assert result["max_tokens"] == 256


def test_reasoning_effort_passthrough() -> None:
    result = responses_to_chat_request(
        {"model": "m", "input": "hi", "reasoning": {"effort": "high"}}
    )
    assert result["reasoning_effort"] == "high"


# --- Response translation ---


def test_text_response_becomes_message_output_item() -> None:
    chat_response = {
        "model": "m",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello there"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    result = chat_to_responses_response(chat_response, model="m")
    assert result["object"] == "response"
    assert result["status"] == "completed"
    assert result["output"] == [
        {
            "id": result["output"][0]["id"],
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "hello there", "annotations": []}
            ],
        }
    ]
    assert result["usage"] == {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 8,
    }


def test_tool_call_response_becomes_function_call_output_item() -> None:
    chat_response = {
        "model": "m",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "shell",
                                "arguments": '{"cmd":"ls"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {},
    }
    result = chat_to_responses_response(chat_response, model="m")
    assert result["output"] == [
        {
            "id": result["output"][0]["id"],
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "shell",
            "arguments": '{"cmd":"ls"}',
        }
    ]


def test_length_finish_reason_marks_incomplete() -> None:
    chat_response = {
        "model": "m",
        "choices": [
            {
                "message": {"role": "assistant", "content": "cut off"},
                "finish_reason": "length",
            }
        ],
        "usage": {},
    }
    result = chat_to_responses_response(chat_response, model="m")
    assert result["status"] == "incomplete"


# --- Streaming translation ---


async def _achunks(*raws: str):
    for raw in raws:
        yield raw


@pytest.mark.asyncio
async def test_stream_text_deltas_translate_to_responses_events() -> None:
    chunks = _achunks(
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    )
    events = [e async for e in translate_chat_stream_to_responses(chunks, model="m")]
    types = [_event_type(e) for e in events]
    assert types[0] == "response.created"
    assert "response.output_item.added" in types
    assert types.count("response.output_text.delta") == 2
    assert types[-1] == "response.completed"

    completed = _event_data(events[-1])
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["output"][0]["content"][0]["text"] == "Hello"


def _chat_chunk(delta: dict, finish_reason: str | None = None) -> str:
    choice: dict = {"delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return f"data: {json.dumps({'choices': [choice]})}\n\n"


@pytest.mark.asyncio
async def test_stream_function_call_translates_to_responses_events() -> None:
    chunks = _achunks(
        _chat_chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "shell", "arguments": ""},
                    }
                ]
            }
        ),
        _chat_chunk(
            {"tool_calls": [{"index": 0, "function": {"arguments": '{"cmd":'}}]}
        ),
        _chat_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"ls"}'}}]}),
        _chat_chunk({}, finish_reason="tool_calls"),
        "data: [DONE]\n\n",
    )
    events = [e async for e in translate_chat_stream_to_responses(chunks, model="m")]
    types = [_event_type(e) for e in events]
    assert "response.function_call_arguments.delta" in types
    assert types[-1] == "response.completed"

    completed = _event_data(events[-1])
    output_item = completed["response"]["output"][0]
    assert output_item["type"] == "function_call"
    assert output_item["call_id"] == "call_1"
    assert output_item["name"] == "shell"
    assert output_item["arguments"] == '{"cmd":"ls"}'


def _event_type(sse_event: str) -> str:
    for line in sse_event.splitlines():
        if line.startswith("event: "):
            return line[len("event: ") :]
    raise AssertionError(f"no event line in {sse_event!r}")


def _event_data(sse_event: str) -> dict:
    for line in sse_event.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise AssertionError(f"no data line in {sse_event!r}")


# --- End-to-end route: translation composes with the existing chat pipeline ---


@patch(
    "netllm_agent.service.AgentService.proxy_chat_completion",
    new_callable=AsyncMock,
)
def test_responses_route_translates_request_and_response(
    mock_proxy: AsyncMock, client: TestClient
) -> None:
    mock_proxy.return_value = {
        "model": "test-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hi there"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    resp = client.post(
        "/v1/responses",
        json={
            "model": "test-model",
            "instructions": "be terse",
            "input": "hello",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "response"
    assert body["output"][0]["content"][0]["text"] == "hi there"

    # The chat pipeline (source identity, routing, scenarios, capacity)
    # sees a normal Chat Completions payload -- translation is edge-only.
    sent_payload = mock_proxy.call_args.args[0]
    assert sent_payload["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]
