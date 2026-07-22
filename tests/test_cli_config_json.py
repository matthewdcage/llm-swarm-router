"""`netllm config export/import/schema` — the macOS app's local config
bridge (Sources/Config/ConfigStore.swift shells out to these, not HTTP,
so the app can edit config.toml even when the agent isn't running)."""

from __future__ import annotations

import json
from pathlib import Path

import netllm_cli.main as cli_main
from netllm_core.config_schema import config_schema_document
from netllm_core.models import NetllmConfig, load_config, save_config
from typer.testing import CliRunner

runner = CliRunner()


def _cfg_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    return path


def test_config_schema_matches_the_http_endpoint_document(tmp_path: Path) -> None:
    result = runner.invoke(cli_main.app, ["config", "schema"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == config_schema_document()


def test_config_schema_accepts_the_config_flag_cli_runner_always_appends(
    tmp_path: Path,
) -> None:
    """Regression test: Sources/Config/CLIRunner.swift's run() unconditionally
    appends `--config <path>` to every CLI invocation it makes, including
    `config schema` (which doesn't need a config file — the schema
    describes shape, not a specific file's values). A command that
    doesn't declare `--config` gets rejected by Typer with "No such
    option", `ConfigStore.loadSchema()` swallows that via `try?`, and the
    macOS app silently renders every schema-driven field as empty (the
    ui/discovery/swarm tabs and the model_pools editor) with no visible
    error — this is exactly what shipped and was caught by inspection,
    not by this test suite, hence adding this test now."""
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(cli_main.app, ["config", "schema", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == config_schema_document()


def test_config_export_then_import_round_trips(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    export_result = runner.invoke(
        cli_main.app, ["config", "export", "--config", str(cfg_path)]
    )
    assert export_result.exit_code == 0, export_result.output
    exported = json.loads(export_result.output)
    exported["ui"]["log_dir"] = "/tmp/custom-logs"

    import_result = runner.invoke(
        cli_main.app,
        ["config", "import", "--config", str(cfg_path)],
        input=json.dumps(exported),
    )
    assert import_result.exit_code == 0, import_result.output

    reloaded = load_config(cfg_path)
    assert reloaded.ui.log_dir == "/tmp/custom-logs"
