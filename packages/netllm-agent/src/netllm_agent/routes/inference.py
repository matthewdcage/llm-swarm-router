"""The dual inference surface: OpenAI /v1/* plus Anthropic /v1/messages.

Every route here is inference-gated. The upstream-error mapping is
deliberately identical between the OpenAI routes and, for the
``OpenAIUpstreamError`` arm, /v1/messages — see the D11 note on that
route.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.errors import parse_inference_json
from netllm_agent.routes.context import RouteContext
from netllm_agent.service import SourceCapacityExceeded


async def started_stream(gen: AsyncIterator[str]) -> AsyncIterator[str]:
    """Pull the first chunk before the StreamingResponse exists (F-32).

    An async generator executes nothing until first iteration, and
    StreamingResponse sends ``http.response.start`` before iterating — so
    pre-stream failures (source admission, routing resolution, every
    backend failed) would surface as HTTP 200 with an aborted body.
    Awaiting the first chunk here makes those errors raise in the route
    handler, where they map to real status codes (429/502/...).

    Mid-stream failures after bytes are on the wire can still only abort
    the SSE stream — that is acceptable and standard; the service layer
    emits an in-band error event for that case.
    """
    try:
        first = await anext(gen)
    except StopAsyncIteration:
        first = None

    async def _replay() -> AsyncIterator[str]:
        try:
            if first is not None:
                yield first
                async for chunk in gen:
                    yield chunk
        finally:
            await gen.aclose()

    return _replay()


def register(ctx: RouteContext) -> None:
    app = ctx.app
    service = ctx.service
    gates = ctx.gates

    # --- OpenAI-compatible proxy ---
    @app.get("/v1/models")
    async def openai_models(request: Request) -> dict[str, Any]:
        gates.require_inference_access(request)
        return await service.list_models_aggregated()

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(request: Request) -> Any:
        gates.require_inference_access(request)
        payload = await parse_inference_json(request)
        stream = bool(payload.get("stream"))

        try:
            if stream:
                return StreamingResponse(
                    await started_stream(
                        service.proxy_chat_completion_stream(
                            payload, headers=request.headers
                        )
                    ),
                    media_type="text/event-stream",
                )
            return await service.proxy_chat_completion(payload, headers=request.headers)
        except SourceCapacityExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except OpenAIUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code if exc.status_code in (400, 404) else 502,
                detail=str(exc),
            ) from exc

    @app.post("/v1/responses")
    async def openai_responses(request: Request) -> Any:
        """Codex CLI surface: Codex requires wire_api = "responses" for
        every custom provider (Chat Completions removed Feb 2026) — see
        docs/cli-source-routing-plan.md and
        netllm_core.openai_responses_bridge."""
        gates.require_inference_access(request)
        payload = await parse_inference_json(request)
        stream = bool(payload.get("stream"))

        try:
            if stream:
                return StreamingResponse(
                    await started_stream(
                        service.proxy_responses_stream(payload, headers=request.headers)
                    ),
                    media_type="text/event-stream",
                )
            return await service.proxy_responses(payload, headers=request.headers)
        except SourceCapacityExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except OpenAIUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code if exc.status_code in (400, 404) else 502,
                detail=str(exc),
            ) from exc

    @app.post("/v1/embeddings")
    async def openai_embeddings(request: Request) -> Any:
        gates.require_inference_access(request)
        payload = await parse_inference_json(request)
        try:
            return await service.proxy_embeddings(payload, headers=request.headers)
        except SourceCapacityExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except OpenAIUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code if exc.status_code in (400, 404) else 502,
                detail=str(exc),
            ) from exc

    # --- Anthropic Messages API proxy ---
    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Any:
        gates.require_inference_access(request)
        payload = await parse_inference_json(request)
        stream = bool(payload.get("stream"))
        # [D12] The route layer used to lower-case the header keys here
        # before handing them over — the only route that did. Every proxy
        # entry point normalizes exactly once now, at the top of
        # build_request_plan (AgentService._normalize_headers), so this was a
        # duplicate whose only future was to drift out of step with it.

        try:
            if stream:
                return StreamingResponse(
                    await started_stream(
                        service.proxy_messages_stream(payload, headers=request.headers)
                    ),
                    media_type="text/event-stream",
                )
            return await service.proxy_messages(payload, headers=request.headers)
        except SourceCapacityExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except AnthropicUpstreamError as exc:
            status = exc.status_code or 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except OpenAIUpstreamError as exc:
            # D11 (plan §3 Phase 3, commit 3c): an OpenAIUpstreamError here
            # comes from a translated (openai-format) backend, and used to
            # be flattened to 502 — so a caller's own bad request or an
            # unknown model read as "the router is broken". Forward 400/404
            # exactly as the OpenAI surfaces above do; everything else
            # stays 502. The body is still Anthropic-shaped (errors.py
            # keys on the path, F-38).
            raise HTTPException(
                status_code=exc.status_code if exc.status_code in (400, 404) else 502,
                detail=str(exc),
            ) from exc
