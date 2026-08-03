"""``/v1/messages`` — the Anthropic Messages surface adapter (plan §3 Phase 6).

Two upstream arms behind one adapter (``AgentService._messages_on_backend``):
an anthropic-format row is called natively, an openai-format row is
translated in and out. Both are per-backend and both resolve the served
model name at call time (D10), so the engine sees a single ``invoke``.

The Anthropic cloud is not a strategy candidate here — it is
``CandidateSchedule``'s ordered fallback tier, so it never shadows the local
mesh in a rotation (D6). That is schedule data; the loop does not know.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from netllm_core.anthropic_bridge import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    translate_openai_stream_to_anthropic,
)
from netllm_core.pool import Backend
from netllm_sdk_anthropic.client import AnthropicUpstream, AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.request_plan import RequestPlan
from netllm_agent.taxonomy import Surface

from ..engine import open_stream, run_with_failover
from .base import AnthropicDialectAdapter, Invocation

__all__ = ["MessagesAdapter", "MessagesSurfaceMixin"]


@dataclass(frozen=True)
class MessagesAdapter(AnthropicDialectAdapter):
    log_label: str = "messages backend"

    def guard(self, requested_model: str, effective_model: str) -> None:
        self.service._reject_non_chat_messages_model(requested_model, effective_model)

    def build_invocation(self, plan: RequestPlan, backend: Backend) -> Invocation:
        # The payload stays immutable and both arms re-derive the served
        # name themselves (_anthropic_payload_for_backend / the translated
        # arm's `oai_payload["model"] = ...`); recording it here keeps the
        # Invocation honest about what will go on the wire.
        return Invocation(
            backend=backend,
            upstream_model=self.service._model_for_backend(
                plan.model, backend, exact_model_only=plan.exact_model_only
            ),
            payload=plan.payload,
        )

    async def invoke(self, plan: RequestPlan, invocation: Invocation) -> Any:
        return await self.service._messages_on_backend(
            invocation.backend,
            invocation.payload,
            plan.model,
            plan.headers,
            plan.api_key,
            exact_model_only=plan.exact_model_only,
        )

    def invoke_stream(
        self, plan: RequestPlan, invocation: Invocation
    ) -> AsyncIterator[str]:
        """Both arms behind one iterator, exactly as ``invoke`` does.

        ``_messages_stream_on_backend`` picks the native or the translated
        arm off ``backend.api_format``; the engine sees one stream either
        way, which is why the ``candidates_exhausted``/``fallback_iter``
        state machine that used to interleave the two phases here could be
        deleted outright rather than ported.
        """
        return self.service._messages_stream_on_backend(
            invocation.backend,
            invocation.payload,
            plan.model,
            plan.headers,
            plan.api_key,
            exact_model_only=plan.exact_model_only,
        )

    def restore_model(
        self, plan: RequestPlan, invocation: Invocation, result: Any
    ) -> Any:
        # [D10] Compare against what actually came back, not against the
        # canonical name: an alias-only request (requested == canonical,
        # served ID different) would otherwise leave the backend's own name
        # in the response body.
        if isinstance(result, dict) and result.get("model") != plan.requested_model:
            result["model"] = plan.requested_model
        return result

    def classify_error(self, exc: BaseException) -> bool:
        """Both dialects retire a candidate here.

        The translated arm reaches openai-format rows through
        ``OpenAIUpstream``, so its failures arrive as ``OpenAIUpstreamError``
        even though the caller is speaking Anthropic.
        """
        return isinstance(exc, AnthropicUpstreamError | OpenAIUpstreamError)

    def exhaustion_error(
        self, plan: RequestPlan, last_error: Exception | None
    ) -> Exception:
        return self.service._exhausted(
            Surface.MESSAGES,
            plan.requested_model,
            last_error,
            api_key=plan.api_key,
        )


class MessagesSurfaceMixin:
    """``/v1/messages`` — entry points and the anthropic-dialect helpers."""

    def _anthropic_fallback_backends(
        self, *, local_only: bool, extra: list[Backend] | None = None
    ) -> list[Backend]:
        """Anthropic-format backends tried after the OpenAI-format mesh.

        Strategy selection runs over local providers and LAN peers; the
        Anthropic cloud (or any anthropic-format backend) stays a final
        fallback so it never shadows the local mesh in a rotation. `extra`
        carries the request-scoped legacy cloud row, which is not in the pool
        (F-04) but must still be reachable as this final tier.
        """
        return [
            b
            for b in [*self.pool.backends, *(extra or [])]
            if b.enabled
            and b.api_format == "anthropic"
            and (b.local or self.pool.allow_remote)
            and (not local_only or b.local)
        ]

    # `_messages_attempt` — one acquire→call→account cycle for the Messages
    # API — was deleted in Phase 6. It was the last per-attempt helper that
    # was not shared: `engine._run_attempt` now runs that cycle for every
    # non-streaming surface, with the Messages-specific parts
    # (`_messages_on_backend`, the response-model restore, the two-dialect
    # error classification) supplied by `MessagesAdapter`.

    async def proxy_messages(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Route Anthropic-dialect POST /v1/messages.

        [Phase 6] The last non-streaming loop to be strangled, and the one
        that shows why the engine had to be surface-agnostic rather than
        chat-shaped: this surface is the only one with a fallback phase
        (the anthropic tier, D6), the only one that retries across two wire
        dialects (D11), and the only one whose exhaustion can be a keyless
        401. All three are ``MessagesAdapter`` and ``CandidateSchedule``
        data now — ``run_with_failover`` runs the identical code it runs
        for chat.
        """
        plan = self.build_request_plan(payload, headers, surface=Surface.MESSAGES)
        return await run_with_failover(MessagesAdapter(self), plan)

    async def proxy_messages_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Route a streaming POST /v1/messages.

        [Phase 7] This function was the last home of the
        ``candidates_exhausted``/lazy-``fallback_iter`` state machine — a
        single ``while True`` that interleaved the strategy phase and the
        anthropic fallback tier with two booleans, and existed nowhere else
        in the codebase. Both phases are ``CandidateSchedule`` data walked
        by ``engine._walk_schedule`` now, so the machine is deleted rather
        than ported.

        [D9] Two client-visible flips ride with the move, both of them the
        Messages stream arm finally matching its four siblings: the
        requested model name is restored on ``message_start`` frames, and a
        mid-stream failure ends with a terminator event after the ``error``
        one.
        """
        plan = self.build_request_plan(payload, headers, surface=Surface.MESSAGES)
        session = await open_stream(MessagesAdapter(self), plan)
        async for chunk in session:
            yield chunk

    def _anthropic_payload_for_backend(
        self,
        payload: dict[str, Any],
        model: str,
        backend: Backend,
        *,
        exact_model_only: bool = False,
    ) -> dict[str, Any]:
        """[D10] Per-backend model resolution for the anthropic-format arm.

        The openai-format arm has always done this (`oai_payload["model"] =
        self._model_for_backend(...)`); the anthropic arm relied on the
        caller having mutated `payload["model"]` up-front, so it could only
        ever send one name for the whole request. Resolving here makes the
        Messages surfaces match chat/embeddings: the payload stays immutable
        and each attempt sends the name *its* backend serves.
        """
        upstream_model = self._model_for_backend(
            model, backend, exact_model_only=exact_model_only
        )
        if upstream_model == payload.get("model"):
            return payload
        return {**payload, "model": upstream_model}

    async def _messages_on_backend(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        headers: Mapping[str, str],
        fallback_api_key: str,
        *,
        exact_model_only: bool = False,
    ) -> dict[str, Any]:
        if backend.api_format == "anthropic":
            key = backend.resolve_api_key() or fallback_api_key
            if not key:
                raise AnthropicUpstreamError(
                    "ANTHROPIC_API_KEY required", status_code=401
                )
            client = AnthropicUpstream(
                key,
                base_url=backend.base_url,
                default_headers=self._anthropic_upstream_headers(backend, headers),
                auth_mode=backend.auth_mode,
                connect_timeout=self.config.routing.upstream_connect_timeout_s,
                read_timeout=self.config.routing.upstream_read_timeout_s,
            )
            return await client.messages_create(
                self._anthropic_payload_for_backend(
                    payload, model, backend, exact_model_only=exact_model_only
                )
            )
        oai_payload = anthropic_to_openai_request(payload)
        oai_payload["model"] = self._model_for_backend(
            model, backend, exact_model_only=exact_model_only
        )
        client = self._openai_upstream(backend, headers)
        result = await client.chat_completion(oai_payload)
        return openai_to_anthropic_response(result, model=model)

    async def _messages_stream_on_backend(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        headers: Mapping[str, str],
        fallback_api_key: str,
        *,
        exact_model_only: bool = False,
    ) -> AsyncIterator[str]:
        if backend.api_format == "anthropic":
            key = backend.resolve_api_key() or fallback_api_key
            if not key:
                raise AnthropicUpstreamError(
                    "ANTHROPIC_API_KEY required", status_code=401
                )
            client = AnthropicUpstream(
                key,
                base_url=backend.base_url,
                default_headers=self._anthropic_upstream_headers(backend, headers),
                auth_mode=backend.auth_mode,
                connect_timeout=self.config.routing.upstream_connect_timeout_s,
                read_timeout=self.config.routing.upstream_read_timeout_s,
            )
            async for chunk in client.messages_stream(
                self._anthropic_payload_for_backend(
                    payload, model, backend, exact_model_only=exact_model_only
                )
            ):
                yield chunk
            return
        oai_payload = anthropic_to_openai_request(payload)
        oai_payload["model"] = self._model_for_backend(
            model, backend, exact_model_only=exact_model_only
        )
        client = self._openai_upstream(backend, headers)
        async for chunk in translate_openai_stream_to_anthropic(
            client.chat_completion_stream(oai_payload),
            model=model,
        ):
            yield chunk

    # `_stream_with_metrics` — the chat stream's per-attempt accounting
    # wrapper — was deleted in Phase 7. Wrapping the iterator was the
    # mechanism behind D16: its success accounting sat *after* the
    # `async for`, so any consumer that abandoned the generator early (the
    # Responses bridge breaks at `data: [DONE]`) skipped it entirely.
    # `engine.StreamSession` reads one chunk ahead instead, which settles
    # the accounting before the terminator is handed on and cannot be
    # skipped by a consumer at all.
