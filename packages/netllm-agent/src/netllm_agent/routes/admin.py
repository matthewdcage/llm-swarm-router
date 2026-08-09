"""Admin-gated control plane: /netllm/v1/* config, registry and mutation routes.

Every route in this module calls ``gates.require_admin_access`` and nothing
else does — that split is the whole reason the module exists, and
``tests/test_route_auth_gates.py`` holds it against the pre-split mapping.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException, Request
from netllm_core.config_schema import config_schema_document
from netllm_core.update import build_update_check_payload, version_payload

from netllm_agent.admin import (
    apply_config_patch,
    cloud_provider_registry_payload,
    config_summary,
    doctor_payload,
    harness_registry_payload,
    local_provider_registry_payload,
    logs_payload,
    peers_scan_payload,
    save_config_patch,
)
from netllm_agent.routes.context import RouteContext


def register(ctx: RouteContext) -> None:
    app = ctx.app
    service = ctx.service
    cfg = ctx.cfg
    config_path = ctx.config_path
    gates = ctx.gates

    @app.get("/netllm/v1/doctor")
    async def netllm_doctor(request: Request) -> dict[str, Any]:
        gates.require_admin_access(request)
        await service.refresh_local_backends()
        # doctor_payload force-probes every local backend and all peers, so
        # it must not run on the event loop — same treatment netllm_status
        # gives its probe pass.
        return await asyncio.to_thread(doctor_payload, cfg, service)

    @app.get("/netllm/v1/version")
    async def netllm_version(request: Request) -> dict[str, Any]:
        gates.require_admin_access(request)
        return version_payload()

    @app.get("/netllm/v1/update/check")
    async def netllm_update_check(
        request: Request, force: bool = False
    ) -> dict[str, Any]:
        gates.require_admin_access(request)
        return await build_update_check_payload(force=force)

    @app.get("/netllm/v1/config")
    async def netllm_config_summary(request: Request) -> dict[str, Any]:
        gates.require_admin_access(request)
        return config_summary(cfg)

    @app.get("/netllm/v1/config/schema")
    async def netllm_config_schema(request: Request) -> dict[str, Any]:
        """Form shape for the 6 editable config sections — see
        config_summary above for values. Version-gated: the document only
        changes on a netllm version bump, so clients can cache it across
        sessions keyed on the returned "version" (see
        docs/config-schema-rewrite-plan.md §3.2)."""
        gates.require_admin_access(request)
        return config_schema_document()

    @app.get("/netllm/v1/cloud/providers")
    async def netllm_cloud_providers(request: Request) -> dict[str, Any]:
        """Registry metadata for the pre-configured cloud providers —
        single source of truth for the macOS app and dashboard (see
        admin.cloud_provider_registry_payload)."""
        gates.require_admin_access(request)
        return {"providers": cloud_provider_registry_payload()}

    @app.get("/netllm/v1/local-providers")
    async def netllm_local_providers(request: Request) -> dict[str, Any]:
        """Registry metadata for the discoverable local providers — the twin
        of /netllm/v1/cloud/providers, and the thing whose absence forced the
        dashboard and macOS app to hand-mirror the roster (and to drift:
        vLLM was prefilled on LM Studio's port). See
        admin.local_provider_registry_payload."""
        gates.require_admin_access(request)
        return {"providers": local_provider_registry_payload()}

    @app.get("/netllm/v1/harnesses")
    async def netllm_harnesses(request: Request) -> dict[str, Any]:
        """Known-harness registry merged with configured routing.sources
        state and live PATH detection (see admin.harness_registry_payload).
        Additive endpoint -- does not change /netllm/v1/config or
        /netllm/v1/status; an older client simply never calls this."""
        gates.require_admin_access(request)
        return {"harnesses": harness_registry_payload(cfg)}

    @app.get("/netllm/v1/cloud/providers/{provider_id}/models")
    async def netllm_cloud_provider_models(
        provider_id: str, request: Request
    ) -> dict[str, Any]:
        """Full model catalog for one provider, probed live from the
        provider's API with the configured key (static_models fallback).
        Ignores the models allowlist by design — this feeds the
        allowlist-editing UI (docs/models-ux-plan.md follow-up)."""
        gates.require_admin_access(request)
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
        gates.require_admin_access(request)
        body = await request.json()
        if not isinstance(body, dict) or "draining" not in body:
            raise HTTPException(
                status_code=400, detail="Expected JSON object with 'draining': bool"
            )
        service.draining = bool(body["draining"])
        return {"ok": True, "draining": service.draining}

    @app.get("/netllm/v1/logs")
    async def netllm_logs(request: Request, tail: int = 200) -> dict[str, Any]:
        gates.require_admin_access(request)
        return logs_payload(cfg, tail=tail)

    @app.post("/netllm/v1/admin/discover")
    async def netllm_admin_discover(request: Request) -> dict[str, Any]:
        gates.require_admin_access(request)
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
        gates.require_admin_access(request)
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
        gates.require_admin_access(request)
        return await peers_scan_payload(
            cfg,
            save=save,
            config_path=config_path,
        )
