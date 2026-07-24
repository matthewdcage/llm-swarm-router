"""`netllm sources` CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import netllm_cli.main as cli_main
from netllm_core.models import (
    NetllmConfig,
    ScenarioRule,
    SourceConfig,
    SourceMatch,
    is_lan_listen,
    load_config,
    save_config,
)
from typer.testing import CliRunner

runner = CliRunner()


def _cfg_path(tmp_path: Path, cfg: NetllmConfig | None = None) -> Path:
    path = tmp_path / "config.toml"
    save_config(cfg or NetllmConfig(), path)
    return path


def test_sources_list_shows_registry_with_detection(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch("netllm_core.harness_detection.shutil.which", return_value=None):
        result = runner.invoke(
            cli_main.app, ["sources", "list", "--config", str(cfg_path)]
        )
    assert result.exit_code == 0, result.output
    assert "Claude Code" in result.output
    assert "Codex CLI" in result.output


def test_sources_toggle_creates_new_source(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    cfg = load_config(cfg_path)
    assert len(cfg.routing.sources) == 1
    assert cfg.routing.sources[0].id == "codex"
    assert cfg.routing.sources[0].known_id == "codex"
    assert cfg.routing.sources[0].enabled is True


def test_sources_toggle_flips_existing_source(tmp_path: Path) -> None:
    cfg = NetllmConfig()
    cfg.routing.sources = [SourceConfig(id="codex", known_id="codex", enabled=True)]
    cfg_path = _cfg_path(tmp_path, cfg)

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(cfg_path).routing.sources[0].enabled is False

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(cfg_path).routing.sources[0].enabled is True


def test_sources_toggle_does_not_disturb_other_sources_or_fields(
    tmp_path: Path,
) -> None:
    """Regression guard: apply_config_patch's routing.sources merge fully
    replaces the list from only the patch entries -- toggling one source
    must not drop another, or blank the toggled source's other fields."""
    cfg = NetllmConfig()
    cfg.routing.sources = [
        SourceConfig(
            id="codex",
            known_id="codex",
            enabled=True,
            secret="s3cr3t",
            model_rewrites={"gpt-4": "gpt-4o"},
            scenarios={"background": ScenarioRule(strategy="local_first")},
            match=SourceMatch(user_agent_contains=["codex-cli"]),
        ),
        SourceConfig(id="claude-code", known_id="claude-code", enabled=True),
    ]
    cfg_path = _cfg_path(tmp_path, cfg)

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output

    updated = load_config(cfg_path)
    assert len(updated.routing.sources) == 2
    claude = next(s for s in updated.routing.sources if s.id == "claude-code")
    assert claude.enabled is True

    codex = next(s for s in updated.routing.sources if s.id == "codex")
    assert codex.enabled is False  # only this field flipped
    assert codex.secret == "s3cr3t"
    assert codex.model_rewrites == {"gpt-4": "gpt-4o"}
    assert codex.scenarios["background"].strategy == "local_first"
    assert codex.match.user_agent_contains == ["codex-cli"]


def test_sources_toggle_elevated_without_secret_rejected_on_lan(
    tmp_path: Path,
) -> None:
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    assert is_lan_listen(cfg.agent.listen)
    cfg.routing.sources = [
        SourceConfig(id="codex", known_id="codex", allow_cloud=True, secret="")
    ]
    cfg_path = _cfg_path(tmp_path, cfg)

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code != 0
    # Config on disk is untouched -- the rejected patch was never saved.
    assert load_config(cfg_path).routing.sources[0].enabled is True
