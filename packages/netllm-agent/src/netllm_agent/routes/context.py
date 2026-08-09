"""What every route registrar is handed.

``create_app`` builds one of these and passes it to each registrar in
``netllm_agent.routes.REGISTRARS``; nothing else crosses the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from netllm_core.models import NetllmConfig

from netllm_agent.routes.gates import AccessGates
from netllm_agent.service import AgentService


@dataclass(frozen=True)
class RouteContext:
    app: FastAPI
    service: AgentService
    cfg: NetllmConfig
    config_path: Path | None
    gates: AccessGates
