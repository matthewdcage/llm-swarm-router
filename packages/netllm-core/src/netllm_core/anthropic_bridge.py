"""Messages API ↔ Chat Completions translation (no vendor SDK imports)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

_FINISH_TO_STOP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def anthropic_to_openai_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert Anthropic Messages request to OpenAI Chat Completions request."""
    result: dict[str, Any] = {}
    passthrough_keys = (
        "model",
        "max_tokens",
        "temperature",
        "top_p",
        "stream",
        "stop",
    )
    for key in passthrough_keys:
        if key in payload:
            result[key] = payload[key]
    if "stop_sequences" in payload:
        result["stop"] = payload["stop_sequences"]

    messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if system is not None:
        messages.extend(_anthropic_system_to_openai(system))

    for msg in payload.get("messages") or []:
        messages.extend(_anthropic_message_to_openai(msg))

    result["messages"] = messages

    if "tools" in payload:
        result["tools"] = [_anthropic_tool_to_openai(t) for t in payload["tools"]]
    if "tool_choice" in payload:
        result["tool_choice"] = _anthropic_tool_choice_to_openai(payload["tool_choice"])

    return result


def openai_to_anthropic_response(
    payload: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    """Convert OpenAI Chat Completions response to Anthropic Messages response."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason") or "stop"
    usage = payload.get("usage") or {}

    content: list[dict[str, Any]] = []
    text = message.get("content")
    if text:
        content.append({"type": "text", "text": text})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {"raw": args_raw}
        content.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name", ""),
                "input": args if isinstance(args, dict) else {},
            }
        )
    if not content:
        content.append({"type": "text", "text": ""})

    return {
        "id": payload.get("id") or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": payload.get("model") or model,
        "content": content,
        "stop_reason": _FINISH_TO_STOP.get(finish, "end_turn"),
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def openai_error_to_anthropic(message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": "api_error", "message": message},
    }


async def translate_openai_stream_to_anthropic(
    chunks: AsyncIterator[str],
    *,
    model: str,
) -> AsyncIterator[str]:
    """Wrap OpenAI SSE chunks as Anthropic stream events (text + tool_use)."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    started = False
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0
    text_buffer = ""

    # block_index -> {"type": "text"|"tool_use", "id", "name", "args"}
    blocks: dict[int, dict[str, Any]] = {}
    open_blocks: set[int] = set()
    next_block_index = 0
    text_block_index: int | None = None

    def start_text_block() -> list[str]:
        nonlocal text_block_index, next_block_index
        if text_block_index is not None:
            return []
        text_block_index = next_block_index
        next_block_index += 1
        open_blocks.add(text_block_index)
        blocks[text_block_index] = {"type": "text"}
        return [
            _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        ]

    def close_block(index: int) -> str | None:
        if index not in open_blocks:
            return None
        open_blocks.discard(index)
        return _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": index},
        )

    async for raw in chunks:
        if not raw.startswith("data: "):
            continue
        data_str = raw[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        if not started:
            started = True
            yield _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "model": chunk.get("model") or model,
                        "content": [],
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )

        usage = chunk.get("usage")
        if usage:
            input_tokens = usage.get("prompt_tokens", input_tokens)
            output_tokens = usage.get("completion_tokens", output_tokens)

        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}

        if delta.get("content"):
            for event in start_text_block():
                yield event
            assert text_block_index is not None
            text_buffer += delta["content"]
            yield _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": text_block_index,
                    "delta": {"type": "text_delta", "text": delta["content"]},
                },
            )

        for tool_delta in delta.get("tool_calls") or []:
            if not isinstance(tool_delta, dict):
                continue
            idx = tool_delta.get("index", 0)
            if idx not in blocks:
                if text_block_index is not None and text_block_index in open_blocks:
                    stop = close_block(text_block_index)
                    if stop:
                        yield stop
                tool_id = tool_delta.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"
                fn = tool_delta.get("function") or {}
                name = fn.get("name") or ""
                blocks[idx] = {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "args": "",
                    "block_index": next_block_index,
                }
                block_index = next_block_index
                next_block_index += 1
                blocks[idx]["block_index"] = block_index
                open_blocks.add(block_index)
                yield _sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": {},
                        },
                    },
                )
            state = blocks[idx]
            fn = tool_delta.get("function") or {}
            if fn.get("name") and not state.get("name"):
                state["name"] = fn["name"]
            arg_piece = fn.get("arguments") or ""
            if arg_piece:
                state["args"] = state.get("args", "") + arg_piece
                yield _sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": state["block_index"],
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": arg_piece,
                        },
                    },
                )

        finish = choice.get("finish_reason")
        if finish:
            stop_reason = _FINISH_TO_STOP.get(finish, "end_turn")

    if not started:
        yield _sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        started = True

    if text_block_index is None and not blocks:
        for event in start_text_block():
            yield event

    for index in sorted(open_blocks):
        stop = close_block(index)
        if stop:
            yield stop

    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens or max(1, len(text_buffer) // 4)},
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _anthropic_system_to_openai(system: Any) -> list[dict[str, Any]]:
    if isinstance(system, str):
        return [{"role": "system", "content": system}]
    if isinstance(system, list):
        blocks = [
            _openai_text_block(block)
            for block in system
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if blocks:
            if len(blocks) == 1 and "cache_control" not in blocks[0]:
                return [{"role": "system", "content": blocks[0].get("text", "")}]
            return [{"role": "system", "content": blocks}]
        return [{"role": "system", "content": _system_to_text(system)}]
    return [{"role": "system", "content": str(system)}]


def _openai_text_block(block: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "text", "text": block.get("text", "")}
    if "cache_control" in block:
        out["cache_control"] = block["cache_control"]
    return out


def _system_to_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(system)


def _anthropic_message_to_openai(msg: dict[str, Any]) -> list[dict[str, Any]]:
    role = msg.get("role", "user")
    content = msg.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return [{"role": role, "content": str(content)}]

    if role == "assistant":
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
        out: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(text_parts) or None,
        }
        if tool_calls:
            out["tool_calls"] = tool_calls
        return [out]

    if role == "user":
        results: list[dict[str, Any]] = []
        text_parts: list[str] = []
        image_parts: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "image":
                source = block.get("source") or {}
                if source.get("type") == "base64":
                    media = source.get("media_type", "image/jpeg")
                    data = source.get("data", "")
                    image_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media};base64,{data}"},
                        }
                    )
            elif btype == "tool_result":
                results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": _tool_result_content(block.get("content")),
                    }
                )
        if text_parts or image_parts:
            if image_parts:
                parts: list[dict[str, Any]] = []
                if text_parts:
                    parts.append({"type": "text", "text": "\n".join(text_parts)})
                parts.extend(image_parts)
                results.insert(0, {"role": "user", "content": parts})
            else:
                results.insert(
                    0,
                    {
                        "role": "user",
                        "content": "\n".join(text_parts),
                    },
                )
        return results or [{"role": "user", "content": ""}]

    return [{"role": role, "content": _blocks_to_text(content)}]


def _tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def _blocks_to_text(blocks: list[Any]) -> str:
    parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _anthropic_tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    fn: dict[str, Any] = {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
    }
    out: dict[str, Any] = {"type": "function", "function": fn}
    if "cache_control" in tool:
        out["cache_control"] = tool["cache_control"]
    return out


def _anthropic_tool_choice_to_openai(choice: Any) -> Any:
    if isinstance(choice, str):
        return choice
    if not isinstance(choice, dict):
        return "auto"
    ctype = choice.get("type")
    if ctype == "auto":
        return "auto"
    if ctype == "any":
        return "required"
    if ctype == "tool":
        name = choice.get("name", "")
        return {"type": "function", "function": {"name": name}}
    return "auto"
