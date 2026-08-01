"""``/v1/responses`` — pure edge translation over the chat surface.

Cluster ``surfaces/responses.py`` (plan §1). There is no ``ResponsesAdapter``
and no ``Surface.RESPONSES``: the Responses API is a wire dialect at the
edge, translated into and out of a Chat Completions request, so everything
below the translation is literally ``proxy_chat_completion`` /
``proxy_chat_completion_stream``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from netllm_core.openai_responses_bridge import (
    chat_to_responses_response,
    responses_to_chat_request,
    translate_chat_stream_to_responses,
)

__all__ = ["ResponsesSurfaceMixin"]


class ResponsesSurfaceMixin:
    """Codex-compatible ``/v1/responses``, both arms."""

    async def proxy_responses(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Codex CLI wiring (docs/cli-source-routing-plan.md): Codex
        requires wire_api = "responses" for every custom provider --
        Chat Completions support was fully removed from Codex in Feb
        2026. Translation happens only at this edge; everything else
        (source identity, per-source routing, scenario classification,
        capacity, failover) is the same proxy_chat_completion path every
        other OpenAI-compatible client already uses.
        """
        chat_payload = responses_to_chat_request(payload)
        result = await self.proxy_chat_completion(chat_payload, headers=headers)
        return chat_to_responses_response(result, model=payload.get("model", ""))

    async def proxy_responses_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Route a streaming POST /v1/responses.

        [Phase 7] Pure edge translation over C-S, as the non-stream twin is
        over C-NS — but with one thing said out loud that used to be left to
        the garbage collector. ``translate_chat_stream_to_responses``
        **breaks** out of its ``async for`` the moment it sees
        ``data: [DONE]`` (``openai_responses_bridge``), abandoning the chat
        generator suspended at that yield. Its ``finally`` — the pool
        release, the per-source admission release — then ran whenever
        CPython got around to finalizing the generator, which is to say at
        an unspecified time on an unspecified turn of the loop.

        Closing it here makes the release deterministic and prompt: it
        happens before this function returns, on the completed path and on
        the client-disconnect path alike.

        [D16] The *accounting* half of the same hole is fixed structurally
        upstream of here — ``StreamSession`` settles success before it hands
        on the chunk the bridge stops at, so RESP-S no longer has to be
        reached at all for its success to be recorded.
        """
        chat_payload = responses_to_chat_request(payload)
        stream = self.proxy_chat_completion_stream(chat_payload, headers=headers)
        try:
            async for event in translate_chat_stream_to_responses(
                stream, model=payload.get("model", "")
            ):
                yield event
        finally:
            await stream.aclose()
