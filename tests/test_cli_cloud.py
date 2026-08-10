"""`netllm cloud` CLI commands."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def stub_probe(status: str = "ok", detail: str = "Key accepted.") -> Iterator[None]:
    """Answer the live credential check without touching the network.

    `netllm cloud enable` / `set-key` / `connect` all verify now (UI-7a), so
    without this every one of those tests would issue a real request to a
    real provider. The stub still fingerprints the key it was given, because
    the fingerprint is what the write-path gate compares — a stub that faked
    that too would pass tests the product would fail.
    """
    from netllm_core import cloud_verification

    async def _fake(provider_cfg, spec, *, api_key=None, **kwargs):  # noqa: ANN001
        key = (api_key or "") or cloud_verification.resolve_api_key(provider_cfg, spec)
        return {
            "status": status,
            "ok": status == "ok",
            "checked_at": "2026-08-10T00:00:00+00:00",
            "detail": detail,
            "blocker": cloud_verification.blocker_sentence(status, detail, spec),
            "http_status": 200 if status == "ok" else 401,
            "key_fingerprint": cloud_verification.key_fingerprint(key),
        }

    with patch.object(cloud_verification, "probe_cloud_provider", _fake):
        yield


def _with_key(cfg_path: Path, provider: str, key: str = "mk-inline") -> None:
    cfg = load_config(cfg_path)
    cfg.cloud.providers[provider] = CloudProviderConfig(api_key=key)
    save_config(cfg, cfg_path)


def test_cloud_list_shows_registry_providers(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(cli_main.app, ["cloud", "list", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "Moonshot AI (Kimi)" in result.output
    assert "OpenRouter" in result.output


def test_cloud_enable_persists_config(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    _with_key(cfg_path, "moonshot")
    with stub_probe():
        result = runner.invoke(
            cli_main.app, ["cloud", "enable", "moonshot", "--config", str(cfg_path)]
        )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].enabled is True
    # And the check that earned it was written down, so the running agent and
    # the dashboard can both see WHY it is allowed to be on.
    assert cfg.cloud.providers["moonshot"].verified_status == "ok"


def test_cloud_enable_refuses_a_provider_with_no_key(tmp_path: Path) -> None:
    """The reported bug, on the surface that could always reach it first."""
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "enable", "moonshot", "--config", str(cfg_path)]
    )
    assert result.exit_code != 0
    assert "No key set" in result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers.get("moonshot", CloudProviderConfig()).enabled is False


def test_cloud_enable_refuses_a_key_the_provider_rejects(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    _with_key(cfg_path, "moonshot", "mk-revoked")
    with stub_probe(status="unauthorized", detail="HTTP 401."):
        result = runner.invoke(
            cli_main.app, ["cloud", "enable", "moonshot", "--config", str(cfg_path)]
        )
    assert result.exit_code != 0
    assert "rejected" in result.output.lower()
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].enabled is False
    # The failed check is kept: every surface must be able to say "401",
    # not fall back to "never checked" the moment a check fails.
    assert cfg.cloud.providers["moonshot"].verified_status == "unauthorized"


def test_cloud_enable_with_region(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    _with_key(cfg_path, "moonshot")
    with stub_probe():
        result = runner.invoke(
            cli_main.app,
            [
                "cloud",
                "enable",
                "moonshot",
                "--region",
                "cn",
                "--config",
                str(cfg_path),
            ],
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
    with stub_probe():
        result = _set_key_via_env(cfg_path)
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].api_key_env == "MY_MOONSHOT_KEY"
    assert cfg.cloud.providers["moonshot"].api_key == ""
    assert result.exit_code == 0, result.output


def _set_key_via_env(cfg_path: Path):  # noqa: ANN202
    return runner.invoke(
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


def test_cloud_set_key_inline_via_prompt(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch("getpass.getpass", return_value="mk-secret"), stub_probe():
        result = runner.invoke(
            cli_main.app, ["cloud", "set-key", "moonshot", "--config", str(cfg_path)]
        )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert cfg.cloud.providers["moonshot"].api_key == "mk-secret"
    # set-key checks what it just stored: a key pasted with a trailing
    # newline should be caught now, not on the first failed failover.
    assert cfg.cloud.providers["moonshot"].verified_status == "ok"


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
    _with_key(cfg_path, "openrouter", "sk-or-key")
    with stub_probe():
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
    with stub_probe():
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
