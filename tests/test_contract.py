"""Contract tests — lock non-breaking invariants for HTTP, config, and install paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from netllm_core.models import NetllmConfig, load_config, save_config
from netllm_core.platform import default_discovery_providers, default_log_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.toml"

# The HTTP surface lives in a generated manifest, asserted as an exact set —
# see scripts/generate-routes-json.py for why presence-only was not enough.
ROUTES_MANIFEST = REPO_ROOT / "tests/contract/routes.json"
FRAMEWORK_PATHS = frozenset(
    {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)

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
    from netllm_cli.commands import init_install as cli_init_install
    from typer.testing import CliRunner

    async def _no_providers(cfg: NetllmConfig) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(cli_init_install, "scan_local_providers", _no_providers)
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


def test_swift_default_providers_match_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock Swift Settings defaults to Python discovery.providers on Darwin.

    discovery.providers moved from a NetllmConfigDocument.DiscoverySection
    default to SettingsViewModel.providers (docs/config-schema-rewrite-plan.md
    §5 phase 4 — discovery became a dynamic [String: JSONValue] section;
    the providers checkbox loop still needs a known list to iterate,
    which now lives on the view model instead of a typed struct default).

    This used to be ``skipif(sys.platform != "darwin")`` and so ran on exactly
    one CI job. It reads a checked-in .swift file as text and compares it to a
    Python list — no Xcode, no macOS. The platform is pinned instead, which is
    what the assertion actually needed (docs/extending/PROGRAM.md §2, 0b.5).
    """
    monkeypatch.setattr(sys, "platform", "darwin")
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


def test_cloud_provider_api_key_env_is_derivable() -> None:
    """`KeychainStore.CloudKeyEnv.defaultEnvVar` derives the env var from the
    id, so the macOS app can export a key for a provider its binary predates.

    That derivation is only sound while every registry entry agrees with it.
    A provider whose vendor env var is spelled differently is allowed — it
    just may not rely on the offline fallback, so add it to the Swift
    bootstrap roster below and say so here.
    """
    from netllm_core.cloud_providers import CLOUD_PROVIDERS

    for provider_id, spec in CLOUD_PROVIDERS.items():
        assert spec.api_key_env == f"{provider_id.upper()}_API_KEY", (
            f"{provider_id} names {spec.api_key_env}; the Swift fallback would "
            f"export {provider_id.upper()}_API_KEY and the key would 401"
        )


def test_swift_cloud_key_env_has_no_hardcoded_table() -> None:
    """PythonRuntime may not restate the api_key_env mapping.

    It held a closed five-entry `[(account, envVar)]` list, which is why a
    provider added everywhere else stored its key and never injected it. The
    mapping is derived now; this is the projection that keeps it derived,
    and it runs on Linux because it reads the .swift file as text.
    """
    runtime = REPO_ROOT / "apps/netllm-mac/Sources/Server/PythonRuntime.swift"
    code = [
        line
        for line in runtime.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    ]
    offenders = [line.strip() for line in code if "_API_KEY" in line]
    assert not offenders, f"PythonRuntime.swift restates api_key_env: {offenders}"


def test_swift_cloud_bootstrap_roster_matches_registry() -> None:
    """The offline provider roster in Swift must match CLOUD_PROVIDERS.

    Ledgered as a mirror in tests/conformance/ledgers/mirrors.toml (expires
    phase-4, when it becomes generated). Until then it is projection-tested
    rather than merely hoped about.
    """
    from netllm_core.cloud_providers import CLOUD_PROVIDERS

    text = (REPO_ROOT / "apps/netllm-mac/Sources/Config/KeychainStore.swift").read_text(
        encoding="utf-8"
    )
    marker = "static let bootstrapProviderIDs = ["
    _, _, rest = text.partition(marker)
    assert rest, f"{marker} not found — did the roster move?"
    inner, _, _ = rest.partition("]")
    swift_ids = [part.strip().strip('"') for part in inner.split(",") if part.strip()]
    assert set(swift_ids) == set(CLOUD_PROVIDERS)


def test_fastapi_routes_match_generated_manifest() -> None:
    """Exact-set equality against tests/contract/routes.json.

    The predecessor of this test listed 17 of ~28 routes and asserted only
    presence, so deleting ``/v1/responses`` was CI-green — the opposite of the
    "``/v1/*`` is additive only, no sunset" promise the corpus exists to keep.
    Regenerate with ``uv run python scripts/generate-routes-json.py``.
    """
    from netllm_agent.app import create_app

    app = create_app()
    live = {
        (route.path, tuple(sorted(getattr(route, "methods", None) or [])))
        for route in app.routes
        if getattr(route, "path", None) not in FRAMEWORK_PATHS
        and hasattr(route, "path")
    }
    manifest = json.loads(ROUTES_MANIFEST.read_text(encoding="utf-8"))
    recorded = {(row["path"], tuple(row["methods"])) for row in manifest["routes"]}
    assert live == recorded


def test_known_providers_roster() -> None:
    """The local-backend roster, which had zero tests referencing it.

    ``KNOWN_PROVIDERS`` is the discovery-side statement of the same fact that
    ``ProviderId`` states in the type system and ``default_discovery_providers``
    states per platform. Nothing checked they agreed; adding a provider to one
    and not the others is silent. Phase 3 collapses all three into
    ``LocalProviderSpec`` — until then, assert the agreement.
    """
    from typing import get_args

    from netllm_core.models import ProviderId
    from netllm_discovery import KNOWN_PROVIDERS

    ids = [pid for pid, _label, _ports in KNOWN_PROVIDERS]
    assert ids == sorted(set(ids), key=ids.index), "duplicate id in KNOWN_PROVIDERS"

    # Every discoverable provider must be nameable in config. The converse
    # does not hold, and the gap is the interesting part: these three are
    # ProviderIds that no port scan can find — "custom" points routing at an
    # arbitrary base_url, and the two cloud ids reach a vendor over the
    # internet. A fourth entry appearing here is either a new local provider
    # missing from KNOWN_PROVIDERS or a new cloud one that owes this comment
    # a line.
    assert set(ids) <= set(get_args(ProviderId))
    assert set(get_args(ProviderId)) - set(ids) == {"custom", "anthropic", "openai"}

    for pid, label, ports in KNOWN_PROVIDERS:
        assert pid and pid.islower() and pid.isascii()
        assert label.strip(), f"{pid} has no display label"
        assert ports, f"{pid} has no default scan ports"
        assert len(set(ports)) == len(ports), f"{pid} repeats a scan port"
        assert all(1 <= port <= 65535 for port in ports)

    # Every platform default must be a real, discoverable provider.
    for platform in ("darwin", "linux", "win32"):
        with patch.object(sys, "platform", platform):
            assert set(default_discovery_providers()) <= set(ids)


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
