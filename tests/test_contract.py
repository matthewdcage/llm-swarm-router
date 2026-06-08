"""Contract tests — lock non-breaking invariants for HTTP, config, and install paths."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from netllm_core.models import NetllmConfig, load_config, save_config
from netllm_core.platform import default_discovery_providers, default_log_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.toml"

EXPECTED_HTTP_ROUTES = {
    "/",
    "/health",
    "/metrics",
    "/v1/models",
    "/v1/chat/completions",
    "/v1/messages",
    "/netllm/v1/status",
    "/netllm/v1/doctor",
    "/netllm/v1/config",
    "/netllm/v1/client-env",
    "/netllm/v1/admin/discover",
}


def test_default_listen_address() -> None:
    cfg = NetllmConfig()
    assert cfg.agent.listen == "127.0.0.1:11400"


def test_config_example_roundtrip(tmp_path: Path) -> None:
    assert CONFIG_EXAMPLE.is_file()
    cfg = load_config(CONFIG_EXAMPLE)
    out = tmp_path / "config.toml"
    save_config(cfg, out)
    reloaded = load_config(out)
    assert reloaded.discovery.providers == cfg.discovery.providers
    assert reloaded.agent.listen == cfg.agent.listen


def test_provider_ids_accept_legacy_values() -> None:
    cfg = NetllmConfig()
    cfg.discovery.providers = ["omlx", "ollama", "lmstudio", "custom", "vllm"]
    assert cfg.discovery.providers[0] == "omlx"


def test_darwin_default_log_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    path = default_log_dir()
    assert "Library" in str(path)
    assert "Application Support" in str(path)
    assert path.name == "logs"


def test_linux_default_providers_exclude_omlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    providers = default_discovery_providers()
    assert "omlx" not in providers
    assert "vllm" in providers
    assert "ollama" in providers


def test_darwin_default_providers_include_omlx_and_vllm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    providers = default_discovery_providers()
    assert "omlx" in providers
    assert "vllm" in providers


def test_fastapi_routes_registered() -> None:
    from netllm_agent.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    for path in EXPECTED_HTTP_ROUTES:
        assert path in paths


def test_install_method_darwin_channels() -> None:
    from netllm_cli.install_detect import get_install_method

    with patch("netllm_cli.install_detect.is_app_bundle", return_value=True):
        assert get_install_method() == "app"
    with patch("netllm_cli.install_detect.is_app_bundle", return_value=False):
        with patch("netllm_cli.install_detect.is_homebrew", return_value=True):
            assert get_install_method() == "homebrew"
    with patch("netllm_cli.install_detect.is_app_bundle", return_value=False):
        with patch("netllm_cli.install_detect.is_homebrew", return_value=False):
            with patch(
                "netllm_cli.install_detect.is_linux_systemd",
                return_value=False,
            ):
                with patch(
                    "netllm_cli.install_detect.is_windows_service", return_value=False
                ):
                    assert get_install_method() == "source"


def test_install_method_windows_service() -> None:
    from netllm_cli.install_detect import get_install_method

    with patch("netllm_cli.install_detect.is_app_bundle", return_value=False):
        with patch("netllm_cli.install_detect.is_homebrew", return_value=False):
            with patch(
                "netllm_cli.install_detect.is_linux_systemd",
                return_value=False,
            ):
                with patch(
                    "netllm_cli.install_detect.is_windows_service",
                    return_value=True,
                ):
                    assert get_install_method() == "windows-service"


def test_ui_route_serves_dashboard() -> None:
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app
    from netllm_core.models import NetllmConfig

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    with TestClient(create_app(cfg)) as client:
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert "dashboard" in resp.text.lower()
        assert "llm-swarm-router" in resp.text.lower()
