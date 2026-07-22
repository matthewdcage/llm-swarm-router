"""Dashboard's generic schema-driven form renderer (phase 2 of
docs/config-schema-rewrite-plan.md) — the `ui` tab, migrated first."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.config_schema import config_schema_document
from netllm_core.models import NetllmConfig

STATIC_DIR = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "netllm-agent"
    / "src"
    / "netllm_agent"
    / "static"
)


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_dashboard_js_serves_generic_schema_renderer(client: TestClient) -> None:
    resp = client.get("/ui/dashboard.js")
    assert resp.status_code == 200
    body = resp.text
    assert "function renderSchemaForm" in body
    assert "function renderSchemaField" in body
    # ui is the phase-2 pilot section: migrated to the generic renderer,
    # sourced from the fetched schema rather than hand-built widgets.
    assert 'renderSchemaForm("ui", state.configSchema' in body
    assert "loadConfigSchema" in body
    assert "/netllm/v1/config/schema" in body


def test_dashboard_js_syntax_is_valid() -> None:
    js_path = STATIC_DIR / "dashboard.js"
    result = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_schema_endpoint_matches_default_ui_config(client: TestClient) -> None:
    """The pilot section's schema-driven defaults must match the pydantic
    defaults a fresh NetllmConfig actually produces — otherwise the
    dashboard's emptyConfigDraft() fallback would silently diverge from
    server-side defaults, exactly the drift class this rewrite targets."""
    resp = client.get("/netllm/v1/config/schema")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc == config_schema_document()
    ui_defaults = {f["name"]: f["default"] for f in doc["sections"]["ui"]["fields"]}
    assert ui_defaults == {
        "auto_start_on_launch": True,
        "log_dir": "",
        "check_for_updates_automatically": True,
    }
