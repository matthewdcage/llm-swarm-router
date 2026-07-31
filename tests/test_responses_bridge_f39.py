"""F-39 regression: Responses streaming bridge lifecycle-event completeness.

The audit (docs/architecture/09-follow-up-audit-2026-07-31.md, F-39) found
translate_chat_stream_to_responses never closed output items or content
parts: no response.content_part.added before text deltas, no
response.content_part.done after response.output_text.done, no
response.output_item.done for either the message or function_call items,
and response.completed carried whatever the partial bookkeeping produced.
This test replays a synthetic Chat Completions SSE transcript (text plus a
function call, the Codex tool-loop shape) and asserts the exact ordered
event-type sequence per the OpenAI Responses SSE spec, plus the fully
populated final output array and monotonic sequence numbers.
"""

from __future__ import annotations

import json

import pytest
from netllm_core.openai_responses_bridge import translate_chat_stream_to_responses


def _chat_chunk(delta: dict, finish_reason: str | None = None) -> str:
    choice: dict = {"delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return f"data: {json.dumps({'choices': [choice]})}\n\n"


async def _achunks(*raws: str):
    for raw in raws:
        yield raw


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


@pytest.mark.asyncio
async def test_f39_stream_emits_complete_lifecycle_event_sequence() -> None:
    """F-39: exact event ordering for a text + tool-call stream, including
    content_part bracketing and output_item.done for every opened item."""
    chunks = _achunks(
        _chat_chunk({"content": "Hel"}),
        _chat_chunk({"content": "lo"}),
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

    assert types == [
        "response.created",
        "response.output_item.added",  # message item opens
        "response.content_part.added",  # F-39: part opens before text deltas
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_item.added",  # function_call item opens
        "response.function_call_arguments.delta",
        "response.function_call_arguments.delta",
        "response.output_text.done",
        "response.content_part.done",  # F-39: part closes after text done
        "response.output_item.done",  # F-39: message item closes
        "response.function_call_arguments.done",
        "response.output_item.done",  # F-39: function_call item closes
        "response.completed",
    ]

    datas = [_event_data(e) for e in events]

    # sequence_number stays strictly monotonic across every event.
    seqs = [d["sequence_number"] for d in datas]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    by_type: dict[str, list[dict]] = {}
    for t, d in zip(types, datas):
        by_type.setdefault(t, []).append(d)

    part_added = by_type["response.content_part.added"][0]
    assert part_added["output_index"] == 0
    assert part_added["content_index"] == 0
    assert part_added["part"]["type"] == "output_text"
    assert part_added["part"]["text"] == ""

    part_done = by_type["response.content_part.done"][0]
    assert part_done["part"] == {
        "type": "output_text",
        "text": "Hello",
        "annotations": [],
    }
    assert part_done["item_id"] == part_added["item_id"]

    msg_done, fc_done = by_type["response.output_item.done"]
    assert msg_done["item"]["type"] == "message"
    assert msg_done["item"]["status"] == "completed"
    assert msg_done["item"]["content"] == [
        {"type": "output_text", "text": "Hello", "annotations": []}
    ]
    assert fc_done["item"]["type"] == "function_call"
    assert fc_done["item"]["status"] == "completed"
    assert fc_done["item"]["call_id"] == "call_1"
    assert fc_done["item"]["name"] == "shell"
    assert fc_done["item"]["arguments"] == '{"cmd":"ls"}'

    # response.completed carries the fully-populated output array, matching
    # the items exactly as closed by their output_item.done events.
    completed = by_type["response.completed"][0]
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["output"] == [
        msg_done["item"],
        fc_done["item"],
    ]
