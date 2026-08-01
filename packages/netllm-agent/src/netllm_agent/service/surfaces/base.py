"""The ``SurfaceAdapter`` protocol — the whole per-surface variance budget
(F-24/F-26 plan §1, §3 Phase 6).

``engine.run_with_failover`` is the *only* failover loop in the agent. It
knows how to walk a :class:`~netllm_agent.candidates.CandidateSchedule`,
acquire and release a backend, account for the attempt and end the request
when the schedule runs out. It deliberately knows nothing about chat versus
embeddings versus Messages: every difference between the surfaces is a
member of this protocol, and the loop reads them through the adapter.

That inversion is load-bearing, not stylistic. The defect F-24 documents is
that five hand-copied loops drifted — a fix (F-33 usage parsing, F-35
payload adaptation) would land on one loop and silently miss the other four.
The engine's *anti-erosion gate* (``tests/contract/test_engine_erosion.py``,
runnable in CI via ``scripts/check-engine-erosion.py``) makes the inversion
permanent by failing the build if ``engine.py`` ever references a
:class:`~netllm_agent.taxonomy.Surface` member or imports a ``surfaces/``
module other than this one. **A new per-surface need extends this protocol;
the loop never grows a branch.**

Members
-------
``guard``
    Capability check, run as a plan step before anything is routed.
``candidates``
    The request's attempt budget: cloud injection plus
    ``AgentService.build_candidates``.
``build_invocation`` / ``invoke``
    Per-backend upstream call, split so the engine can time it and so the
    per-backend model resolution is visible to ``restore_model``.
``extract_usage``
    Prompt/completion tokens out of the response body.
``restore_model``
    Put the caller's requested model name back on the way out.
``classify_error``
    Which exceptions are a *backend* failure (retry the next candidate)
    rather than a request failure (propagate now).
``exhaustion_error``
    The terminal error once no candidate is left.
``wire_error``
    The surface's native **HTTP** error envelope (plan §1, §3 Phase 3d).
    Deliberately *not* what ``mid_stream_error_frame`` returns, and Phase 7
    considered and rejected merging them: a mid-stream frame is in-band SSE
    with no status code, and the OpenAI arm's frame is the minimal
    ``{"error": {"message": ...}}`` that the contract vectors pin — routing
    it through ``openai_error_body`` would add ``type``/``param``/``code``
    and change bytes on a vector that carries no divergence ID, for
    cosmetics. The route layer currently reaches the same bodies through
    ``errors.install_error_handlers``, which keys on the request path; this
    member is the seam that replaces that keying when the surfaces own their
    own handlers.

Streaming members (plan §3 Phase 7)
-----------------------------------
``invoke_stream``
    The per-backend upstream SSE iterator. ``engine.open_stream`` pulls its
    first event *inside* the failover loop, which is what makes "a
    ``StreamSession`` exists only after upstream connect" structural rather
    than a convention.
``extract_stream_usage``
    Tokens carried by one SSE chunk, folded into the attempt's running
    maximum and handed to ``recorder.success`` when the stream completes.
``restore_stream_line``
    The per-line twin of ``restore_model``: put the caller's requested
    model name back on the way out, in this surface's wire dialect.
``mid_stream_error_frame``
    The dialect-native frames that end a stream which failed *after* its
    first byte reached the client. There is no retry once that happens —
    a second upstream response would be replayed into the same SSE body.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from netllm_core.pool import Backend

from netllm_agent.candidates import CandidateSchedule
from netllm_agent.errors import anthropic_error_body, openai_error_body
from netllm_agent.request_plan import RequestPlan
from netllm_agent.taxonomy import ExhaustionContext, Surface, exhaustion_error

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .. import AgentService

__all__ = [
    "BaseAdapter",
    "Invocation",
    "SurfaceHelpersMixin",
    "SurfaceAdapter",
    "restore_anthropic_sse_chunk",
    "restore_openai_sse_chunk",
]


def _restore_openai_sse_line(line: str, model: str) -> str:
    """Rewrite one OpenAI ``data:`` line's top-level ``model`` field."""
    if line.startswith("data: ") and '"model"' in line:
        try:
            body = json.loads(line[len("data: ") :])
            body["model"] = model
        except (ValueError, TypeError):
            return line
        return f"data: {json.dumps(body)}"
    return line


