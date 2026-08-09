"""FastAPI application factory.

Assembly only: build the service, the lifespan, the access gates and the
route context, then hand that context to every registrar in
``netllm_agent.routes.REGISTRARS``. The routes themselves live in
``netllm_agent/routes/`` — one module per coherent group (Phase 5b).

``tests/test_route_auth_gates.py`` holds ``create_app`` to a line budget so
this file cannot silently reaccumulate the 559-line shape it had.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from netllm_core.models import NetllmConfig
from netllm_core.version import get_version

from netllm_agent.errors import install_error_handlers
from netllm_agent.routes import REGISTRARS, AccessGates, RouteContext
from netllm_agent.service import AgentService

__all__ = ["create_app"]


def create_app(
    config: NetllmConfig | None = None,
    *,
    config_path: Path | None = None,
) -> FastAPI:
    cfg = config or NetllmConfig()
    service = AgentService(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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

    ctx = RouteContext(
        app=app,
        service=service,
        cfg=cfg,
        config_path=config_path,
        gates=AccessGates(cfg),
    )
    for register in REGISTRARS:
        register(ctx)

    return app
