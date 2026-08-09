"""Root banner, health probe, and the /ui static mount."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from netllm_core.version import get_version

from netllm_agent.routes.context import RouteContext

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def register(ctx: RouteContext) -> None:
    app = ctx.app
    service = ctx.service

    @app.get("/")
    async def root(request: Request) -> Any:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/ui/", status_code=307)
        base = service.swarm.local_agent_url()
        return {
            "service": "netllm-agent",
            "version": get_version(),
            "status": "running",
            "message": (
                "OpenAI-compatible router is up. Dashboard: /ui/ — APIs: /v1/*"
            ),
            "dashboard": f"{base}/ui/",
            "openai_base_url": f"{base}/v1",
            "anthropic_base_url": base,
            "endpoints": {
                "health": f"{base}/health",
                "models": f"{base}/v1/models",
                "chat": f"{base}/v1/chat/completions",
                "responses": f"{base}/v1/responses",
                "embeddings": f"{base}/v1/embeddings",
                "messages": f"{base}/v1/messages",
                "status": f"{base}/netllm/v1/status",
                "telemetry": f"{base}/netllm/v1/telemetry",
                "version": f"{base}/netllm/v1/version",
                "update_check": f"{base}/netllm/v1/update/check",
                "dashboard": f"{base}/ui/",
                "metrics": f"{base}/metrics",
            },
            "cli": {
                "status": "netllm status",
                "discover": "netllm discover",
                "env": "netllm env",
                "test": "netllm test",
            },
        }

    if _STATIC_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}
