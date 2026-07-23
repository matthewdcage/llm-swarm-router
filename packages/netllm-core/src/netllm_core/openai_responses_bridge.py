"""Responses API <-> Chat Completions translation (no vendor SDK imports).

Exists so Codex CLI can reach netllm: as of February 2026 Codex removed
support for `wire_api = "chat"` entirely -- every custom
`[model_providers.<id>]` in its config.toml, not just the built-in
"openai" one, must speak the Responses API
(`POST {base_url}/responses`). netllm's internal pipeline only ever
speaks Chat Completions; this module is the same kind of adapter as
anthropic_bridge.py, just for a different upstream-facing wire format.

Scope (docs/cli-source-routing-plan.md, Codex wiring): plain text and
function-calling turns, single-turn and multi-turn conversations replayed
via `input` (Codex resends its own history each call rather than relying on
`previous_response_id`/server-side state). Not implemented: encrypted
reasoning items, image/file input blocks, and the `store`/background-response
lifecycle -- Codex's default interactive usage doesn't need them, and adding
translation for parts with no test traffic to validate against risks being
silently wrong. See docs/cli-source-routing-plan.md Phase 3.5 for the
verification status of the streaming half of this module.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

_PASSTHROUGH_KEYS = ("model", "stream", "temperature", "top_p")


def responses_to_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert a Responses API request body to a Chat Completions one."""
    result: dict[str, Any] = {}
    for key in _PASSTHROUGH_KEYS:
        if key in payload:
            result[key] = payload[key]

    if "max_output_tokens" in payload:
        result["max_tokens"] = payload["max_output_tokens"]

    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        result["reasoning_effort"] = reasoning["effort"]

    messages: list[dict[str, Any]] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})

    messages.extend(_responses_input_to_messages(payload.get("input")))
    result["messages"] = messages

    if "tools" in payload:
        result["tools"] = [_responses_tool_to_chat(t) for t in payload["tools"] or []]
    if "tool_choice" in payload:
        result["tool_choice"] = _responses_tool_choice_to_chat(payload["tool_choice"])
    if "parallel_tool_calls" in payload:
        result["parallel_tool_calls"] = payload["parallel_tool_calls"]

    return result


def _responses_input_to_messages(raw_input: Any) -> list[dict[str, Any]]:
    if raw_input is None:
        return []
    if isinstance(raw_input, str):
        return [{"role": "user", "content": raw_input}]
    if not isinstance(raw_input, list):
        return []

    messages: list[dict[str, Any]] = []
    for item in raw_input:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call_output" or (
            item_type is None and "call_id" in item and "output" in item
        ):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": _stringify_tool_output(item.get("output")),
                }
            )
            continue
        if item_type == "function_call":
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item.get("call_id", ""),
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": item.get("arguments", "") or "{}",
                            },
                        }
                    ],
                }
            )
            continue
        # Plain message item: {"role": ..., "content": str | list[block]}.
        role = item.get("role", "user")
        messages.append({"role": role, "content": _responses_content_to_text(item)})
    return messages


def _stringify_tool_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    return json.dumps(output)


def _responses_content_to_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _responses_tool_to_chat(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function" or "function" in tool:
        # Already Chat-Completions-shaped, or a built-in tool type we
        # don't translate (e.g. a hosted web_search tool) -- pass through.
        return tool
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        },
    }


def _responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        if "function" in tool_choice:
            return tool_choice
        return {
            "type": "function",
            "function": {"name": tool_choice.get("name", "")},
        }
    return tool_choice


