"""Raw HTTP wire helpers for OpenAI-compatible chat completions.

The official SDK parses responses into typed models that drop vendor extension
fields (e.g. ``delta.reasoning_content``). These helpers POST JSON and return
raw bodies so extension fields survive end-to-end.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
from openai import AsyncOpenAI
from openai._models import FinalRequestOptions

from netllm_sdk_openai.errors import OpenAIUpstreamError

__all__ = [
    "build_chat_wire_body",
    "iter_chat_completion_sse_lines",
    "read_chat_completion_json",
]


_EXTENSION_KEYS = frozenset({"reasoning_content"})


def _extract_extensions(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Side-channel vendor fields the OpenAI SDK types do not model."""
    saved: dict[str, dict[str, Any]] = {}
    for idx, choice in enumerate(raw.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        for container in ("message", "delta"):
            block = choice.get(container)
            if not isinstance(block, dict):
                continue
            ext = {k: block[k] for k in _EXTENSION_KEYS if k in block}
            if ext:
                saved.setdefault(str(idx), {})[container] = ext
    return saved


def _apply_extensions(
    body: dict[str, Any], extensions: dict[str, dict[str, Any]]
) -> None:
    choices = body.get("choices") or []
    for idx, ext in extensions.items():
        i = int(idx)
        if i >= len(choices) or not isinstance(choices[i], dict):
            continue
        for container, fields in ext.items():
            target = choices[i].get(container)
            if not isinstance(target, dict):
                target = {}
                choices[i][container] = target
            target.update(fields)


def _strip_extensions(
    raw: dict[str, Any], extensions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    stripped = json.loads(json.dumps(raw))
    for idx, ext in extensions.items():
        i = int(idx)
        choices = stripped.get("choices") or []
        if i >= len(choices) or not isinstance(choices[i], dict):
            continue
        for container, fields in ext.items():
            block = choices[i].get(container)
            if isinstance(block, dict):
                for key in fields:
                    block.pop(key, None)
    return stripped


def enrich_openai_chat_completion(raw: dict[str, Any]) -> dict[str, Any]:
    """SDK ``model_dump`` shape plus preserved vendor extension fields."""
    from openai.types.chat import ChatCompletion

    return _enrich_openai_chat_body(raw, ChatCompletion)


def enrich_openai_chat_chunk(raw: dict[str, Any]) -> dict[str, Any]:
    """Streaming chunk twin of :func:`enrich_openai_chat_completion`."""
    from openai.types.chat import ChatCompletionChunk

    return _enrich_openai_chat_body(raw, ChatCompletionChunk)


def _enrich_openai_chat_body(raw: dict[str, Any], model_class: Any) -> dict[str, Any]:
    from pydantic import ValidationError

    extensions = _extract_extensions(raw)
    stripped = _strip_extensions(raw, extensions)
    try:
        enriched = model_class.model_validate(stripped).model_dump()
    except ValidationError:
        return raw
    _apply_extensions(enriched, extensions)
    return enriched


def build_chat_wire_body(adapted: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``extra_body`` into the top-level POST JSON (vLLM/oMLX flat fields)."""
    body: dict[str, Any] = dict(adapted)
    extra = body.pop("extra_body", None)
    if not isinstance(extra, dict):
        return body
    for key, value in extra.items():
        if key not in body:
            body[key] = value
        elif key == "chat_template_kwargs" and isinstance(value, dict):
            existing = body.get(key)
            if isinstance(existing, dict):
                body[key] = {**value, **existing}
    return body


def _chat_post_options(body: dict[str, Any]) -> FinalRequestOptions:
    return FinalRequestOptions.construct(
        method="POST",
        url="/chat/completions",
        json_data=body,
    )


def _format_status_error(status_code: int, body: bytes) -> str:
    """Match OpenAI SDK ``_make_status_error_from_response`` message shape."""
    err_text = body.decode("utf-8", errors="replace").strip()
    if not err_text:
        return f"Error code: {status_code}"
    try:
        parsed = json.loads(err_text)
        return f"Error code: {status_code} - {parsed}"
    except json.JSONDecodeError:
        return err_text


def _raise_for_status(status_code: int, body: bytes) -> None:
    raise OpenAIUpstreamError(
        _format_status_error(status_code, body), status_code=status_code
    )


def _wrap_transport(exc: Exception) -> OpenAIUpstreamError:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.content
        return OpenAIUpstreamError(
            _format_status_error(exc.response.status_code, body),
            status_code=exc.response.status_code,
        )
    if isinstance(exc, OpenAIUpstreamError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return OpenAIUpstreamError("Request timed out.", status_code=None)
    if isinstance(exc, httpx.ConnectError):
        return OpenAIUpstreamError("Connection error.", status_code=None)
    status = getattr(exc, "status_code", None)
    return OpenAIUpstreamError(str(exc), status_code=status)


async def read_chat_completion_json(
    openai_client: AsyncOpenAI,
    adapted: Mapping[str, Any],
) -> dict[str, Any]:
    """Non-streaming chat completion — raw JSON, no SDK parsing."""
    wire_body = build_chat_wire_body(adapted)
    request = openai_client._build_request(
        _chat_post_options(wire_body), retries_taken=0
    )
    try:
        response = await openai_client._client.send(request)
        if response.status_code >= 400:
            _raise_for_status(response.status_code, response.content)
        return enrich_openai_chat_completion(json.loads(response.content))
    except Exception as exc:
        raise _wrap_transport(exc) from exc


async def iter_chat_completion_sse_lines(
    openai_client: AsyncOpenAI,
    adapted: Mapping[str, Any],
) -> AsyncIterator[str]:
    """Streaming chat completion — preserve upstream SSE JSON verbatim."""
    wire_body = build_chat_wire_body(adapted)
    wire_body["stream"] = True
    request = openai_client._build_request(
        _chat_post_options(wire_body), retries_taken=0
    )
    try:
        response = await openai_client._client.send(request, stream=True)
        if response.status_code >= 400:
            _raise_for_status(response.status_code, await response.aread())
        saw_done = False
        async for line in response.aiter_lines():
            if not line or line.startswith(":"):
                continue
            stripped = line.strip()
            if stripped in ("data: [DONE]", "[DONE]"):
                saw_done = True
                yield "data: [DONE]\n\n"
                continue
            if stripped.startswith("data:"):
                payload = stripped[5:].strip()
            else:
                payload = stripped
            if payload != "[DONE]":
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict) and parsed.get("object") == (
                    "chat.completion.chunk"
                ):
                    payload = json.dumps(enrich_openai_chat_chunk(parsed))
            yield f"data: {payload}\n\n"
        if not saw_done:
            yield "data: [DONE]\n\n"
    except Exception as exc:
        raise _wrap_transport(exc) from exc