def restore_openai_sse_chunk(chunk: str, model: str) -> str:
    """Restore the requested model name across one OpenAI SSE chunk.

    A chunk can carry several ``data:`` lines (the wire framing is not the
    router's to choose); unparseable lines pass through untouched.
    """
    if '"model"' not in chunk:
        return chunk
    return "\n".join(
        _restore_openai_sse_line(line, model) for line in chunk.split("\n")
    )


def _restore_anthropic_sse_line(line: str, model: str) -> str:
    """Rewrite ``message_start``'s nested ``message.model``.

    The Anthropic stream dialect names the model exactly once, inside the
    ``message_start`` envelope — there is no top-level ``model`` key to
    rewrite the way the OpenAI chunks have. A line whose name already
    matches is returned *verbatim* rather than re-serialized, so restore is
    a no-op on the wire whenever it has nothing to do.
    """
    if not line.startswith("data: ") or '"model"' not in line:
        return line
    try:
        body = json.loads(line[len("data: ") :])
    except (ValueError, TypeError):
        return line
    if not isinstance(body, dict):
        return line
    message = body.get("message")
    if not isinstance(message, dict) or "model" not in message:
        return line
    if message["model"] == model:
        return line
    message["model"] = model
    return f"data: {json.dumps(body)}"


def restore_anthropic_sse_chunk(chunk: str, model: str) -> str:
    """Restore the requested model name across one Anthropic SSE chunk."""
    if '"model"' not in chunk:
        return chunk
    return "\n".join(
        _restore_anthropic_sse_line(line, model) for line in chunk.split("\n")
    )


@dataclass(frozen=True)
class Invocation:
    """One backend's worth of resolved call state.

    Built once per attempt, *inside* the timed region, so that upstream
    client construction and per-backend model resolution are attributed to
    the attempt they belong to (and their failures are classified by the
    same ``classify_error`` as the call itself — the D2 lesson).
    """

    backend: Backend
    # The name this backend actually serves, after model_aliases/pool
    # resolution. ``restore_model`` compares against it.
    upstream_model: str
    # The body to send. Never ``plan.payload`` mutated in place (D10).
    payload: dict[str, Any]
    # Surface-private: the upstream client, when the surface has one.
    client: Any = field(default=None, compare=False)


