"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from netllm_core.config_schema import config_schema_document
from netllm_core.models import NetllmConfig
from netllm_core.update import build_update_check_payload, version_payload
from netllm_core.version import get_version
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

from netllm_agent.admin import (
    apply_config_patch,
    client_env_vars,
    cloud_provider_registry_payload,
    config_summary,
    doctor_payload,
    logs_payload,
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

    app = FastAPI(title="netllm-agent", version=get_version(), lifespan=lifespan)
    app.state.service = service
    app.state.config = cfg

    def require_inference_access(request: Request) -> None:
        """Opt-in gate (swarm.require_token_for_inference): non-local
        clients must present the cluster token on /v1/* routes. Peer
        agents forward with the token automatically."""
        token = (cfg.swarm.cluster_token or "").strip()
        if not cfg.swarm.require_token_for_inference or not token:
            return
        from netllm_core.platform import local_admin_client_hosts

        client_host = (request.client.host if request.client else "").lower()
        if client_host in local_admin_client_hosts():
            return
        auth = request.headers.get("Authorization", "")
        if secrets.compare_digest(auth, f"Bearer {token}"):
            return
        if secrets.compare_digest(request.headers.get("x-api-key", ""), token):
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "This netllm agent requires the swarm cluster token for "
                "inference. Send Authorization: Bearer <token>."
            ),
        )

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
                "embeddings": f"{base}/v1/embeddings",
                "messages": f"{base}/v1/messages",
                "status": f"{base}/netllm/v1/status",
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

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=metrics_bytes(), media_type="text/plain")

    # --- Swarm API ---
    @app.get("/netllm/v1/status")
    async def netllm_status() -> dict[str, Any]:
        await service.refresh_local_backends(force_scan=True)

        def _probe_local() -> None:
            for backend in service.pool.backends:
                # Local rows only — probing peer agents from a status
                # handler recurses when the peer probes us back.
                if backend.enabled and backend.local:
                    service.pool.is_healthy(backend, force_refresh=True)

        await asyncio.to_thread(_probe_local)
        return await service.status_payload_enriched()

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

    @app.get("/netllm/v1/version")
    async def netllm_version(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        return version_payload()

    @app.get("/netllm/v1/update/check")
    async def netllm_update_check(
        request: Request, force: bool = False
    ) -> dict[str, Any]:
        require_admin_access(request, cfg)
        return await build_update_check_payload(force=force)

    @app.get("/netllm/v1/config")
    async def netllm_config_summary(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        return config_summary(cfg)

    @app.get("/netllm/v1/config/schema")
    async def netllm_config_schema(request: Request) -> dict[str, Any]:
        """Form shape for the 6 editable config sections — see
        config_summary above for values. Version-gated: the document only
        changes on a netllm version bump, so clients can cache it across
        sessions keyed on the returned "version" (see
        docs/config-schema-rewrite-plan.md §3.2)."""
        require_admin_access(request, cfg)
        return config_schema_document()

    @app.get("/netllm/v1/cloud/providers")
    async def netllm_cloud_providers(request: Request) -> dict[str, Any]:
        """Registry metadata for the pre-configured cloud providers —
        single source of truth for the macOS app and dashboard (see
        admin.cloud_provider_registry_payload)."""
        require_admin_access(request, cfg)
        return {"providers": cloud_provider_registry_payload()}

    @app.post("/netllm/v1/admin/drain")
    async def netllm_admin_drain(request: Request) -> dict[str, Any]:
        """Toggle this agent's drain state ahead of a planned restart or
        shutdown. Draining removes this agent from every peer's routing
        candidates (via the next heartbeat) without touching requests
        already in flight here — nothing is cancelled. Runtime-only,
        never persisted; resets to False on the next process start."""
        require_admin_access(request, cfg)
        body = await request.json()
        if not isinstance(body, dict) or "draining" not in body:
            raise HTTPException(
                status_code=400, detail="Expected JSON object with 'draining': bool"
            )
        service.draining = bool(body["draining"])
        return {"ok": True, "draining": service.draining}

    @app.get("/netllm/v1/client-env")
    async def netllm_client_env() -> dict[str, Any]:
        base = service.swarm.local_agent_url()
        return {"vars": client_env_vars(base)}

    @app.get("/netllm/v1/logs")
    async def netllm_logs(request: Request, tail: int = 200) -> dict[str, Any]:
        require_admin_access(request, cfg)
        return logs_payload(cfg, tail=tail)

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
        cfg.cloud = merged.cloud
        app.state.config = cfg
        # Hot-apply: re-sync pool knobs and invalidate the provider-scan
        # cache so routing/backend edits take effect without a restart.
        service.apply_config(merged)
        await service.refresh_local_backends(force_scan=True)
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
            if not secrets.compare_digest(auth, f"Bearer {token}"):
                raise HTTPException(status_code=401, detail="Invalid cluster token")
        await service.handle_heartbeat(payload)
        return Response(status_code=204)

    # --- OpenAI-compatible proxy ---
    @app.get("/v1/models")
    async def openai_models(request: Request) -> dict[str, Any]:
        require_inference_access(request)
        return await service.list_models_aggregated()

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(request: Request) -> Any:
        require_inference_access(request)
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
            raise HTTPException(
                status_code=exc.status_code if exc.status_code in (400, 404) else 502,
                detail=str(exc),
            ) from exc

    @app.post("/v1/embeddings")
    async def openai_embeddings(request: Request) -> Any:
        require_inference_access(request)
        payload = await request.json()
        try:
            return await service.proxy_embeddings(payload, headers=request.headers)
        except OpenAIUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code if exc.status_code in (400, 404) else 502,
                detail=str(exc),
            ) from exc

    # --- Anthropic Messages API proxy ---
    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Any:
        require_inference_access(request)
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
