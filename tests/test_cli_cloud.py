"""`netllm cloud` CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import netllm_cli.main as cli_main
from netllm_core.models import (
    CloudProviderConfig,
    NetllmConfig,
    load_config,
    save_config,
)
from typer.testing import CliRunner

runner = CliRunner()


def _cfg_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    return path


def test_cloud_list_shows_registry_providers(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(cli_main.app, ["cloud", "list", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "Moonshot AI (Kimi)" in result.output
    assert "OpenRouter" in result.output


def test_cloud_enable_persists_config(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "enable", "moonshot", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].enabled is True


def test_cloud_enable_with_region(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app,
        ["cloud", "enable", "moonshot", "--region", "cn", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].region == "cn"


def test_cloud_enable_unknown_provider_fails(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "enable", "not-a-provider", "--config", str(cfg_path)]
    )
    assert result.exit_code != 0


def test_cloud_disable_persists_config(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    cfg = load_config(cfg_path)
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(enabled=True)
    save_config(cfg, cfg_path)

    result = runner.invoke(
        cli_main.app, ["cloud", "disable", "moonshot", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    reloaded = load_config(cfg_path)
    assert reloaded.cloud.providers["moonshot"].enabled is False


def test_cloud_set_key_via_env_flag(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app,
        [
            "cloud",
            "set-key",
            "moonshot",
            "--env",
            "MY_MOONSHOT_KEY",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].api_key_env == "MY_MOONSHOT_KEY"
    assert cfg.cloud.providers["moonshot"].api_key == ""


def test_cloud_set_key_inline_via_prompt(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch("getpass.getpass", return_value="mk-secret"):
        result = runner.invoke(
            cli_main.app, ["cloud", "set-key", "moonshot", "--config", str(cfg_path)]
        )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].api_key == "mk-secret"


def test_cloud_fallback_direction(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "local", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.fallback == "local"


def test_cloud_fallback_toggle_off(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "off", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.fallback_enabled is False


def test_cloud_fallback_invalid_mode_fails(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "bogus", "--config", str(cfg_path)]
    )
    assert result.exit_code != 0


@patch("netllm_core.health.diagnose_backend", new_callable=AsyncMock)
def test_cloud_test_probes_provider(mock_diagnose: AsyncMock, tmp_path: Path) -> None:
    mock_diagnose.return_value = {"status": "online", "models": ["kimi-k3"]}
    cfg_path = _cfg_path(tmp_path)
    cfg = load_config(cfg_path)
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, api_key="mk-inline"
    )
    save_config(cfg, cfg_path)

    result = runner.invoke(
        cli_main.app, ["cloud", "test", "moonshot", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "api.moonshot.ai" in result.output