@runtime_checkable
class SurfaceAdapter(Protocol):
    """Everything ``run_with_failover`` needs that varies by surface."""

    service: AgentService
    #: Prefix for the per-attempt failure log line.
    log_label: str

    def guard(self, model: str) -> None: ...

    def candidates(self, plan: RequestPlan) -> CandidateSchedule: ...

    def build_invocation(self, plan: RequestPlan, backend: Backend) -> Invocation: ...

    async def invoke(self, plan: RequestPlan, invocation: Invocation) -> Any: ...

    def extract_usage(self, result: Any) -> tuple[int, int]: ...

    def restore_model(
        self, plan: RequestPlan, invocation: Invocation, result: Any
    ) -> Any: ...

    def classify_error(self, exc: BaseException) -> bool: ...

    def exhaustion_error(
        self, plan: RequestPlan, last_error: Exception | None
    ) -> Exception: ...

    def wire_error(self, status_code: int, message: str) -> dict[str, object]: ...

    # -- streaming (plan §3 Phase 7) ---------------------------------------

    def invoke_stream(
        self, plan: RequestPlan, invocation: Invocation
    ) -> AsyncIterator[str]: ...

    def extract_stream_usage(self, chunk: str) -> tuple[int, int]: ...

    def restore_stream_line(
        self, plan: RequestPlan, invocation: Invocation, chunk: str
    ) -> str: ...

    def mid_stream_error_frame(self, exc: BaseException) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class BaseAdapter:
    """Shared adapter behavior; the three concrete adapters subclass it.

    Only members that are genuinely identical across every surface live
    here. Anything a surface could reasonably want to differ on stays
    abstract, so a new surface has to answer the question rather than
    inherit an accident.
    """

    service: AgentService
    log_label: str = "backend"

    # -- candidate budget ---------------------------------------------------

    def legacy_cloud_backends(self, plan: RequestPlan) -> list[Backend]:
        """The request-scoped legacy cloud row, if this request may have one.

        Differs by dialect (an OpenAI key mints ``openai-cloud``, an
        Anthropic key ``anthropic-cloud``), which is why it is per-surface
        rather than part of ``build_candidates``.
        """
        raise NotImplementedError

    def candidates(self, plan: RequestPlan) -> CandidateSchedule:
        """Cloud injection, then the shared schedule builder.

        Called after ``refresh_local_backends()``: the pool the schedule
        measures must be the pool the loop selects from.
        """
        cloud_extra: list[Backend] = []
        if plan.routing.allow_cloud_inject:
            cloud_extra = self.legacy_cloud_backends(plan)
            self.service._materialize_cloud_provider_backends()
        return self.service.build_candidates(
            plan, cloud_extra, fallback_tiers=self.fallback_tiers(plan, cloud_extra)
        )

    def fallback_tiers(
        self, plan: RequestPlan, cloud_extra: list[Backend]
    ) -> tuple[tuple[Backend, ...], ...]:
        """Ordered tiers tried after the strategy phase is exhausted (D6).

        [Seam S2] Empty here, and empty for every OpenAI-dialect surface:
        their cloud topology is ``extra_candidates`` + ``prefer_cloud``.
        The Messages surface is the one with a real tier, and it supplies
        it from :class:`AnthropicDialectAdapter` — which is why
        ``build_candidates`` no longer has to ask ``plan.surface`` and no
        longer calls back into ``surfaces/messages.py``.

        Called *after* cloud materialization, on the same pool
        ``build_candidates`` measures.
        """
        return ()

    # -- accounting ---------------------------------------------------------

    def extract_usage(self, result: Any) -> tuple[int, int]:
        """Prompt/completion tokens from a non-streaming response body.

        The reader handles both dialects' ``usage`` shapes already
        (``input_tokens``/``output_tokens`` as well as
        ``prompt_tokens``/``completion_tokens``), so all three surfaces
        share it.
        """
        return self.service._usage_from_response(result)

    def invoke_stream(
        self, plan: RequestPlan, invocation: Invocation
    ) -> AsyncIterator[str]:
        """The upstream SSE iterator for one attempt.

        Deliberately a stub: ``/v1/embeddings`` has no streaming arm, and a
        surface that grows one must say so rather than inherit an accident.
        """
        raise NotImplementedError(f"{type(self).__name__} has no streaming arm")

    def extract_stream_usage(self, chunk: str) -> tuple[int, int]:
        """Prompt/completion tokens carried by one SSE chunk.

        The reader already understands both dialects' streaming usage
        shapes (Anthropic's ``message_start.message.usage`` and
        ``message_delta.usage``, OpenAI's ``stream_options.include_usage``
        chunk), so all three surfaces share it. The ``"usage"`` substring
        pre-check is the same cheap gate both bespoke loops used before the
        engine owned this.
        """
        if '"usage"' not in chunk:
            return 0, 0
        return self.service._usage_from_sse_chunk(chunk)

    # -- error shaping ------------------------------------------------------

    def wire_error(self, status_code: int, message: str) -> dict[str, object]:
        raise NotImplementedError

    def mid_stream_error_frame(self, exc: BaseException) -> tuple[str, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class OpenAIDialectAdapter(BaseAdapter):
    """Shared behavior of the two OpenAI-dialect non-streaming surfaces."""

    def legacy_cloud_backends(self, plan: RequestPlan) -> list[Backend]:
        return self.service._legacy_openai_cloud_backend(
            self.service._openai_api_key(plan.headers)
        )

    def build_invocation(self, plan: RequestPlan, backend: Backend) -> Invocation:
        upstream_model = self.service._model_for_backend(plan.model, backend)
        payload = (
            {**plan.payload, "model": upstream_model}
            if upstream_model != plan.model
            else plan.payload
        )
        return Invocation(
            backend=backend,
            upstream_model=upstream_model,
            payload=payload,
            client=self.service._openai_upstream(backend, plan.headers),
        )

    def restore_model(
        self, plan: RequestPlan, invocation: Invocation, result: Any
    ) -> Any:
        if invocation.upstream_model != plan.requested_model and isinstance(
            result, dict
        ):
            result["model"] = plan.requested_model
        return result

    def restore_stream_line(
        self, plan: RequestPlan, invocation: Invocation, chunk: str
    ) -> str:
        """Per-line twin of :meth:`restore_model`, same guard.

        The non-stream arm rewrites only when the served name differs from
        the requested one; the stream arm used to express that by *wrapping*
        the iterator conditionally (``_restore_stream_model``). Same
        condition, no wrapper.
        """
        if invocation.upstream_model == plan.requested_model:
            return chunk
        return restore_openai_sse_chunk(chunk, plan.requested_model)

    def wire_error(self, status_code: int, message: str) -> dict[str, object]:
        return openai_error_body(status_code, message)

    def mid_stream_error_frame(self, exc: BaseException) -> tuple[str, ...]:
        """OpenAI dialect: an error chunk, then the stream's terminator.

        ``data: [DONE]`` is what an OpenAI client waits for; omitting it
        leaves the client hanging on a socket that is already closed.
        """
        return (
            "data: " + json.dumps({"error": {"message": str(exc)}}) + "\n\n",
            "data: [DONE]\n\n",
        )


@dataclass(frozen=True)
class AnthropicDialectAdapter(BaseAdapter):
    """Shared behavior of the Anthropic-dialect surfaces (Messages)."""

    def legacy_cloud_backends(self, plan: RequestPlan) -> list[Backend]:
        return self.service._legacy_anthropic_cloud_backend(plan.api_key)

    def fallback_tiers(
        self, plan: RequestPlan, cloud_extra: list[Backend]
    ) -> tuple[tuple[Backend, ...], ...]:
        """[D6/Seam S2] The anthropic-format rows, tried after the mesh.

        Verbatim the condition ``build_candidates`` used to apply behind
        ``if plan.surface is Surface.MESSAGES``: same ``local_only``, same
        request-scoped extras, same "drop the tier if it is empty" (which
        the shared builder now does for every tier).
        """
        tier = tuple(
            self.service._anthropic_fallback_backends(
                local_only=plan.routing.local_only, extra=list(cloud_extra)
            )
        )
        return (tier,) if tier else ()

    def restore_stream_line(
        self, plan: RequestPlan, invocation: Invocation, chunk: str
    ) -> str:
        """[D9] Restore the requested name on ``message_start`` frames.

        This surface is the one that had *no* stream restore at all, which
        was invisible only for as long as the wire name happened to equal
        the canonical one. It stopped being invisible when D10 made the
        anthropic arm resolve the served id per backend.

        The guard is by *value*, not by ``invocation.upstream_model``: the
        translated arm synthesizes ``message_start`` from the upstream
        chunk's own ``model`` field (``anthropic_bridge`` line 169), so the
        name on the wire is not always the one this adapter resolved. A
        frame that already carries the requested name is passed through
        untouched, so restore never re-serializes a frame it agrees with.
        """
        return restore_anthropic_sse_chunk(chunk, plan.requested_model)

    def wire_error(self, status_code: int, message: str) -> dict[str, object]:
        return anthropic_error_body(status_code, message)

    def mid_stream_error_frame(self, exc: BaseException) -> tuple[str, ...]:
        """[D9] Anthropic dialect: an ``error`` event, then its terminator.

        The second frame is new in Phase 7. The OpenAI arm has always ended
        a failed stream with ``data: [DONE]``; the Anthropic arm emitted
        ``event: error`` and simply stopped, leaving a client that tracks
        the message lifecycle waiting for a ``message_stop`` that never
        came. ``message_stop`` is this dialect's terminator, so it is the
        one to send.
        """
        return (
            "event: error\ndata: "
            + json.dumps(
                {
                    "type": "error",
                    "error": {"type": "upstream_error", "message": str(exc)},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: "
            + json.dumps({"type": "message_stop"})
            + "\n\n",
        )


class SurfaceHelpersMixin:
    """Service-level helpers shared by the surfaces (plan §1 surfaces/base)."""

    def _exhausted(
        self,
        surface: Surface,
        requested_model: str,
        last_error: Exception | None,
        *,
        capability: str | None = None,
        api_key: str = "",
    ) -> Exception:
        """Every failover loop's terminal error, via the taxonomy."""
        return exhaustion_error(
            ExhaustionContext(
                surface=surface,
                pool=self.pool,
                requested_model=requested_model,
                capability=capability,
                api_key=api_key,
            ),
            last_error,
        )

    @staticmethod
    def _restore_sse_line_model(line: str, model: str) -> str:
        """[Phase 7] Kept as the service-level name for one OpenAI SSE
        line's model restore; the implementation lives in
        ``surfaces/base.py`` alongside its Anthropic twin, because restore
        is per-dialect and dialects are what adapters are."""
        return restore_openai_sse_chunk(line, model)
