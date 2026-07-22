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
    "/v1/embeddings",
    "/v1/messages",
    "/netllm/v1/status",
    "/netllm/v1/version",
    "/netllm/v1/update/check",
    "/netllm/v1/doctor",
    "/netllm/v1/config",
    "/netllm/v1/client-env",
    "/netllm/v1/admin/discover",
    "/netllm/v1/heartbeat",
    "/netllm/v1/peers",
    "/netllm/v1/backends",
}

# Strategies that existing user configs may reference; removing any is breaking.
LEGACY_ROUTING_STRATEGIES = (
    "failover",
    "round_robin",
    "local_first",
    "least_load",
    "latency_weighted",
    "batch_shard",
)


def test_default_listen_address() -> None:
    cfg = NetllmConfig()
    assert cfg.agent.listen == "127.0.0.1:11400"


def test_default_config_behavior_unchanged() -> None:
    """Existing installs keep single-machine semantics: loopback bind,
    local_first routing, no cluster token, peer role, mDNS on."""
    cfg = NetllmConfig()
    assert cfg.routing.default_strategy == "local_first"
    assert cfg.routing.allow_remote is True
    assert cfg.swarm.cluster_token == ""
    assert cfg.agent.role == "peer"
    assert cfg.agent.advertise is True
    assert cfg.swarm.mdns is True
    assert cfg.swarm.subnet_scan is False


def test_legacy_routing_strategies_still_accepted() -> None:
    from netllm_core.models import RoutingConfig

    for strategy in LEGACY_ROUTING_STRATEGIES:
        cfg = RoutingConfig(default_strategy=strategy)
        assert cfg.default_strategy == strategy


def test_init_non_tty_writes_single_machine_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`netllm init` without a TTY must never prompt and must keep the
    current single-machine defaults (loopback listen, local_first)."""
    import netllm_cli.main as cli_main
    from typer.testing import CliRunner

    async def _no_providers(cfg: NetllmConfig) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(cli_main, "scan_local_providers", _no_providers)
    cfg_path = tmp_path / "config.toml"
    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        ["init", "--config", str(cfg_path), "--no-global-cli"],
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.agent.listen == "127.0.0.1:11400"
    assert cfg.routing.default_strategy == "local_first"
    assert cfg.swarm.cluster_token == ""


def test_save_config_handles_optional_none_fields(tmp_path: Path) -> None:
    """A backend override without api_format (None) must round-trip —
    TOML has no null, so save_config strips None leaves."""
    from netllm_core.models import BackendOverride, RoutingPolicy

    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(base_url="http://127.0.0.1:18081/v1", provider="custom")
    ]
    cfg.routing.policies = [RoutingPolicy(name="p1")]
    out = tmp_path / "config.toml"
    save_config(cfg, out)
    reloaded = load_config(out)
    assert reloaded.routing.backends[0].api_format is None
    assert reloaded.routing.policies[0].api_format is None
    assert reloaded.routing.policies[0].strategy is None


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


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Swift config defaults are macOS-only",
)
def test_darwin_swift_default_providers_match_python() -> None:
    """Lock Swift Settings defaults to Python discovery.providers on Darwin.

    discovery.providers moved from a NetllmConfigDocument.DiscoverySection
    default to SettingsViewModel.providers (docs/config-schema-rewrite-plan.md
    §5 phase 4 — discovery became a dynamic [String: JSONValue] section;
    the providers checkbox loop still needs a known list to iterate,
    which now lives on the view model instead of a typed struct default).
    """
    doc_path = REPO_ROOT / "apps/netllm-mac/Sources/AppView/SettingsViewModel.swift"
    text = doc_path.read_text(encoding="utf-8")
    marker = "static let providers = ["
    swift_defaults: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if marker not in stripped:
            continue
        inner = stripped.split(marker, 1)[1].split("]", 1)[0]
        swift_defaults = [
            part.strip().strip('"').strip("'")
            for part in inner.split(",")
            if part.strip()
        ]
        break
    assert swift_defaults == default_discovery_providers()


def test_fastapi_routes_registered() -> None:
    from netllm_agent.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    for path in EXPECTED_HTTP_ROUTES:
        assert path in paths


def test_install_method_darwin_channels() -> None:
    from netllm_cli.install_detect import get_install_method

    core = "netllm_core.install_detect"
    with patch(f"{core}.is_app_bundle", return_value=True):
        assert get_install_method() == "app"
    with patch(f"{core}.is_app_bundle", return_value=False):
        with patch(f"{core}.is_homebrew", return_value=True):
            assert get_install_method() == "homebrew"
    with patch(f"{core}.is_app_bundle", return_value=False):
        with patch(f"{core}.is_homebrew", return_value=False):
            with patch(f"{core}.is_linux_systemd", return_value=False):
                with patch(f"{core}.is_windows_service", return_value=False):
                    assert get_install_method() == "source"


def test_install_method_windows_service() -> None:
    from netllm_cli.install_detect import get_install_method

    core = "netllm_core.install_detect"
    with patch(f"{core}.is_app_bundle", return_value=False):
        with patch(f"{core}.is_homebrew", return_value=False):
            with patch(f"{core}.is_linux_systemd", return_value=False):
                with patch(f"{core}.is_windows_service", return_value=True):
                    assert get_install_method() == "windows-service"


def test_status_payload_contract_keys() -> None:
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app
    from netllm_core.models import NetllmConfig

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    with TestClient(create_app(cfg)) as client:
        data = client.get("/netllm/v1/status").json()
    for key in (
        "agent_id",
        "hostname",
        "role",
        "listen_url",
        "backends",
        "peers",
        "routing_strategy",
    ):
        assert key in data


def test_heartbeat_payload_contract() -> None:
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app
    from netllm_core.models import NetllmConfig

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    with TestClient(create_app(cfg)) as client:
        resp = client.post(
            "/netllm/v1/heartbeat",
            json={
                "agent_id": "remote-peer",
                "listen_url": "http://192.168.1.50:11400",
                "role": "peer",
                "hostname": "worker",
                "backends": [],
            },
        )
    assert resp.status_code == 204


def test_heartbeat_accepts_legacy_v03_backend_rows() -> None:
    """Old peers (v0.3.x) send backend rows without any newer optional
    fields — mixed-version swarms must keep working."""
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app
    from netllm_core.models import NetllmConfig

    legacy_backend = {
        "id": "omlx:http://127.0.0.1:8080/v1",
        "base_url": "http://127.0.0.1:8080/v1",
        "provider": "omlx",
        "health": {"status": "online", "models": ["mlx-model"]},
    }
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    with TestClient(create_app(cfg)) as client:
        resp = client.post(
            "/netllm/v1/heartbeat",
            json={
                "agent_id": "old-peer",
                "listen_url": "http://192.168.1.51:11400",
                "role": "peer",
                "hostname": "legacy",
                "backends": [legacy_backend],
            },
        )
        assert resp.status_code == 204
        peers = client.get("/netllm/v1/peers").json()["peers"]
    assert any(p["agent_id"] == "old-peer" for p in peers)


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
