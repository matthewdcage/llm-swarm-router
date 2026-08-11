"""Admin-gated control plane: /netllm/v1/* config, registry and mutation routes.

Every route in this module calls ``gates.require_admin_access`` and nothing
else does — that split is the whole reason the module exists, and
``tests/test_route_auth_gates.py`` holds it against the pre-split mapping.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from netllm_core.config_schema import config_schema_document
from netllm_core.update import build_update_check_payload, version_payload
from netllm_discovery.lan import last_peer_scan_at
from netllm_discovery.local import last_provider_scan_at

from netllm_agent.admin import (
    apply_config_patch,
    cloud_provider_registry_payload,
    config_summary,
    doctor_payload,
    harness_registry_payload,
    local_provider_registry_payload,
    log_file_path,
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
        return await asyncio.to_thread(doctor_payload, cfg, service, config_path)

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

    @app.post("/netllm/v1/cloud/providers/{provider_id}/verify")
    async def netllm_cloud_provider_verify(
        provider_id: str, request: Request
    ) -> dict[str, Any]:
        """Check a provider's credential against the provider, and record it.

        A provider cannot be enabled until this has passed
        (netllm_core.config_guards.enforce_cloud_provider_verification), so
        this route is the only way to earn that. The body may carry
        `{"api_key": "..."}` -- a key the user has typed but not saved -- and
        that key is used for the probe and then dropped: nothing logs it,
        nothing stores it, and the response carries only its fingerprint.
        That is what stops the UI from having to save a broken key in order
        to find out that it is broken.

        The outcome IS persisted, onto the four read-only
        `[cloud.providers.<id>].verified_*` fields, because it has to
        outlive both a page reload and the agent process: `netllm cloud
        enable` runs in a different process and reads the same record.
        """
        gates.require_admin_access(request)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — an empty body is the common case
            body = {}
        api_key = ""
        if isinstance(body, dict):
            raw = body.get("api_key")
            api_key = raw.strip() if isinstance(raw, str) else ""
        payload = await service.verify_cloud_provider(provider_id, api_key or None)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown cloud provider")

        from netllm_core.cloud_verification import record_verification
        from netllm_core.models import CloudProviderConfig, save_config

        provider_cfg = cfg.cloud.providers.get(provider_id)
        if provider_cfg is None:
            provider_cfg = CloudProviderConfig()
            cfg.cloud.providers[provider_id] = provider_cfg
        record_verification(provider_cfg, payload)
        persisted = False
        if config_path is not None:
            save_config(cfg, config_path)
            persisted = True
        # The live config object is updated either way, so a check still
        # survives a page reload on an agent started without a config file
        # -- it just does not survive a restart, and the response says so
        # rather than letting the UI imply otherwise.
        #
        # `key_fingerprint` is dropped on the way out. It is one-way and the
        # caller has no use for it; the gate compares it server-side. Not
        # sending a derivative of a credential anywhere it is not needed is
        # the cheaper habit.
        public = {k: v for k, v in payload.items() if k != "key_fingerprint"}
        return {**public, "persisted": persisted}

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
    async def netllm_logs(
        request: Request,
        tail: int = 200,
        before: int | None = None,
        download: bool = False,
    ) -> Any:
        """Structured + raw agent log window, or the whole file.

        `before` is a 1-based line cursor: the window ends at `before - 1`, so
        a client can page backwards without the 10 s poll clobbering what it
        already fetched (see admin.logs_payload).

        `download=1` streams the *entire* agent.log as text/plain rather than
        adding a `/netllm/v1/logs/download` route. Same resource, same admin
        gate, different representation -- and the route table (and therefore
        `tests/contract/route-auth-gates.json`, which is derived from a pinned
        pre-split app.py and cannot express a post-split route) is unchanged.
        The file is unredacted: everything the agent logged, secrets included.
        """
        gates.require_admin_access(request)
        if download:
            path = log_file_path(cfg)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="No agent.log yet")
            return FileResponse(
                path,
                media_type="text/plain; charset=utf-8",
                filename="agent.log",
            )
        return logs_payload(cfg, tail=tail, before=before)

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
        # Echo the scan clock the pass just stamped. `status.discovery`
        # carries it too, so a client that refreshes gets it either way —
        # this saves the extra round-trip for one that only wants to say
        # "scanned just now" after its own button press.
        return {
            "ok": True,
            "backends_registered": len(local),
            "online": online,
            "last_scan_at": last_provider_scan_at() or None,
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
        payload = await peers_scan_payload(
            cfg,
            save=save,
            config_path=config_path,
        )
        # Same reasoning as /admin/discover: the scan stamps its own clock,
        # this just spares the caller a follow-up /status read.
        payload.setdefault("last_peer_scan_at", last_peer_scan_at() or None)
        return payload
