"""UI-6 on the CLI: `netllm doctor` emits the same check inventory.

The CLI runs its own checks -- it can probe the port it is about to bind and
the PATH it was launched from, which an already-running agent cannot -- but the
row shape, the severities and the issues/notes derivation come from the one
shared builder, so `netllm doctor --json` and `GET /netllm/v1/doctor` cannot
drift apart on shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import netllm_cli.main as cli_main
import pytest
from netllm_cli.commands import diagnose as cli_diagnose
from netllm_core.doctor_checks import DOCTOR_ACTION_KINDS, DOCTOR_SEVERITIES
from netllm_core.models import NetllmConfig, save_config
from typer.testing import CliRunner

runner = CliRunner()

#: Frozen here, not imported, so renaming an id is a two-file diff.
FROZEN_CLI_CHECK_IDS = frozenset(
    {
        "config.present",
        "swarm.open_lan_no_token",
        "cloud.fallback_order",
        "agent.gateway_advertise",
        "cloud.unknown_provider",
        "config.deprecated_key",
        "config.schema_version",
        "swarm.mdns_available",
        "cli.global_path",
        "backends.local_online",
        "cloud.anthropic_key",
        "agent.lan_ip",
        "agent.supervisor",
        "agent.port_conflict",
        "swarm.mdns_advertise",
        "swarm.mdns_multicast",
    }
)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch):
    """No live provider scan, no live port probe, no mDNS browse."""

    async def _one_online(cfg: NetllmConfig) -> list[dict[str, Any]]:
        return [{"name": "ollama", "status": "online", "models": ["m"]}]

    monkeypatch.setattr(cli_diagnose, "scan_local_providers", _one_online)
    monkeypatch.setattr(cli_diagnose, "mdns_available", lambda: False)
    monkeypatch.setattr("netllm_discovery.runtime.check_listen_port", lambda _cfg: None)
    monkeypatch.setattr("netllm_discovery.lan.local_lan_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(
        "netllm_cli.install_detect.skip_global_path_doctor_check", lambda: True
    )


def _run(cfg_path: Path, *args: str):
    return runner.invoke(cli_main.app, ["doctor", "--config", str(cfg_path), *args])


def _healthy_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.agent.listen = "127.0.0.1:11400"
    # `mdns_available` is stubbed False above, so leaving mDNS advertising on
    # would be a legitimate finding and this would not be a healthy config.
    cfg.swarm.mdns = False
    save_config(cfg, cfg_path)
    return cfg_path


def test_json_carries_the_check_inventory(tmp_path: Path) -> None:
    result = _run(_healthy_config(tmp_path), "--json")
    payload = json.loads(result.stdout)
    assert payload["checks"], "no checks emitted"
    ids = {c["id"] for c in payload["checks"]}
    assert ids <= FROZEN_CLI_CHECK_IDS
    for check in payload["checks"]:
        assert check["severity"] in DOCTOR_SEVERITIES
        assert check["action"]["kind"] in DOCTOR_ACTION_KINDS
        assert isinstance(check["ok"], bool)


def test_json_issues_and_notes_are_the_derivation(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"  # open LAN -> a note, not an issue
    cfg.agent.role = "gateway"
    cfg.agent.advertise = False  # -> an issue
    save_config(cfg, cfg_path)

    payload = json.loads(_run(cfg_path, "--json").stdout)
    checks = payload["checks"]
    assert payload["issues"] == [
        {"title": c["title"], "fix": c.get("fix", "")}
        for c in checks
        if not c["ok"] and c["severity"] == "error"
    ]
    assert payload["notes"] == [
        c["detail"] for c in checks if not c["ok"] and c["severity"] == "warn"
    ]
    assert payload["ok"] is False
    assert any("Gateway not advertising" == i["title"] for i in payload["issues"])
    assert any("open" in note.lower() for note in payload["notes"])


def test_a_healthy_config_passes_every_check_it_ran(tmp_path: Path) -> None:
    payload = json.loads(_run(_healthy_config(tmp_path), "--json").stdout)
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert all(c["ok"] for c in payload["checks"])
    assert all(c["severity"] == "info" for c in payload["checks"])


def test_verbose_lists_the_checks_that_passed(tmp_path: Path) -> None:
    result = _run(_healthy_config(tmp_path), "--verbose")
    assert result.exit_code == 0, result.output
    assert "passed" in result.output
    assert "config.present" in result.output
    assert "agent.port_conflict" in result.output


def test_the_default_output_is_unchanged(tmp_path: Path) -> None:
    """`netllm doctor` is in scripts; its quiet-when-healthy behaviour is the
    useful part and the inventory is opt-in."""
    result = _run(_healthy_config(tmp_path))
    assert result.exit_code == 0, result.output
    assert "All checks passed." in result.output
    assert "config.present" not in result.output


def test_a_failing_run_points_at_verbose_without_dumping_the_inventory(
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.role = "gateway"
    cfg.agent.advertise = False
    save_config(cfg, cfg_path)
    result = _run(cfg_path)
    assert result.exit_code == 1
    assert "Gateway not advertising" in result.output
    assert "--verbose" in result.output
    assert "cli.global_path" not in result.output


def test_a_missing_config_is_reported_as_a_check(tmp_path: Path) -> None:
    payload = json.loads(_run(tmp_path / "nope.toml", "--json").stdout)
    row = next(c for c in payload["checks"] if c["id"] == "config.present")
    assert row["ok"] is False
    assert row["fix"] == "Run `netllm init`"
    assert {"title": "No config file", "fix": "Run `netllm init`"} in payload["issues"]
