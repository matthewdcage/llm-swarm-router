"""Drive all seven request paths through the REAL app.py routes.

A real ``AgentService`` is wired to FakeFarm backends (see farm.py) and
exercised through FastAPI/Starlette's TestClient, so edge translation
(Responses bridge), the F-32 pre-flight stream split, and route-layer error
mapping are all inside the tested surface.

Seven paths (behavior-matrix.md "The six paths" + RESP split into ns/s):
chat_ns, chat_s, emb, messages_ns, messages_s, responses_ns, responses_s.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest import mock

from farm import FARM_API_KEY, FakeFarm
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig
from probe import ServiceProbe

PATHS = (
    "chat_ns",
    "chat_s",
    "emb",
    "messages_ns",
    "messages_s",
    "responses_ns",
    "responses_s",
)

_ROUTE = {
    "chat_ns": "/v1/chat/completions",
    "chat_s": "/v1/chat/completions",
    "emb": "/v1/embeddings",
    "messages_ns": "/v1/messages",
    "messages_s": "/v1/messages",
    "responses_ns": "/v1/responses",
    "responses_s": "/v1/responses",
}
_STREAMING = {"chat_s", "messages_s", "responses_s"}

# Env keys that flip the legacy cloud-inject rows on — scrubbed so vectors
# never depend on the recording machine's environment.
_SCRUBBED_ENV = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def scanned_backend_id(base_url: str) -> str:
    """Pool id of a scan-discovered row (netllm_discovery.local
    scan_results_to_backends): a deterministic uuid5 of the base URL.
    Override rows the scan cannot see keep the base URL itself as id."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, base_url))


@dataclass
class DriveResult:
    status: int
    body: Any  # parsed JSON for non-stream responses, else None
    frames: list[str] | None  # SSE frames for stream paths, else None


@dataclass
class ContractEnv:
    farm: FakeFarm
    client: TestClient
    service: Any
    probe: ServiceProbe

    def drive(self, request: dict[str, Any]) -> DriveResult:
        return drive(self.client, request)


def build_config(scenario: dict[str, Any], farm: FakeFarm) -> NetllmConfig:
    """Scenario dict -> a real NetllmConfig wired to the farm backends."""
    cfg = NetllmConfig()
    data = cfg.model_dump(mode="python")
    data["swarm"]["mdns"] = False
    data["agent"]["advertise"] = False
    # No local provider port scanning: farm rows enter via routing.backends.
    data["discovery"]["providers"] = []
    data["routing"]["backends"] = [
        {
            "base_url": b.base_url,
            "provider": "custom",
            "api_format": b.api_format,
            "api_key": FARM_API_KEY,
            "enabled": True,
            "local": b.local,
        }
        for b in farm.backends
        # `configured: false` hosts are on the wire but not in config — the
        # router must reach them some other way (a request-scoped legacy
        # cloud row, which a configured row at the same URL would suppress).
        if b.configured
    ]
    for section in ("routing", "cloud", "agent", "swarm"):
        overrides = scenario.get(section)
        if overrides:
            data[section].update(overrides)
    return NetllmConfig.model_validate(data)


@contextmanager
def contract_environment(scenario: dict[str, Any]) -> Iterator[ContractEnv]:
    """Build farm + config + real app; yield a ready-to-drive environment.

    ``scenario`` shape (JSON-friendly, checked into vectors):
    ``{"backends": [{"name", "api_format", "models", "script", "local"}],
    "routing": {...overrides...}, "cloud": {...}, ...}``.
    """
    farm = FakeFarm()
    for spec in scenario.get("backends", []):
        farm.add_backend(
            spec["name"],
            api_format=spec.get("api_format", "openai"),
            models=spec.get("models", []),
            script=spec.get("script", []),
            local=spec.get("local", True),
            scan_visible=spec.get("scan_visible", True),
            url=spec.get("url"),
            configured=spec.get("configured", True),
        )
    env_patch = {k: "" for k in _SCRUBBED_ENV}
    with (
        farm.installed(),
        mock.patch.dict(os.environ, env_patch),
    ):
        for key in _SCRUBBED_ENV:
            os.environ.pop(key, None)
        cfg = build_config(scenario, farm)
        app = create_app(cfg)
        with TestClient(app, raise_server_exceptions=False) as client:
            service = app.state.service
            yield ContractEnv(
                farm=farm,
                client=client,
                service=service,
                probe=ServiceProbe(service),
            )


def drive(client: TestClient, request: dict[str, Any]) -> DriveResult:
    """Execute one vector request dict against the app."""
    path = request["path"]
    if path not in PATHS:
        raise AssertionError(f"unknown contract path {path!r}")
    body = dict(request.get("body", {}))
    if path in _STREAMING:
        body["stream"] = True
    headers = {str(k): str(v) for k, v in request.get("headers", {}).items()}
    resp = client.post(_ROUTE[path], json=body, headers=headers)
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        frames = [f for f in resp.text.split("\n\n") if f.strip()]
        return DriveResult(status=resp.status_code, body=None, frames=frames)
    parsed: Any
    try:
        parsed = resp.json()
    except ValueError:
        parsed = resp.text
    return DriveResult(status=resp.status_code, body=parsed, frames=None)
