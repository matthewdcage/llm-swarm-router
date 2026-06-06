"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from netllm_core.models import NetllmConfig
from netllm_sdk_anthropic.client import AnthropicUpstream, AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.metrics import metrics_bytes
from netllm_agent.service import AgentService


def create_app(config: NetllmConfig | None = None) -> FastAPI:
    cfg = config or NetllmConfig()
    service = AgentService(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await service.refresh_local_backends()
        service.start_background()
        yield
        service.stop_background()

    app = FastAPI(title="netllm-agent", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.config = cfg

    @app.get("/")
    async def root() -> dict[str, Any]:
        base = service.swarm.local_agent_url()
        return {
            "service": "netllm-agent",
            "status": "running",
            "message": (
                "OpenAI-compatible router is up. Use /v1/* — not this root path."
            ),
            "openai_base_url": f"{base}/v1",
            "endpoints": {
                "health": f"{base}/health",
                "models": f"{base}/v1/models",
                "chat": f"{base}/v1/chat/completions",
                "status": f"{base}/netllm/v1/status",
                "metrics": f"{base}/metrics",
            },
            "cli": {
                "status": "netllm status",
                "discover": "netllm discover",
                "test": "netllm test",
            },
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=metrics_bytes(), media_type="text/plain")

    # --- Swarm API ---
    @app.get("/netllm/v1/status")
    async def netllm_status() -> dict[str, Any]:
        return service.status_payload()

    @app.get("/netllm/v1/peers")
    async def netllm_peers() -> dict[str, Any]:
        return {"peers": service.swarm.all_peer_urls()}

    @app.get("/netllm/v1/backends")
    async def netllm_backends() -> dict[str, Any]:
        await service.refresh_local_backends()
        return {"backends": [b.model_dump(mode="json") for b in service.pool.backends]}

    @app.post("/netllm/v1/heartbeat")
    async def netllm_heartbeat(request: Request) -> Response:
        payload = await request.json()
        token = cfg.swarm.cluster_token
        if token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                raise HTTPException(status_code=401, detail="Invalid cluster token")
        await service.handle_heartbeat(payload)
        return Response(status_code=204)

    # --- OpenAI-compatible proxy ---
    @app.get("/v1/models")
    async def openai_models() -> dict[str, Any]:
        return await service.list_models_aggregated()

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(request: Request) -> Any:
        payload = await request.json()
        stream = bool(payload.get("stream"))

        try:
            if stream:
                return StreamingResponse(
                    service.proxy_chat_completion_stream(payload),
                    media_type="text/event-stream",
                )
            return await service.proxy_chat_completion(payload)
        except OpenAIUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- Anthropic Messages API proxy ---
    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Any:
        payload = await request.json()
        stream = bool(payload.get("stream"))
        import os

        api_key = request.headers.get("x-api-key") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        if not api_key:
            raise HTTPException(status_code=401, detail="ANTHROPIC_API_KEY required")

        client = AnthropicUpstream(api_key)
        try:
            if stream:
                gen = client.messages_stream(payload)
                return StreamingResponse(gen, media_type="text/event-stream")
            return await client.messages_create(payload)
        except AnthropicUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
