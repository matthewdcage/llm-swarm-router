"""Swarm-facing read routes plus the peer heartbeat sink.

Everything here is either read-gated (``require_read_access``) or, in the
heartbeat's case, guarded by its own constant-time cluster-token compare —
a peer posting a heartbeat is never a local client, so the read gate would
reject the exact traffic this route exists for.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import HTTPException, Request, Response

from netllm_agent.admin import client_env_vars
from netllm_agent.routes.context import RouteContext


def register(ctx: RouteContext) -> None:
    app = ctx.app
    service = ctx.service
    cfg = ctx.cfg
    gates = ctx.gates

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
        gates.require_read_access(request)
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

    @app.get("/netllm/v1/peers")
    async def netllm_peers(request: Request) -> dict[str, Any]:
        gates.require_read_access(request)
        return {"peers": service.swarm.all_peer_urls()}

    @app.get("/netllm/v1/backends")
    async def netllm_backends(request: Request) -> dict[str, Any]:
        gates.require_read_access(request)
        await service.refresh_local_backends()
        return {"backends": [b.model_dump(mode="json") for b in service.pool.backends]}

    @app.get("/netllm/v1/client-env")
    async def netllm_client_env(request: Request) -> dict[str, Any]:
        gates.require_read_access(request)
        base = service.swarm.local_agent_url()
        return {"vars": client_env_vars(base)}

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
