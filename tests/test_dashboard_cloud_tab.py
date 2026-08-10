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


def shipped_js() -> list[Path]:
    """Every JavaScript file the agent serves, core and pages."""
    files = sorted(STATIC_DIR.glob("*.js")) + sorted(STATIC_DIR.glob("pages/*.js"))
    assert len(files) > 12, f"only found {len(files)} JS files — did they move?"
    return files


def test_index_html_has_cloud_nav_item_and_panel(client: TestClient) -> None:
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert 'data-page="cloud"' in resp.text
    assert 'id="page-cloud"' in resp.text


def test_dashboard_serves_the_cloud_page_renderer(client: TestClient) -> None:
    """The Cloud tab became `pages/cloud.js`, registered against the router.

    Both halves are asserted over the *served* bytes, not the repo files: a
    page module that exists on disk but is not reachable under /ui/ (missing
    from the static mount, or missing its <script> tag) is the same outage as
    a deleted renderer.
    """
    page = client.get("/ui/pages/cloud.js")
    assert page.status_code == 200
    assert "function renderCloudPage(" in page.text
    assert 'registerPage("cloud", renderCloudPage)' in page.text

    index = client.get("/ui/")
    assert "pages/cloud.js" in index.text, "cloud.js is never loaded by index.html"

    # The patch builder and the offline provider roster stayed in the core
    # module — cloud.js deliberately carries no provider id literal.
    core = client.get("/ui/dashboard.js")
    assert core.status_code == 200
    assert "buildCloudPatch" in core.text
    assert '"cloud"' in core.text, "cloud is not in the PAGES roster"
    for provider_id in (
        "moonshot",
        "zai",
        "openai",
        "anthropic",
        "openrouter",
        "dashscope",
    ):
        assert provider_id in core.text


def test_every_shipped_js_file_parses() -> None:
    """Catches JS syntax errors without requiring a browser.

    Widened from `dashboard.js` to every module the split produced: the pages
    load as plain <script> tags into one shared global scope, so a syntax
    error in any one of them takes the dashboard down exactly as it did when
    there was a single file.
    """
    import subprocess

    broken = []
    for js_path in shipped_js():
        result = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            broken.append(f"{js_path.name}: {result.stderr.strip()}")
    assert not broken, "\n".join(broken)


def test_cloud_patch_never_sends_key_when_not_provided(tmp_path: Path) -> None:
    """buildCloudPatch's write-only contract, exercised end to end: a
    provider entry saved without a key does not blank a previously
    stored one (mirrors the admin API's own preserve-on-omit test, but
    verifies the JS-shaped payload the admin endpoint actually receives
    from the dashboard save button matches what save-preserving expects).

    The provider starts verified and enabled, which is now the only way it
    can legitimately be either: the point under test is that a save carrying
    no `api_key` neither blanks the stored key nor invalidates the check that
    key passed."""
    from netllm_core.cloud_verification import key_fingerprint
    from netllm_core.models import CloudProviderConfig, save_config

    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True,
        api_key="mk-stored",
        verified_status="ok",
        verified_at="2026-08-10T00:00:00+00:00",
        verified_key_fingerprint=key_fingerprint("mk-stored"),
    )
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
    assert summary["cloud"]["providers"]["moonshot"]["api_key_set"] is True
    assert "mk-stored" not in str(summary)
    # The omitted key kept its verification: a save that touches nothing
    # about the credential must not knock the provider back to "unverified".
    assert summary["cloud"]["providers"]["moonshot"]["verification"]["ok"] is True