def chat_to_responses_response(
    payload: dict[str, Any], *, model: str
) -> dict[str, Any]:
    """Convert a Chat Completions response to a Responses API response."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output: list[dict[str, Any]] = []

    content = message.get("content")
    if isinstance(content, str) and content:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": content, "annotations": []}
                ],
            }
        )

    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        output.append(
            {
                "id": f"fc_{uuid.uuid4().hex[:24]}",
                "type": "function_call",
                "status": "completed",
                "call_id": tool_call.get("id", ""),
                "name": function.get("name", ""),
                "arguments": function.get("arguments", "") or "{}",
            }
        )

    usage = payload.get("usage") or {}
    finish_reason = choice.get("finish_reason")

    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": payload.get("model") or model,
        "status": "incomplete" if finish_reason == "length" else "completed",
        "output": output,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get(
                "total_tokens",
                usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
            ),
        },
    }


async def translate_chat_stream_to_responses(
    chunks: AsyncIterator[str],
    *,
    model: str,
) -> AsyncIterator[str]:
    """Wrap internal OpenAI Chat Completions SSE chunks as Responses API
    streaming events (text + a single function call at a time).

    Best-effort: covers the common single-text-item and single-function-
    call-item cases a Codex-style tool-calling loop produces. Not
    verified against a live Codex session -- see
    docs/cli-source-routing-plan.md Phase 3.5.
    """
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    sequence = 0

    def seq() -> int:
        nonlocal sequence
        sequence += 1
        return sequence

    def envelope(status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "status": status,
            "output": output,
        }

    started = False
    text_item_id: str | None = None
    text_buffer = ""
    # tool_call index -> {"call_id", "name", "arguments", "item_id"}
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None

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
                "response.created",
                {
                    "type": "response.created",
                    "sequence_number": seq(),
                    "response": envelope("in_progress", []),
                },
            )

        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}

        if delta.get("content"):
            if text_item_id is None:
                text_item_id = f"msg_{uuid.uuid4().hex[:24]}"
                yield _sse_event(
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "sequence_number": seq(),
                        "output_index": 0,
                        "item": {
                            "id": text_item_id,
                            "type": "message",
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    },
                )
            text_buffer += delta["content"]
            yield _sse_event(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "sequence_number": seq(),
                    "item_id": text_item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": delta["content"],
                },
            )

        for tool_delta in delta.get("tool_calls") or []:
            if not isinstance(tool_delta, dict):
                continue
            idx = tool_delta.get("index", 0)
            if idx not in tool_calls:
                fn = tool_delta.get("function") or {}
                item_id = f"fc_{uuid.uuid4().hex[:24]}"
                call_id = tool_delta.get("id") or f"call_{uuid.uuid4().hex[:16]}"
                tool_calls[idx] = {
                    "item_id": item_id,
                    "call_id": call_id,
                    "name": fn.get("name") or "",
                    "arguments": "",
                }
                yield _sse_event(
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "sequence_number": seq(),
                        "output_index": idx + 1,
                        "item": {
                            "id": item_id,
                            "type": "function_call",
                            "status": "in_progress",
                            "call_id": call_id,
                            "name": tool_calls[idx]["name"],
                            "arguments": "",
                        },
                    },
                )
            state = tool_calls[idx]
            fn = tool_delta.get("function") or {}
            if fn.get("name") and not state["name"]:
                state["name"] = fn["name"]
            arg_piece = fn.get("arguments") or ""
            if arg_piece:
                state["arguments"] += arg_piece
                yield _sse_event(
                    "response.function_call_arguments.delta",
                    {
                        "type": "response.function_call_arguments.delta",
                        "sequence_number": seq(),
                        "item_id": state["item_id"],
                        "output_index": idx + 1,
                        "delta": arg_piece,
                    },
                )

        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    output: list[dict[str, Any]] = []
    if text_item_id is not None:
        yield _sse_event(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "sequence_number": seq(),
                "item_id": text_item_id,
                "output_index": 0,
                "content_index": 0,
                "text": text_buffer,
            },
        )
        output.append(
            {
                "id": text_item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": text_buffer, "annotations": []}
                ],
            }
        )

    for idx in sorted(tool_calls):
        state = tool_calls[idx]
        yield _sse_event(
            "response.function_call_arguments.done",
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": seq(),
                "item_id": state["item_id"],
                "output_index": idx + 1,
                "arguments": state["arguments"],
            },
        )
        output.append(
            {
                "id": state["item_id"],
                "type": "function_call",
                "status": "completed",
                "call_id": state["call_id"],
                "name": state["name"],
                "arguments": state["arguments"] or "{}",
            }
        )

    status = "incomplete" if finish_reason == "length" else "completed"
    yield _sse_event(
        "response.completed",
        {
            "type": "response.completed",
            "sequence_number": seq(),
            "response": envelope(status, output),
        },
    )


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
