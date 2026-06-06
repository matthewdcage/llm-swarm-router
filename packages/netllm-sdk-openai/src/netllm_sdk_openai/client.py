"""Thin wrapper over the official OpenAI Python SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI, OpenAI


class OpenAIUpstreamError(Exception):
    """Normalized upstream failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenAIUpstream:
    """OpenAI-compatible upstream client using the official SDK."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "netllm-local",
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
    ) -> None:
        timeout = httpx_timeout(connect_timeout, read_timeout)
        self._async = AsyncOpenAI(
            base_url=base_url.rstrip("/"),
            api_key=api_key or "netllm-local",
            timeout=timeout,
        )
        self._sync = OpenAI(
            base_url=base_url.rstrip("/"),
            api_key=api_key or "netllm-local",
            timeout=timeout,
        )
        self.base_url = base_url.rstrip("/")

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            page = await self._async.models.list()
            return [{"id": m.id, "object": m.object} for m in page.data]
        except Exception as exc:
            raise _wrap(exc) from exc

    async def chat_completion(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if payload.get("stream"):
                raise OpenAIUpstreamError("Use chat_completion_stream for stream=True")
            resp = await self._async.chat.completions.create(**payload)
            return resp.model_dump()
        except Exception as exc:
            raise _wrap(exc) from exc

    async def chat_completion_stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        payload = {**payload, "stream": True}
        try:
            stream = await self._async.chat.completions.create(**payload)
            async for chunk in stream:
                yield f"data: {json.dumps(chunk.model_dump())}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            raise _wrap(exc) from exc

    def chat_completion_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._sync.chat.completions.create(**payload)
            return resp.model_dump()
        except Exception as exc:
            raise _wrap(exc) from exc


def httpx_timeout(connect: float, read: float) -> Any:
    import httpx

    return httpx.Timeout(read, connect=connect)


def _wrap(exc: Exception) -> OpenAIUpstreamError:
    status = getattr(exc, "status_code", None)
    return OpenAIUpstreamError(str(exc), status_code=status)
