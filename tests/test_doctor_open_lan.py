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
