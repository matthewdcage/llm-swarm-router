"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from netllm_core.models import NetllmConfig
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.admin import (
    apply_config_patch,
    client_env_vars,
    config_summary,
    doctor_payload,
    peers_scan_payload,
    require_admin_access,
    save_config_patch,
)
from netllm_agent.metrics import metrics_bytes
from netllm_agent.service import AgentService

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    config: NetllmConfig | None = None,
    *,
    config_path: Path | None = None,
) -> FastAPI:
    cfg = config or NetllmConfig()
    service = AgentService(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await service.refresh_local_backends(
            persist_provider_urls=True,
            config_path=config_path,
        )
        service.start_background()
        yield
        service.stop_background()

    app = FastAPI(title="netllm-agent", version="0.2.3", lifespan=lifespan)
    app.state.service = service
    app.state.config = cfg

    @app.get("/")
    async def root(request: Request) -> Any:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/ui/", status_code=307)
        base = service.swarm.local_agent_url()
        return {
            "service": "netllm-agent",
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
                "messages": f"{base}/v1/messages",
                "status": f"{base}/netllm/v1/status",
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

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=metrics_bytes(), media_type="text/plain")

    # --- Swarm API ---
    @app.get("/netllm/v1/status")
    async def netllm_status() -> dict[str, Any]:
        await service.refresh_local_backends()
        for backend in service.pool.backends:
            if backend.enabled:
                service.pool.is_healthy(backend)
        return service.status_payload()

    @app.get("/netllm/v1/peers")
    async def netllm_peers() -> dict[str, Any]:
        return {"peers": service.swarm.all_peer_urls()}

    @app.get("/netllm/v1/backends")
    async def netllm_backends() -> dict[str, Any]:
        await service.refresh_local_backends()
        return {"backends": [b.model_dump(mode="json") for b in service.pool.backends]}

    @app.get("/netllm/v1/doctor")
    async def netllm_doctor(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        await service.refresh_local_backends()
        return doctor_payload(cfg, service)

    @app.get("/netllm/v1/config")
    async def netllm_config_summary(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        return config_summary(cfg)

    @app.get("/netllm/v1/client-env")
    async def netllm_client_env() -> dict[str, Any]:
        base = service.swarm.local_agent_url()
        return {"vars": client_env_vars(base)}

    @app.post("/netllm/v1/admin/discover")
    async def netllm_admin_discover(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        local = await service.refresh_local_backends(
            persist_provider_urls=True,
            config_path=config_path,
        )
        for backend in local:
            service.pool.is_healthy(backend, force_refresh=True)
        online = sum(
            1
            for backend in local
            if backend.enabled and backend.health.status == "online"
        )
        return {
            "ok": True,
            "backends_registered": len(local),
            "online": online,
        }

    @app.post("/netllm/v1/admin/config")
    async def netllm_admin_config(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        patch = await request.json()
        if not isinstance(patch, dict):
            raise HTTPException(status_code=400, detail="Expected JSON object")
        listen_before = cfg.agent.listen
        result = save_config_patch(
            cfg,
            patch,
            config_path=config_path,
            listen_before=listen_before,
        )
        merged = apply_config_patch(cfg, patch)
        cfg.agent = merged.agent
        cfg.discovery = merged.discovery
        cfg.swarm = merged.swarm
        cfg.routing = merged.routing
        cfg.ui = merged.ui
        app.state.config = cfg
        service.config = merged
        return result

    @app.post("/netllm/v1/admin/peers-scan")
    async def netllm_admin_peers_scan(
        request: Request,
        save: bool = False,
    ) -> dict[str, Any]:
        require_admin_access(request, cfg)
        return await peers_scan_payload(
            cfg,
            save=save,
            config_path=config_path,
        )

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
                    service.proxy_chat_completion_stream(
                        payload, headers=request.headers
                    ),
                    media_type="text/event-stream",
                )
            return await service.proxy_chat_completion(payload, headers=request.headers)
        except OpenAIUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- Anthropic Messages API proxy ---
    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Any:
        payload = await request.json()
        stream = bool(payload.get("stream"))
        hdrs = {k.lower(): v for k, v in request.headers.items()}

        try:
            if stream:
                return StreamingResponse(
                    service.proxy_messages_stream(payload, headers=hdrs),
                    media_type="text/event-stream",
                )
            return await service.proxy_messages(payload, headers=hdrs)
        except AnthropicUpstreamError as exc:
            status = exc.status_code or 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except OpenAIUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
