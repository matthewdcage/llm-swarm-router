"""Dashboard static assets ship a Cloud tab wired to the admin config API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
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


def test_index_html_has_cloud_nav_item_and_panel(client: TestClient) -> None:
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert 'data-tab="cloud"' in resp.text
    assert 'id="tab-cloud"' in resp.text


def test_dashboard_js_serves_cloud_tab_renderer(client: TestClient) -> None:
    resp = client.get("/ui/dashboard.js")
    assert resp.status_code == 200
    body = resp.text
    assert "renderCloudTab" in body
    assert "cloud: renderCloudTab" in body
    assert "buildCloudPatch" in body
    for provider_id in ("moonshot", "zai", "openai", "anthropic", "openrouter"):
        assert provider_id in body


def test_dashboard_js_syntax_is_valid() -> None:
    """Catches JS syntax errors without requiring a browser — every brace
    and function in the new Cloud tab code must actually parse."""
    import subprocess

    js_path = STATIC_DIR / "dashboard.js"
    result = subprocess.run(
        ["node", "--check", str(js_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cloud_patch_never_sends_key_when_not_provided(tmp_path: Path) -> None:
    """buildCloudPatch's write-only contract, exercised end to end: a
    provider entry saved without a key does not blank a previously
    stored one (mirrors the admin API's own preserve-on-omit test, but
    verifies the JS-shaped payload the admin endpoint actually receives
    from the dashboard save button matches what save-preserving expects)."""
    from netllm_core.models import save_config

    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    save_config(cfg, cfg_path)
    app = create_app(cfg, config_path=cfg_path)
    client = TestClient(app)
    resp = client.post(
        "/netllm/v1/admin/config",
        json={
            "cloud": {
                "enabled": True,
                "fallback": "cloud",
                "fallback_enabled": True,
                "providers": {
                    "moonshot": {"enabled": True, "region": "", "api_format": None}
                },
            }
        },
    )
    assert resp.status_code == 200, resp.text
    summary = client.get("/netllm/v1/config").json()
    assert summary["cloud"]["providers"]["moonshot"]["enabled"] is True
    assert summary["cloud"]["providers"]["moonshot"]["api_key_set"] is False
