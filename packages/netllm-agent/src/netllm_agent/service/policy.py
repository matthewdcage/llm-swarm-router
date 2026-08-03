"""Header/model/source policy and the one request prologue (plan §1).

Cluster ``policy.py``: header normalization, source attribution and
admission, scenario classification, the per-surface capability guards, the
routing resolution — and ``build_request_plan``, the single prologue every
proxy surface runs (plan §3 Phase 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from netllm_core.capabilities import model_capability
from netllm_core.models import (
    BACKEND_PIN_HEADER,
    HOPS_HEADER,
    LOCAL_ONLY_HEADER,
    MAX_FORWARD_HOPS,
    STRATEGY_HEADER,
    Backend,
    SourceConfig,
)
from netllm_core.routing_policy import ResolvedRouting, resolve_routing
from netllm_core.scenarios import Scenario, classify_scenario
from netllm_core.source_identity import ResolvedSource, resolve_source
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.metrics import SCENARIO_REQUESTS_TOTAL, SOURCE_REQUESTS_TOTAL
from netllm_agent.request_plan import RequestPlan, api_format_for
from netllm_agent.shard import extract_shard_context
from netllm_agent.taxonomy import Surface

from .core import SourceCapacityExceeded
from .surfaces import adapter_for

__all__ = ["PolicyMixin"]


class PolicyMixin:
    """Everything decided before a backend is chosen."""

    @staticmethod
    def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        if not headers:
            return {}
        return {str(k).lower(): str(v) for k, v in headers.items()}

    @staticmethod
    def _incoming_hops(headers: Mapping[str, str]) -> int:
        raw = headers.get(HOPS_HEADER, "").strip()
        return int(raw) if raw.isdigit() else 0

    @staticmethod
    def _wants_local_only(headers: Mapping[str, str] | None) -> bool:
        hdrs = PolicyMixin._normalize_headers(headers)
        raw = hdrs.get(LOCAL_ONLY_HEADER, "")
        if raw.strip().lower() in ("1", "true", "yes"):
            return True
        # Hop-count backstop: even if a peer strips the local-only
        # header, a request that has already crossed the mesh must not
        # be forwarded again.
        return PolicyMixin._incoming_hops(hdrs) >= MAX_FORWARD_HOPS

    def _model_for_backend(
        self, model: str, backend: Backend, *, exact_model_only: bool = False
    ) -> str:
        """Resolve the requested (canonical) model name to the ID this
        backend actually serves.

        One walk (netllm_core.model_resolution.ModelResolver): alias exact →
        alias tag-prefix → alias casefold → routing.model_pools group arm
        (same three arms, only on overflow) → catalog passthrough. It is the
        *same* walk that made this backend a candidate in
        ``backends_for_model``, which is the F-25 fix.

        Agent-hop requests (``exact_model_only=True``) skip pool/group
        substitution so the terminating peer routes the forwarded model
        name literally.
        """
        return self.pool.resolver.upstream_model(
            model, backend, exact_model_only=exact_model_only
        )

    @staticmethod
    def _reject_non_chat_model(requested_model: str, effective_model: str) -> None:
        """Refuse chat requests against models that cannot chat.

        Capability is classified on ``effective_model`` (post-rewrite); the
        400 quotes ``requested_model`` so operators never see internal ids
        (F-57).
        """
        cap = model_capability(effective_model)
        if cap == "chat":
            return
        hint = (
            " Use POST /v1/embeddings for embedding models."
            if cap == "embedding"
            else ""
        )
        raise OpenAIUpstreamError(
            (
                f"Model '{requested_model}' (capability: {cap}) "
                f"cannot serve chat completions.{hint}"
            ),
            status_code=400,
        )

    @staticmethod
    def _reject_non_chat_messages_model(
        requested_model: str, effective_model: str
    ) -> None:
        """Messages API variant of the non-chat model guard."""
        cap = model_capability(effective_model)
        if cap == "chat":
            return
        raise AnthropicUpstreamError(
            (
                f"Model '{requested_model}' (capability: {cap}) "
                "cannot serve the Messages API."
            ),
            status_code=400,
        )

    @staticmethod
    def _reject_non_embedding_model(requested_model: str, effective_model: str) -> None:
        """[D4] The embeddings surface's capability guard — new in Phase 4c.

        /v1/embeddings had no guard of any kind: a chat model sent here was
        dispatched to every backend in turn, each one 400/500-ing, until the
        retry budget ran out (contract vector
        ``guards-emb-chat-model-burns-retry-budget``). The chat and Messages
        surfaces have rejected the mirror-image mistake since forever.

        **User-visible tightening, release-note it.** ``model_capability``
        classifies by name and returns ``"chat"`` for anything it does not
        recognize, so an embedding model with an unrecognized name (no
        ``embed`` substring and none of the known encoder-family tokens:
        bge, gte, e5, minilm, bert, modernbert, colbert, splade) now gets a
        400 here where it used to route. Callers in that position should
        rename the served model, or map it with a ``[routing.model_aliases]``
        entry whose *request* name carries an embedding token.
        """
        cap = model_capability(effective_model)
        if cap == "embedding":
            return
        hint = (
            " Use POST /v1/chat/completions for chat models." if cap == "chat" else ""
        )
        raise OpenAIUpstreamError(
            (
                f"Model '{requested_model}' (capability: {cap}) "
                f"cannot serve embeddings.{hint}"
            ),
            status_code=400,
        )

    def _resolved_routing(
        self,
        model: str,
        *,
        api_format: str,
        headers: Mapping[str, str] | None,
        source: SourceConfig | None = None,
        scenario: str | None = None,
        surface: Surface | None = None,
    ) -> ResolvedRouting:
        hdrs = self._normalize_headers(headers)
        return resolve_routing(
            self.config.routing,
            model=model,
            api_format=api_format,  # type: ignore[arg-type]
            header_local_only=self._wants_local_only(hdrs),
            header_strategy=hdrs.get(STRATEGY_HEADER),
            header_backend=hdrs.get(BACKEND_PIN_HEADER),
            cloud=self.config.cloud,
            source=source,
            scenario=scenario,
            surface=surface.value if surface is not None else None,
        )

    def _attribute_source(self, headers: Mapping[str, str] | None) -> ResolvedSource:
        """Resolve and count the caller's source for this request.

        Called once per proxy entry point, alongside _resolved_routing.
        Counting here (rather than per-strategy-attempt) means retries
        against a second backend don't inflate a source's request count.
        """
        hdrs = self._normalize_headers(headers)
        resolved = resolve_source(headers=hdrs, sources=self.config.routing.sources)
        self._source_counts[resolved.id] = self._source_counts.get(resolved.id, 0) + 1
        SOURCE_REQUESTS_TOTAL.labels(
            source=resolved.id, resolved_via=resolved.resolved_via
        ).inc()
        return resolved

    def _source_config(self, source_id: str) -> SourceConfig | None:
        for s in self.config.routing.sources:
            if s.id == source_id:
                return s
        return None

    @staticmethod
    def _apply_source_model_rewrite(source: SourceConfig | None, model: str) -> str:
        if source is None or not source.model_rewrites:
            return model
        return source.model_rewrites.get(model, model)

    def _classify_and_record_scenario(
        self,
        payload: Mapping[str, Any],
        *,
        api_format: str,
        surface: Surface,
        source_id: str,
        headers: Mapping[str, str],
    ) -> Scenario:
        """Classify this request's scenario (Phase 3) and count it.

        Called once per proxy entry point, mirroring _attribute_source --
        counting here (not per-attempt) keeps retries from inflating a
        scenario's request count.
        """
        scenario = classify_scenario(
            payload,
            api_format=api_format,
            surface=surface.value,
            user_agent=headers.get("user-agent", ""),
        )
        key = (source_id, scenario)
        self._scenario_counts[key] = self._scenario_counts.get(key, 0) + 1
        SCENARIO_REQUESTS_TOTAL.labels(source=source_id, scenario=scenario).inc()
        return scenario

    @staticmethod
    def _apply_scenario_model(
        source: SourceConfig | None,
        scenario: Scenario,
        model: str,
        *,
        surface: Surface | None = None,
    ) -> str:
        if source is None:
            return model
        rule = source.scenarios.get(scenario)
        # [D14] Same gate resolve_routing applies to the rule's strategy /
        # local_only / allow_cloud fields, so a surface-qualified rule cannot
        # half-fire: either the whole rule applies here or none of it does.
        if rule is not None and not rule.applies_to(
            surface.value if surface is not None else None
        ):
            return model
        if rule is not None and rule.model:
            return rule.model
        return model

    def _source_admit(self, source_id: str, source: SourceConfig | None) -> None:
        """Per-source admission control: check and reserve in one step.

        Raises rather than queuing — a source at its configured
        max_concurrency gets a fast, clear rejection (HTTP 429) instead of
        silently piling onto the mesh.

        Check and increment must not be separated: they used to be, with an
        `await refresh_local_backends()` in between, so N concurrent requests
        for the same source all observed in_flight < cap and all proceeded.
        The cap was advisory under exactly the load it exists to bound
        (docs/architecture/07-findings-register.md F-08). This method runs
        before the first await in every proxy path, and the reservation is
        held for the whole request — including retries — by a single
        _source_release in the caller's finally.
        """
        cap = source.max_concurrency if source is not None else 0
        if cap > 0 and self._source_in_flight.get(source_id, 0) >= cap:
            raise SourceCapacityExceeded(source_id, cap)
        self._source_in_flight[source_id] = self._source_in_flight.get(source_id, 0) + 1

    def _source_release(self, source_id: str) -> None:
        self._source_in_flight[source_id] = max(
            0, self._source_in_flight.get(source_id, 0) - 1
        )

    def build_request_plan(
        self,
        payload: dict[str, Any],
        headers: Mapping[str, str] | None,
        *,
        surface: Surface,
    ) -> RequestPlan:
        """The one prologue every proxy surface runs (plan §3 Phase 4).

        Ordering is load-bearing and reproduces the five hand-copied
        prologues exactly:

        1. normalize headers once (D12),
        2. attribute the source and count it (once per request, not per
           attempt),
        3. classify and count the scenario,
        4. apply the model rewrite chain (source rewrites, then the
           scenario override),
        5. run the surface's capability guard,
        6. resolve routing,
        7. extract shard context,
        8. admit against the per-source cap — **last**, and before the
           caller's first ``await``, so a request rejected by a guard above
           never reserves a slot. The matching ``_source_release`` belongs
           in the caller's ``finally``.

        Steps 5 and 8 raise; nothing before step 8 has taken a reservation,
        so an exception out of this method needs no cleanup.
        """
        api_format = api_format_for(surface)
        hdrs = self._normalize_headers(headers)
        requested_model = payload.get("model", "")
        resolved_source = self._attribute_source(hdrs)
        source_cfg = self._source_config(resolved_source.id)
        scenario = self._classify_and_record_scenario(
            payload,
            api_format=api_format,
            surface=surface,
            source_id=resolved_source.id,
            headers=hdrs,
        )
        model = self._apply_source_model_rewrite(source_cfg, requested_model)
        model = self._apply_scenario_model(source_cfg, scenario, model, surface=surface)

        # [D4] Phase 4c: the guard is a plan step on EVERY surface
        # (/v1/embeddings used to have none at all). [Phase 6] Which guard
        # is `SurfaceAdapter.guard`, so the three-way branch that used to
        # stand here is gone: the surface→adapter map in surfaces/__init__
        # is the one place the question is answered.
        adapter_for(self, surface).guard(requested_model, model)
        # [D10] The Messages surfaces used to rewrite payload["model"] here,
        # up-front and once, which pinned every later attempt to the first
        # backend's idea of the name. The payload is now immutable on every
        # surface and the upstream name is derived per backend at call time
        # (_messages_on_backend / _messages_stream_on_backend), so a retry
        # onto a backend with a different alias sends *that* backend's
        # served ID.
        api_key = self._anthropic_api_key(hdrs) if surface is Surface.MESSAGES else ""

        routing = self._resolved_routing(
            model,
            api_format=api_format,
            headers=hdrs,
            source=source_cfg,
            scenario=scenario,
            surface=surface,
        )
        # [D5] Extracted on every surface, but only the chat paths pass it
        # to selection today; Phase 5 flips the rest.
        shard = extract_shard_context(payload, hdrs)
        self._source_admit(resolved_source.id, source_cfg)
        exact_model_only = self._incoming_hops(hdrs) >= 1
        return RequestPlan(
            surface=surface,
            headers=hdrs,
            source=resolved_source,
            source_config=source_cfg,
            scenario=scenario,
            requested_model=requested_model,
            model=model,
            routing=routing,
            shard=shard,
            payload=payload,
            api_key=api_key,
            exact_model_only=exact_model_only,
        )
