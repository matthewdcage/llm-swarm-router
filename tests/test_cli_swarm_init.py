"""Guided swarm init, join, and swarm-token CLI tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import netllm_cli.main as cli_main
import pytest
from netllm_core.models import NetllmConfig, load_config, save_config
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_provider_scan(monkeypatch: pytest.MonkeyPatch):
    async def _empty(cfg: NetllmConfig) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(cli_main, "scan_local_providers", _empty)


def _init(tmp_path: Path, *args: str):
    cfg_path = tmp_path / "config.toml"
    result = runner.invoke(
        cli_main.app,
        ["init", "--config", str(cfg_path), "--no-global-cli", *args],
    )
    return result, cfg_path


def test_init_swarm_configures_mesh(tmp_path: Path) -> None:
    result, cfg_path = _init(tmp_path, "--swarm")
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.agent.listen == "0.0.0.0:11400"
    assert len(cfg.swarm.cluster_token) >= 24
    assert cfg.routing.default_strategy == "local_spillover"
    assert "netllm join" in result.output


def test_init_single_keeps_defaults(tmp_path: Path) -> None:
    result, cfg_path = _init(tmp_path, "--single")
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.agent.listen == "127.0.0.1:11400"
    assert cfg.swarm.cluster_token == ""
    assert cfg.routing.default_strategy == "local_first"


def test_init_swarm_and_single_conflict(tmp_path: Path) -> None:
    result, _ = _init(tmp_path, "--swarm", "--single")
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_join_writes_swarm_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    save_config(NetllmConfig(), cfg_path)

    fake_status = {
        "agent_id": "gw-1",
        "hostname": "studio",
        "listen_url": "http://192.168.1.20:11400",
        "cluster_token_set": True,
    }
    with (
        patch.object(cli_main, "_fetch_join_status", return_value=fake_status),
        patch.object(cli_main, "_validate_join_token") as mock_validate,
    ):
        result = runner.invoke(
            cli_main.app,
            [
                "join",
                "http://192.168.1.20:11400",
                "--token",
                "secret-token",
                "--config",
                str(cfg_path),
            ],
        )
    assert result.exit_code == 0, result.output
    mock_validate.assert_called_once()
    cfg = load_config(cfg_path)
    assert cfg.agent.listen == "0.0.0.0:11400"
    assert cfg.swarm.cluster_token == "secret-token"
    assert cfg.routing.default_strategy == "local_spillover"
    assert "http://192.168.1.20:11400" in cfg.swarm.peers


def test_join_rejects_token_against_open_swarm(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    save_config(NetllmConfig(), cfg_path)
    fake_status = {"agent_id": "gw-1", "cluster_token_set": False}
    with patch.object(cli_main, "_fetch_join_status", return_value=fake_status):
        result = runner.invoke(
            cli_main.app,
            [
                "join",
                "192.168.1.20",
                "--token",
                "secret-token",
                "--config",
                str(cfg_path),
            ],
        )
    assert result.exit_code == 1
    assert "no cluster token" in result.output


def test_join_rejects_own_url(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    save_config(cfg, cfg_path)
    fake_status = {"agent_id": "self", "cluster_token_set": True}
    with (
        patch.object(cli_main, "_fetch_join_status", return_value=fake_status),
        patch.object(cli_main, "_validate_join_token"),
        patch(
            "netllm_discovery.lan.local_lan_ip",
            return_value="192.168.1.5",
        ),
    ):
        result = runner.invoke(
            cli_main.app,
            [
                "join",
                "http://192.168.1.5:11400",
                "--token",
                "tok",
                "--config",
                str(cfg_path),
            ],
        )
    assert result.exit_code == 1
    assert "own agent URL" in result.output


def test_normalize_agent_url() -> None:
    norm = cli_main._normalize_agent_url
    assert norm("192.168.1.20") == "http://192.168.1.20:11400"
    assert norm("http://192.168.1.20:11400/") == "http://192.168.1.20:11400"
    assert norm("studio.local:11400") == "http://studio.local:11400"
    assert norm("http://[fe80::1]") == "http://[fe80::1]:11400"
    assert norm("http://[fe80::1]:11400") == "http://[fe80::1]:11400"


def test_listen_port_of_handles_ipv6_and_bare_hosts() -> None:
    port_of = cli_main._listen_port_of
    assert port_of("127.0.0.1:11400") == "11400"
    assert port_of("0.0.0.0:12000") == "12000"
    assert port_of("[::1]:11400") == "11400"
    assert port_of("::1") == "11400"
    assert port_of("localhost") == "11400"


def test_validate_join_token_rejects_server_error() -> None:
    class FakeResp:
        status_code = 500

    class FakeClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def post(self, *a: object, **k: object) -> FakeResp:
            return FakeResp()

    import pytest as _pytest
    import typer as _typer

    with patch.object(cli_main.httpx, "Client", FakeClient):
        with _pytest.raises(_typer.Exit):
            cli_main._validate_join_token("http://192.168.1.20:11400", "tok", "me")


def test_swarm_token_show_and_rotate(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    save_config(NetllmConfig(), cfg_path)

    result = runner.invoke(cli_main.app, ["swarm-token", "--config", str(cfg_path)])
    assert result.exit_code == 1
    assert "No cluster token" in result.output

    result = runner.invoke(
        cli_main.app, ["swarm-token", "--rotate", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    token = load_config(cfg_path).swarm.cluster_token
    assert len(token) >= 24
    assert token in result.output


def test_heartbeat_requires_token_when_set() -> None:
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.swarm.cluster_token = "secret"
    payload = {
        "agent_id": "peer-x",
        "listen_url": "http://192.168.1.9:11400",
        "role": "peer",
        "hostname": "x",
        "backends": [],
    }
    with TestClient(create_app(cfg)) as client:
        denied = client.post("/netllm/v1/heartbeat", json=payload)
        allowed = client.post(
            "/netllm/v1/heartbeat",
            json=payload,
            headers={"Authorization": "Bearer secret"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 204


def test_status_reports_cluster_token_presence() -> None:
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.swarm.cluster_token = "secret"
    with TestClient(create_app(cfg)) as client:
        data = client.get("/netllm/v1/status").json()
    assert data["cluster_token_set"] is True
