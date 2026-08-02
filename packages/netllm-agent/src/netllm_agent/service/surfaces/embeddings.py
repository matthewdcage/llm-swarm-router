"""``/v1/embeddings`` — the embeddings surface adapter (plan §3 Phase 6).

Same OpenAI dialect and the same failover loop as chat, with three
differences the protocol carries: the upstream verb, the ``embedding``
capability guard (D4, new in Phase 4c — this surface used to have none and
burned the whole retry budget on a chat model), and the ``capability=``
hint on the exhaustion 404 so the "known models" list names embedding
models rather than everything discovered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.request_plan import RequestPlan
from netllm_agent.taxonomy import Surface

from ..engine import run_with_failover
from .base import Invocation, OpenAIDialectAdapter

__all__ = ["EmbeddingsAdapter", "EmbeddingsSurfaceMixin"]


@dataclass(frozen=True)
class EmbeddingsAdapter(OpenAIDialectAdapter):
    log_label: str = "embeddings backend"

    def guard(self, model: str) -> None:
        self.service._reject_non_embedding_model(model)

    async def invoke(self, plan: RequestPlan, invocation: Invocation) -> Any:
        return await invocation.client.embeddings(invocation.payload)

    def classify_error(self, exc: BaseException) -> bool:
        return isinstance(exc, OpenAIUpstreamError)

    def exhaustion_error(
        self, plan: RequestPlan, last_error: Exception | None
    ) -> Exception:
        return self.service._exhausted(
            Surface.EMBEDDINGS,
            plan.requested_model,
            last_error,
            capability="embedding",
        )


class EmbeddingsSurfaceMixin:
    """``/v1/embeddings`` — the entry point (plan §1 surfaces/embeddings)."""

    async def proxy_embeddings(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Route OpenAI-compatible POST /v1/embeddings.

        Same selection and failover loop as chat completions — literally
        the same one since Phase 6 (``engine.run_with_failover``), including
        agent-hop spillover to LAN peers (peer agents expose the same
        /v1/embeddings surface). Anthropic-format backends are excluded:
        the Anthropic Messages API has no embeddings endpoint, so the
        schedule holds them out as ineligible (D8) rather than pre-seeding
        the loop's ``tried`` set with them.

        [D5] Phase 5 also made the shard context reach selection here: the
        plan always extracted it, but this surface passed ``None``, so
        ``batch_shard`` and deterministic placement silently degraded to
        round_robin for every embedding request. The engine feeds
        ``plan.shard`` to selection and to the ledger's ``mark_done`` on
        exactly the terms the chat paths always had.
        """
        plan = self.build_request_plan(payload, headers, surface=Surface.EMBEDDINGS)
        return await run_with_failover(EmbeddingsAdapter(self), plan)
