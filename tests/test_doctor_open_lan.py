"""Doctor behavior for open trusted-LAN swarm (no cluster token)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import netllm_cli.main as cli_main
import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig, save_config
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_provider_scan(monkeypatch: pytest.MonkeyPatch):
    async def _empty(cfg: NetllmConfig) -> list[dict[str, Any]]:
        return [{"name": "ollama", "status": "online", "models": ["m"]}]

    monkeypatch.setattr(cli_main, "scan_local_providers", _empty)


def test_doctor_open_lan_ok_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    save_config(cfg, cfg_path)

    monkeypatch.setattr(cli_main, "mdns_available", lambda: True)
    monkeypatch.setattr(
        "netllm_discovery.runtime.check_listen_port",
        lambda _cfg: None,
    )
    monkeypatch.setattr(
        "netllm_discovery.lan.local_lan_ip",
        lambda: "192.168.1.5",
    )
    monkeypatch.setattr(
        "netllm_discovery.lan.browse_mdns_peers",
        lambda timeout_s=1.0: [{"agent_id": cfg.agent.agent_id}],
    )

    result = runner.invoke(
        cli_main.app,
        ["doctor", "--json", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    titles = [issue["title"] for issue in payload["issues"]]
    assert "LAN exposure without cluster token" not in titles
    assert any("open" in note.lower() for note in payload.get("notes", []))


def test_doctor_endpoint_open_lan_note(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermetic: the agent's own scan + health probe must not touch live
    # local providers (a real LM Studio requiring auth would otherwise
    # add a legitimate doctor issue and flip ok to False).
    async def _fake_agent_scan(cfg: NetllmConfig) -> list[dict[str, Any]]:
        return [
            {
                "id": "ollama",
                "base_url": "http://127.0.0.1:59999/v1",
                "status": "online",
                "models": ["m"],
                "model_count": 1,
            }
        ]

    monkeypatch.setattr("netllm_agent.service.scan_local_providers", _fake_agent_scan)
    monkeypatch.setattr(
        "netllm_core.pool.probe_openai_compat_sync",
        lambda *a, **k: {"status": "online", "models": ["m"], "model_count": 1},
    )
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    with TestClient(create_app(cfg)) as client:
        data = client.get("/netllm/v1/doctor").json()
    titles = [issue["title"] for issue in data["issues"]]
    assert "LAN exposure without cluster token" not in titles
    assert data["ok"] is True
    assert any("open" in note.lower() for note in data.get("notes", []))


# --- F-13 / F-14: what a cluster token actually protects -------------------


def test_token_set_but_inference_open_is_a_doctor_issue() -> None:
    """A cluster token secures gossip and remote admin but leaves /v1/* open
    to the LAN until require_token_for_inference is also set — which is not
    what anyone reads "--secure" to mean. Existing configs get told rather
    than silently rewritten."""
    from netllm_agent.admin import doctor_payload
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.cluster_token = "tok"
    cfg.swarm.require_token_for_inference = False

    payload = doctor_payload(cfg, AgentService(cfg))
    titles = [i["title"] for i in payload["issues"]]
    assert any("inference is open" in t for t in titles)


def test_no_inference_issue_once_the_flag_is_set() -> None:
    from netllm_agent.admin import doctor_payload
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.cluster_token = "tok"
    cfg.swarm.require_token_for_inference = True

    payload = doctor_payload(cfg, AgentService(cfg))
    titles = [i["title"] for i in payload["issues"]]
    assert not any("inference is open" in t for t in titles)


def test_secure_swarm_init_requires_token_for_inference() -> None:
    """`init --swarm --secure` must mean what it says."""
    import netllm_cli.main as cli_main

    cfg = NetllmConfig()
    cli_main._apply_secured_swarm_mode(cfg)
    assert cfg.swarm.cluster_token
    assert cfg.swarm.require_token_for_inference is True


def test_open_swarm_init_does_not_require_token_for_inference() -> None:
    """The open trusted-LAN path is unchanged — no token, no gate."""
    import netllm_cli.main as cli_main

    cfg = NetllmConfig()
    cli_main._apply_open_swarm_mode(cfg)
    assert not cfg.swarm.cluster_token
    assert cfg.swarm.require_token_for_inference is False


# --- F-13: read endpoints leak the whole fleet to the LAN ------------------


def _token_cfg() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.cluster_token = "cluster-tok"
    return cfg


_READ_ROUTES = (
    "/netllm/v1/status",
    "/netllm/v1/peers",
    "/netllm/v1/backends",
    "/netllm/v1/telemetry",
)


@pytest.mark.parametrize("route", _READ_ROUTES)
def test_read_routes_require_token_from_remote_client(route: str) -> None:
    """/netllm/v1/status returns every backend URL, the full model catalog,
    hostnames, agent ids, the peer list, routed counts and token totals —
    complete fleet reconnaissance for anyone on the LAN."""
    with TestClient(create_app(_token_cfg()), client=("10.9.9.9", 5555)) as client:
        assert client.get(route).status_code == 401


@pytest.mark.parametrize("route", _READ_ROUTES)
def test_read_routes_accept_the_cluster_token(route: str) -> None:
    """Peer discovery keeps working: fetch_peer and fetch_agent_status
    already send the token whenever one is configured."""
    with TestClient(create_app(_token_cfg()), client=("10.9.9.9", 5555)) as client:
        resp = client.get(route, headers={"Authorization": "Bearer cluster-tok"})
        assert resp.status_code == 200


@pytest.mark.parametrize("route", _READ_ROUTES)
def test_read_routes_open_when_no_token_configured(route: str) -> None:
    """Zero-config single-machine and open trusted-LAN setups are unchanged."""
    cfg = _token_cfg()
    cfg.swarm.cluster_token = ""
    with TestClient(create_app(cfg), client=("10.9.9.9", 5555)) as client:
        assert client.get(route).status_code == 200


@pytest.mark.parametrize("route", _READ_ROUTES)
def test_read_routes_open_for_local_clients_even_with_a_token(route: str) -> None:
    """The dashboard on the machine itself must not need the token."""
    with TestClient(create_app(_token_cfg())) as client:
        assert client.get(route).status_code == 200
