"""Admin API + config_summary coverage for the [cloud] section."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from netllm_agent.admin import (
    cloud_provider_registry_payload,
    config_summary,
    doctor_payload,
)
from netllm_agent.app import create_app
from netllm_agent.service import AgentService
from netllm_core.cloud_providers import CLOUD_PROVIDERS
from netllm_core.models import (
    CloudProviderConfig,
    NetllmConfig,
    load_config,
    save_config,
)


def test_config_summary_includes_cloud_registry_metadata() -> None:
    cfg = NetllmConfig()
    summary = config_summary(cfg)
    assert "cloud" in summary
    assert summary["cloud"]["enabled"] is True
    assert summary["cloud"]["fallback"] == "cloud"
    providers = summary["cloud"]["providers"]
    assert set(providers) == {"moonshot", "zai", "openai", "anthropic", "openrouter"}
    assert providers["moonshot"]["display_name"] == "Moonshot AI (Kimi)"
    assert providers["moonshot"]["enabled"] is False
    assert providers["moonshot"]["api_key_set"] is False


def test_config_summary_never_returns_raw_key() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, api_key="mk-super-secret"
    )
    summary = config_summary(cfg)
    dumped = str(summary)
    assert "mk-super-secret" not in dumped
    assert summary["cloud"]["providers"]["moonshot"]["api_key_set"] is True


def test_admin_config_save_enables_provider_and_stores_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "cloud": {
                        "enabled": True,
                        "fallback": "local",
                        "providers": {
                            "moonshot": {"enabled": True, "api_key": "mk-inline"}
                        },
                    }
                },
            )
            assert resp.status_code == 200, resp.text
            reloaded = load_config(cfg_path)
            assert reloaded.cloud.fallback == "local"
            assert reloaded.cloud.providers["moonshot"].enabled is True
            assert reloaded.cloud.providers["moonshot"].api_key == "mk-inline"

            summary = client.get("/netllm/v1/config").json()
            assert summary["cloud"]["providers"]["moonshot"]["api_key_set"] is True
            assert "mk-inline" not in str(summary)


def test_admin_config_save_preserves_key_when_omitted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.swarm.mdns = False
        cfg.agent.advertise = False
        cfg.cloud.providers["moonshot"] = CloudProviderConfig(
            enabled=True, api_key="mk-original"
        )
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            # Re-save without api_key — must not blank the stored key.
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "cloud": {"providers": {"moonshot": {"region": "cn"}}},
                },
            )
            assert resp.status_code == 200, resp.text
            reloaded = load_config(cfg_path)
            assert reloaded.cloud.providers["moonshot"].api_key == "mk-original"
            assert reloaded.cloud.providers["moonshot"].region == "cn"


def test_doctor_flags_enabled_provider_without_key() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    service = AgentService(cfg)
    payload = doctor_payload(cfg, service)
    assert any("Moonshot" in issue["title"] for issue in payload["issues"])


def test_doctor_does_not_flag_provider_with_key(monkeypatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-env")
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    service = AgentService(cfg)
    payload = doctor_payload(cfg, service)
    assert not any("Moonshot" in issue["title"] for issue in payload["issues"])


def test_doctor_ignores_disabled_master_switch() -> None:
    cfg = NetllmConfig()
    cfg.cloud.enabled = False
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    service = AgentService(cfg)
    payload = doctor_payload(cfg, service)
    assert not any("Moonshot" in issue["title"] for issue in payload["issues"])


# --- agent-served cloud provider registry (schema drift fix) -------------


def test_cloud_provider_registry_payload_covers_all_providers() -> None:
    rows = cloud_provider_registry_payload()
    ids = {row["id"] for row in rows}
    assert ids == set(CLOUD_PROVIDERS)
    for row in rows:
        spec = CLOUD_PROVIDERS[row["id"]]
        assert row["display_name"] == spec.display_name
        assert row["notes"] == spec.notes
        assert row["regions"] == list(spec.endpoints.keys())
        assert row["auth_modes"] == list(spec.auth_modes)
        assert row["default_api_format"] == spec.default_api_format
        assert row["api_key_env"] == spec.api_key_env


def test_cloud_providers_endpoint_serves_registry() -> None:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as client:
        resp = client.get("/netllm/v1/cloud/providers")
    assert resp.status_code == 200
    body = resp.json()
    ids = {row["id"] for row in body["providers"]}
    assert ids == {"moonshot", "zai", "openai", "anthropic", "openrouter"}
    moonshot = next(r for r in body["providers"] if r["id"] == "moonshot")
    assert moonshot["display_name"] == "Moonshot AI (Kimi)"
    assert "global" in moonshot["regions"]
