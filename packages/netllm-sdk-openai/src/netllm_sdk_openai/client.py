"""Thin wrapper over the official OpenAI Python SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI, OpenAI

from netllm_sdk_openai.errors import OpenAIUpstreamError
from netllm_sdk_openai.payload import (
    adapt_chat_payload_for_sdk,
    adapt_embeddings_payload_for_sdk,
)
from netllm_sdk_openai.wire import (
    iter_chat_completion_sse_lines,
    read_chat_completion_json,
)


class OpenAIUpstream:
    """OpenAI-compatible upstream client using the official SDK."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "netllm-local",
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        timeout = httpx_timeout(connect_timeout, read_timeout)
        extra: dict[str, Any] = {}
        if default_headers:
            extra["default_headers"] = default_headers
        self._async = AsyncOpenAI(
            base_url=base_url.rstrip("/"),
            api_key=api_key or "netllm-local",
            timeout=timeout,
            **extra,
        )
        self._sync = OpenAI(
            base_url=base_url.rstrip("/"),
            api_key=api_key or "netllm-local",
            timeout=timeout,
            **extra,
        )
        self.base_url = base_url.rstrip("/")

    async def chat_completion(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if payload.get("stream"):
                raise OpenAIUpstreamError("Use chat_completion_stream for stream=True")
            sdk_payload = adapt_chat_payload_for_sdk(payload)
            return await read_chat_completion_json(self._async, sdk_payload)
        except Exception as exc:
            raise _wrap(exc) from exc

    async def chat_completion_stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        payload = {**payload, "stream": True}
        try:
            sdk_payload = adapt_chat_payload_for_sdk(payload)
            async for line in iter_chat_completion_sse_lines(self._async, sdk_payload):
                yield line
        except Exception as exc:
            raise _wrap(exc) from exc

    async def embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            sdk_payload = adapt_embeddings_payload_for_sdk(payload)
            resp = await self._async.embeddings.create(**sdk_payload)
            return resp.model_dump()
        except Exception as exc:
            raise _wrap(exc) from exc


def httpx_timeout(connect: float, read: float) -> Any:
    import httpx

    return httpx.Timeout(read, connect=connect)


def _wrap(exc: Exception) -> OpenAIUpstreamError:
    status = getattr(exc, "status_code", None)
    return OpenAIUpstreamError(str(exc), status_code=status)
