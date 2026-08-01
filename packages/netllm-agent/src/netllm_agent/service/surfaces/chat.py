"""``/v1/chat/completions`` — the chat surface adapter (plan §3 Phase 6).

Also serves ``/v1/responses``: the Responses API is a pure edge translation
over this surface (``responses_to_chat_request`` /
``chat_to_responses_response``), which is why ``Surface`` has no
``RESPONSES`` member and this adapter needs no knowledge of it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.request_plan import RequestPlan
from netllm_agent.taxonomy import Surface

from ..engine import open_stream, run_with_failover
from .base import Invocation, OpenAIDialectAdapter

__all__ = ["ChatAdapter", "ChatSurfaceMixin"]


@dataclass(frozen=True)
class ChatAdapter(OpenAIDialectAdapter):
    log_label: str = "backend"

    def guard(self, model: str) -> None:
        self.service._reject_non_chat_model(model)

    async def invoke(self, plan: RequestPlan, invocation: Invocation) -> Any:
        return await invocation.client.chat_completion(invocation.payload)

    def invoke_stream(
        self, plan: RequestPlan, invocation: Invocation
    ) -> AsyncIterator[str]:
        """The raw OpenAI SSE iterator.

        Nothing wraps it: usage capture, model restore and success
        accounting are ``StreamSession``'s, so ``_stream_with_metrics`` —
        the wrapper whose post-loop accounting the Responses bridge used to
        abandon (D16) — has no reason to exist.
        """
        return invocation.client.chat_completion_stream(invocation.payload)

    def classify_error(self, exc: BaseException) -> bool:
        """Only upstream errors retire a candidate.

        Chat talks the OpenAI dialect to *every* row it selects, including
        anthropic-format ones (``excluded_api_formats`` excludes nothing
        for this surface), so ``OpenAIUpstreamError`` is the whole set.
        """
        return isinstance(exc, OpenAIUpstreamError)

    def exhaustion_error(
        self, plan: RequestPlan, last_error: Exception | None
    ) -> Exception:
        return self.service._exhausted(Surface.CHAT, plan.requested_model, last_error)


class ChatSurfaceMixin:
    """``/v1/chat/completions`` — the two entry points (plan §1 surfaces/chat)."""

    async def proxy_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Route OpenAI-compatible POST /v1/chat/completions.

        [Phase 6] The bespoke failover loop that used to live here is now
        ``engine.run_with_failover`` — the same loop the embeddings and
        Messages surfaces run. Everything that made this copy different is
        a member of ``ChatAdapter``.
        """
        plan = self.build_request_plan(payload, headers, surface=Surface.CHAT)
        return await run_with_failover(ChatAdapter(self), plan)

    async def proxy_chat_completion_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Route a streaming POST /v1/chat/completions.

        [Phase 7] The bespoke stream loop that used to live here — its own
        copy of selection, acquire/release, the `yielded_any` no-replay
        guard and the `_stream_with_metrics` accounting wrapper — is now
        ``engine.open_stream`` plus ``StreamSession``, the same pair the
        Messages stream surface runs. Note what the shape says: the failover
        walk is finished by the time ``open_stream`` returns, so nothing
        below this line can choose a different backend.
        """
        plan = self.build_request_plan(payload, headers, surface=Surface.CHAT)
        session = await open_stream(ChatAdapter(self), plan)
        async for chunk in session:
            yield chunk
