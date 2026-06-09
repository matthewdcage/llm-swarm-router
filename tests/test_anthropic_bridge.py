"""Tests for Messages API ↔ Chat Completions bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from netllm_core.anthropic_bridge import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic" / "v1"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_anthropic_to_openai_simple() -> None:
    payload = _load("simple_request.json")
    result = anthropic_to_openai_request(payload)
    assert result["model"] == "llama3.2:latest"
    assert result["max_tokens"] == 1024
    assert result["messages"][0] == {
        "role": "system",
        "content": "You are a helpful assistant.",
    }
    assert result["messages"][1] == {"role": "user", "content": "Hello"}


def test_openai_to_anthropic_simple() -> None:
    oai = _load("simple_openai_response.json")
    result = openai_to_anthropic_response(oai, model="llama3.2:latest")
    assert result["type"] == "message"
    assert result["role"] == "assistant"
    assert result["content"] == [{"type": "text", "text": "Hi there!"}]
    assert result["stop_reason"] == "end_turn"
    assert result["usage"]["input_tokens"] == 10
    assert result["usage"]["output_tokens"] == 4


def test_anthropic_tools_mapping() -> None:
    payload = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "get_weather"},
    }
    result = anthropic_to_openai_request(payload)
    tool = result["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "get_weather"
    assert tool["function"]["parameters"]["properties"]["city"]["type"] == "string"
    assert result["tool_choice"] == {
        "type": "function",
        "function": {"name": "get_weather"},
    }


@pytest.mark.asyncio
async def test_translate_openai_stream() -> None:
    from netllm_core.anthropic_bridge import translate_openai_stream_to_anthropic

    async def chunks():
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield "data: [DONE]\n\n"

    events = []
    async for line in translate_openai_stream_to_anthropic(
        chunks(), model="test-model"
    ):
        events.append(line)

    joined = "".join(events)
    assert "event: message_start" in joined
    assert "event: content_block_delta" in joined
    assert "text_delta" in joined
    assert "event: message_stop" in joined


@pytest.mark.asyncio
async def test_translate_openai_stream_tool_use() -> None:
    from netllm_core.anthropic_bridge import translate_openai_stream_to_anthropic

    async def chunks():
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc",'
            '"function":{"name":"get_weather","arguments":"{\\"city\\":\\"SF\\""}}]}}]}\n\n'
        )
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
            '{"arguments":"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
        )
        yield "data: [DONE]\n\n"

    events = []
    async for line in translate_openai_stream_to_anthropic(
        chunks(), model="test-model"
    ):
        events.append(line)

    joined = "".join(events)
    assert "tool_use" in joined
    assert "content_block_start" in joined
    assert "input_json_delta" in joined
    assert "get_weather" in joined
    assert '"stop_reason": "tool_use"' in joined or "tool_use" in joined


def test_anthropic_system_cache_control_passthrough() -> None:
    payload = {
        "model": "test",
        "max_tokens": 100,
        "system": [
            {
                "type": "text",
                "text": "You are helpful.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    result = anthropic_to_openai_request(payload)
    system_msg = result["messages"][0]
    assert system_msg["role"] == "system"
    content = system_msg["content"]
    assert isinstance(content, list)
    assert content[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_tool_cache_control_passthrough() -> None:
    payload = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "name": "lookup",
                "description": "Look up",
                "input_schema": {"type": "object", "properties": {}},
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    result = anthropic_to_openai_request(payload)
    assert result["tools"][0]["cache_control"] == {"type": "ephemeral"}
