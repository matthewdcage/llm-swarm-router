"""Dashboard's generic schema-driven form renderer (phases 2-3 of
docs/config-schema-rewrite-plan.md) — ui first, then discovery/swarm/
routing/cloud."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.config_schema import config_schema_document
from netllm_core.models import NetllmConfig, load_config, save_config

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


# test_dashboard_js_serves_generic_schema_renderer and
# test_dashboard_js_serves_remaining_sections_generic_widgets moved to
# tests/conformance/kit_config_surfaces.py (Axis D). They asserted the same
# renderer markers this file greps for, but the kit asserts them *per config
# subtree*, with a source location, and fails when a field stops being covered
# rather than only when a string disappears -- see
# test_the_generic_schema_machinery_exists_on_both_surfaces and the
# `schema_rendered` evidence assertion in `disposition()`. Two systems asking
# the same question is the duplication PROGRAM.md 1 exists to stop.


def test_dashboard_js_syntax_is_valid() -> None:
    js_path = STATIC_DIR / "dashboard.js"
    result = subprocess.run(
        ["node", "--check", str(js_path)], capture_output=True, text=True
    )
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
    assert ui_defaults == NetllmConfig().ui.model_dump()


def test_admin_config_round_trips_routing_policies_backends_and_pools() -> None:
    """End-to-end: a patch shaped exactly like buildConfigPatch()'s output
    for a routing policy + backend override + model pool (verified
    manually against the schema-driven widgets in the browser) persists
    and reloads correctly — including the read_only backends[].cloud_provider
    field being absent from what the UI would ever send, and the
    write_only backends[].api_key being empty (nothing "pending" typed)."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as test_client:
            resp = test_client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "default_strategy": "local_first",
                        "allow_remote": True,
                        "require_same_model_for_shard": True,
                        "max_in_flight_per_backend": 0,
                        "follow_gateway": True,
                        "spillover_max_local_in_flight": 2,
                        "health_ttl_s": 30,
                        "offline_retry_s": 10,
                        "max_backend_failures": 3,
                        "model_aliases": {},
                        "model_pools": {
                            "gpu-box": {
                                "enabled": True,
                                "hosts": ["mac-studio"],
                                "models": ["qwen2.5:72b-instruct"],
                            }
                        },
                        "backends": [
                            {
                                "base_url": "http://10.0.0.5:11434/v1",
                                "provider": "ollama",
                                "api_format": None,
                                "enabled": True,
                                "local": True,
                                "max_concurrency": 0,
                            }
                        ],
                        "policies": [
                            {
                                "name": "test-policy",
                                "model_prefix": "",
                                "api_format": "openai",
                                "strategy": None,
                                "prefer_provider": None,
                                "allow_cloud": False,
                                "enabled": True,
                            }
                        ],
                    }
                },
            )
            assert resp.status_code == 200, resp.text

        reloaded = load_config(cfg_path)
        assert reloaded.routing.default_strategy == "local_first"
        assert reloaded.routing.model_pools["gpu-box"].hosts == ["mac-studio"]
        assert reloaded.routing.model_pools["gpu-box"].models == [
            "qwen2.5:72b-instruct"
        ]
        assert reloaded.routing.backends[0].base_url == "http://10.0.0.5:11434/v1"
        assert reloaded.routing.backends[0].cloud_provider == ""
        assert reloaded.routing.policies[0].name == "test-policy"
