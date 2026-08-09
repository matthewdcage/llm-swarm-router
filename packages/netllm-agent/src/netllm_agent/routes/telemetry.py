"""Observability surface: Prometheus /metrics and the telemetry snapshot."""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response

from netllm_agent.metrics import metrics_bytes
from netllm_agent.routes.context import RouteContext


def register(ctx: RouteContext) -> None:
    app = ctx.app
    service = ctx.service
    gates = ctx.gates

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        # F-41: /metrics exposes backend ids/providers, model names, routed
        # counts and token totals — gated like the other read routes once a
        # cluster token exists (Prometheus scrape configs support
        # bearer_token); stays open when no token is configured.
        gates.require_read_access(request)
        return Response(content=metrics_bytes(), media_type="text/plain")

    @app.get("/netllm/v1/telemetry")
    async def netllm_telemetry(
        request: Request,
        scopes: str = "router,omlx",
        history: int = 60,
        watch: bool = True,
    ) -> dict[str, Any]:
        gates.require_read_access(request)
        scope_set = {s.strip() for s in scopes.split(",") if s.strip()}
        if watch:
            service.telemetry.subscribe()
        try:
            payload = await service.telemetry.build_payload(
                service,
                scopes=scope_set,
                include_history=history > 0,
            )
            payload["subscribers"] = service.telemetry.has_subscribers
            return payload
        finally:
            if watch:
                service.telemetry.unsubscribe()
