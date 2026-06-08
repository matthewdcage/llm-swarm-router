"""Thin wrapper over the official Anthropic Python SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic


class AnthropicUpstreamError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AnthropicUpstream:
    """Anthropic Messages API upstream."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
    ) -> None:
        import httpx

        timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        if default_headers:
            kwargs["default_headers"] = default_headers
        self._client = AsyncAnthropic(**kwargs)

    async def messages_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if payload.get("stream"):
                raise AnthropicUpstreamError("Use messages_stream for stream=True")
            resp = await self._client.messages.create(**payload)
            return resp.model_dump()
        except Exception as exc:
            raise _wrap(exc) from exc

    async def messages_stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        payload = {**payload, "stream": True}
        try:
            async with self._client.messages.stream(**payload) as stream:
                async for event in stream:
                    if hasattr(event, "model_dump"):
                        data = event.model_dump()
                    else:
                        data = {"type": event.type}
                    yield f"event: {event.type}\ndata: {json.dumps(data)}\n\n"
        except Exception as exc:
            raise _wrap(exc) from exc


def _wrap(exc: Exception) -> AnthropicUpstreamError:
    status = getattr(exc, "status_code", None)
    return AnthropicUpstreamError(str(exc), status_code=status)
