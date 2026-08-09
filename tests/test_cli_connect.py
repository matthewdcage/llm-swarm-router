"""`netllm connect <tool>` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import netllm_cli.main as cli_main
from netllm_core.models import NetllmConfig, load_config, save_config
from typer.testing import CliRunner

runner = CliRunner()


def _cfg_path(tmp_path: Path, cfg: NetllmConfig | None = None) -> Path:
    path = tmp_path / "config.toml"
    save_config(cfg or NetllmConfig(), path)
    return path


def test_connect_unknown_harness_exits_nonzero() -> None:
    result = runner.invoke(cli_main.app, ["connect", "not-a-tool"])
    assert result.exit_code != 0
    assert "Unknown harness" in result.output


def test_connect_cursor_prints_env(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with (
        patch(
            "netllm_cli.commands.connect._check_agent_health",
            return_value=True,
        ),
        patch("netllm_core.harness_detection.detect", return_value=True),
    ):
        result = runner.invoke(
            cli_main.app,
            ["connect", "cursor", "--config", str(cfg_path), "--print-env"],
        )
    assert result.exit_code == 0, result.output
    assert "export OPENAI_BASE_URL=http://127.0.0.1:11400/v1" in result.output
    assert "export OPENAI_API_KEY=netllm-cursor" in result.output


def test_connect_json_includes_virtual_key_and_surface(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with (
        patch(
            "netllm_cli.commands.connect._check_agent_health",
            return_value=True,
        ),
        patch("netllm_core.harness_detection.detect", return_value=False),
    ):
        result = runner.invoke(
            cli_main.app,
            ["connect", "claude-code", "--config", str(cfg_path), "--json"],
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["harness_id"] == "claude-code"
    assert payload["virtual_key"] == "netllm-claude-code"
    assert payload["surface"] == "anthropic"
    assert "ANTHROPIC_BASE_URL" in payload["env"][0]


def test_connect_unhealthy_agent_exits_nonzero(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch(
        "netllm_cli.commands.connect._check_agent_health",
        return_value=False,
    ):
        result = runner.invoke(
            cli_main.app,
            ["connect", "cursor", "--config", str(cfg_path)],
        )
    assert result.exit_code != 0
    assert "Agent not reachable" in result.output


def test_connect_toggle_registers_source(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch(
        "netllm_cli.commands.connect._check_agent_health",
        return_value=True,
    ):
        result = runner.invoke(
            cli_main.app,
            [
                "connect",
                "codex",
                "--config",
                str(cfg_path),
                "--toggle",
                "--json",
            ],
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["toggle_result"] == "registered"
    cfg = load_config(cfg_path)
    assert len(cfg.routing.sources) == 1
    assert cfg.routing.sources[0].id == "codex"
    assert cfg.routing.sources[0].enabled is True


def test_connect_hermes_agent_json_includes_yaml_snippet(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with (
        patch(
            "netllm_cli.commands.connect._check_agent_health",
            return_value=True,
        ),
        patch("netllm_core.harness_detection.detect", return_value=False),
    ):
        result = runner.invoke(
            cli_main.app,
            ["connect", "hermes-agent", "--config", str(cfg_path), "--json"],
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["harness_id"] == "hermes-agent"
    assert payload["surface"] == "openai"
    assert payload["virtual_key"] == "netllm-hermes-agent"
    assert payload["client_config_path"] == "~/.hermes/config.yaml"
    assert "provider: litellm" in payload["client_config"]
    assert "11400/v1" in payload["client_config"]
