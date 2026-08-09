"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
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
    harness_registry_payload,
    local_provider_registry_payload,
    logs_payload,
    peers_scan_payload,
    require_admin_access,
    save_config_patch,
)
from netllm_agent.errors import install_error_handlers, parse_inference_json
from netllm_agent.metrics import metrics_bytes
from netllm_agent.service import AgentService, SourceCapacityExceeded

_STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _started_stream(gen: AsyncIterator[str]) -> AsyncIterator[str]:
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
        await service.telemetry.close()

    app = FastAPI(title="netllm-agent", version=get_version(), lifespan=lifespan)
    app.state.service = service
    app.state.config = cfg
    install_error_handlers(app)

    def _is_local_client(request: Request) -> bool:
        from netllm_core.platform import local_admin_client_hosts

        client_host = (request.client.host if request.client else "").lower()
        return client_host in local_admin_client_hosts()

    def _presents_cluster_token(request: Request, token: str) -> bool:
        auth = request.headers.get("Authorization", "")
        if secrets.compare_digest(auth, f"Bearer {token}"):
            return True
        return secrets.compare_digest(request.headers.get("x-api-key", ""), token)

    def require_inference_access(request: Request) -> None:
        """Opt-in gate (swarm.require_token_for_inference): non-local
        clients must present the cluster token on /v1/* routes. Peer
        agents forward with the token automatically."""
        token = (cfg.swarm.cluster_token or "").strip()
        if not cfg.swarm.require_token_for_inference or not token:
            return
        if _is_local_client(request) or _presents_cluster_token(request, token):
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "This netllm agent requires the swarm cluster token for "
                "inference. Send Authorization: Bearer <token>."
            ),
        )

    def require_read_access(request: Request) -> None:
        """Gate the read-only swarm endpoints once a cluster token exists.

        /netllm/v1/status returns every backend URL, the full model catalog,
        hostnames, agent ids, the peer list with versions, per-backend routed
        counts and token totals — complete fleet reconnaissance for anyone on
        the LAN (docs/architecture/07-findings-register.md F-13). It has to
        stay reachable for peer discovery, and it is: SwarmRegistry.fetch_peer
        and lan.fetch_agent_status already send the cluster token whenever one
        is configured.

        With no token configured this is a no-op, so the zero-config
        single-machine and open-trusted-LAN setups behave exactly as before.
        """
        token = (cfg.swarm.cluster_token or "").strip()
        if not token:
            return
        if _is_local_client(request) or _presents_cluster_token(request, token):
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "This netllm agent requires the swarm cluster token. "
                "Send Authorization: Bearer <token>."
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

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        # F-41: /metrics exposes backend ids/providers, model names, routed
        # counts and token totals — gated like the other read routes once a
        # cluster token exists (Prometheus scrape configs support
        # bearer_token); stays open when no token is configured.
        require_read_access(request)
        return Response(content=metrics_bytes(), media_type="text/plain")

    # --- Swarm API ---
    @app.get("/netllm/v1/status")
    async def netllm_status(
        request: Request,
        scan: bool = False,
        probe: bool = False,
        probe_peers: bool = False,
    ) -> dict[str, Any]:
        """Swarm status snapshot. Default is cache-fast for UI polling.

        ``scan=1`` reruns the local provider port scan (TTL-cached otherwise).
        ``probe=1`` force-refreshes health on local backends only (never peers).
        ``probe_peers=1`` refreshes peer-agent reachability via GET /health
        (never the peer's /netllm/v1/status or /v1/models).
        macOS Settings and the dashboard poll without these flags; explicit
        Refresh / doctor flows may pass them.
        """
        require_read_access(request)
        await service.refresh_local_backends(force_scan=scan)

        if probe or probe_peers:

            def _probe_for_status() -> None:
                if probe:
                    for backend in service.pool.backends:
                        if backend.enabled and backend.local:
                            service.pool.is_healthy(backend, force_refresh=True)
                if probe_peers:
                    service.pool.refresh_peer_health(force=True)

            await asyncio.to_thread(_probe_for_status)
        return await service.status_payload_enriched()

    @app.get("/netllm/v1/telemetry")
    async def netllm_telemetry(
        request: Request,
        scopes: str = "router,omlx",
        history: int = 60,
        watch: bool = True,
    ) -> dict[str, Any]:
        require_read_access(request)
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

    @app.get("/netllm/v1/peers")
    async def netllm_peers(request: Request) -> dict[str, Any]:
        require_read_access(request)
        return {"peers": service.swarm.all_peer_urls()}

    @app.get("/netllm/v1/backends")
    async def netllm_backends(request: Request) -> dict[str, Any]:
        require_read_access(request)
        await service.refresh_local_backends()
        return {"backends": [b.model_dump(mode="json") for b in service.pool.backends]}

    @app.get("/netllm/v1/doctor")
    async def netllm_doctor(request: Request) -> dict[str, Any]:
        require_admin_access(request, cfg)
        await service.refresh_local_backends()
        # doctor_payload force-probes every local backend and all peers, so
        # it must not run on the event loop — same treatment netllm_status
        # gives its probe pass.
        return await asyncio.to_thread(doctor_payload, cfg, service, config_path)

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

    @app.get("/netllm/v1/local-providers")
    async def netllm_local_providers(request: Request) -> dict[str, Any]:
        """Registry metadata for the discoverable local providers — the twin
        of /netllm/v1/cloud/providers, and the thing whose absence forced the
        dashboard and macOS app to hand-mirror the roster (and to drift:
        vLLM was prefilled on LM Studio's port). See
        admin.local_provider_registry_payload."""
        require_admin_access(request, cfg)
        return {"providers": local_provider_registry_payload()}

    @app.get("/netllm/v1/harnesses")
    async def netllm_harnesses(request: Request) -> dict[str, Any]:
        """Known-harness registry merged with configured routing.sources
        state and live PATH detection (see admin.harness_registry_payload).
        Additive endpoint -- does not change /netllm/v1/config or
        /netllm/v1/status; an older client simply never calls this."""
        require_admin_access(request, cfg)
        return {"harnesses": harness_registry_payload(cfg)}

    @app.get("/netllm/v1/cloud/providers/{provider_id}/models")
    async def netllm_cloud_provider_models(
        provider_id: str, request: Request
    ) -> dict[str, Any]:
        """Full model catalog for one provider, probed live from the
        provider's API with the configured key (static_models fallback).
        Ignores the models allowlist by design — this feeds the
        allowlist-editing UI (docs/models-ux-plan.md follow-up)."""
        require_admin_access(request, cfg)
        payload = await service.cloud_provider_models_probe(provider_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown cloud provider")
        return payload

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
    async def netllm_client_env(request: Request) -> dict[str, Any]:
        require_read_access(request)
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
        payload = await parse_inference_json(request)
        stream = bool(payload.get("stream"))

        try:
            if stream:
                return StreamingResponse(
                    await _started_stream(
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
        require_inference_access(request)
        payload = await parse_inference_json(request)
        stream = bool(payload.get("stream"))

        try:
            if stream:
                return StreamingResponse(
                    await _started_stream(
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
        require_inference_access(request)
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
        require_inference_access(request)
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
                    await _started_stream(
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

    return app
