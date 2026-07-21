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


def test_cloud_enable_with_auth_mode(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app,
        [
            "cloud",
            "enable",
            "openrouter",
            "--auth",
            "oauth_pkce",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["openrouter"].auth == "oauth_pkce"


def test_cloud_enable_rejects_unsupported_auth_mode(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app,
        [
            "cloud",
            "enable",
            "moonshot",
            "--auth",
            "oauth_pkce",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code != 0


def test_cloud_connect_rejects_non_openrouter_provider(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "connect", "moonshot", "--config", str(cfg_path)]
    )
    assert result.exit_code != 0


@patch("netllm_cli.oauth_pkce.exchange_code_for_key", new_callable=AsyncMock)
@patch("netllm_cli.oauth_pkce.wait_for_callback")
@patch("netllm_cli.oauth_pkce.start_local_callback_server")
@patch("netllm_cli.oauth_pkce.open_browser")
def test_cloud_connect_openrouter_full_flow(
    mock_open_browser,
    mock_start_server,
    mock_wait,
    mock_exchange,
    tmp_path: Path,
) -> None:
    mock_start_server.return_value = (54321, object(), object())
    mock_wait.return_value = "auth-code-value"
    mock_exchange.return_value = "sk-or-user-key"

    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "connect", "openrouter", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    mock_open_browser.assert_called_once()
    cfg = load_config(cfg_path)
    provider_cfg = cfg.cloud.providers["openrouter"]
    assert provider_cfg.enabled is True
    assert provider_cfg.auth == "oauth_pkce"
    assert provider_cfg.api_key == "sk-or-user-key"


@patch("netllm_cli.oauth_pkce.wait_for_callback")
@patch("netllm_cli.oauth_pkce.start_local_callback_server")
def test_cloud_connect_openrouter_no_browser_prints_url(
    mock_start_server, mock_wait, tmp_path: Path
) -> None:
    import netllm_cli.oauth_pkce as oauth_pkce_module

    mock_start_server.return_value = (54321, object(), object())
    mock_wait.side_effect = oauth_pkce_module.PKCEFlowError("user cancelled")

    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app,
        ["cloud", "connect", "openrouter", "--no-browser", "--config", str(cfg_path)],
    )
    assert result.exit_code != 0
    assert "openrouter.ai/auth" in result.output
