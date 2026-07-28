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
    result = runner.invoke(
        cli_main.app, ["config", "schema", "--config", str(cfg_path)]
    )
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


# --- F-02: the CLI/macOS save path must enforce the same guards as HTTP -----
#
# netllm_agent.admin.apply_config_patch refuses an elevated routing.sources
# entry without a secret on a LAN-bound agent (HTTP 400). `config import` --
# the macOS Settings Save button -- merged and saved without ever running
# that check, so the app could persist a config the dashboard rejects. Both
# writers now go through netllm_core.config_guards.


def _lan_cfg_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    save_config(cfg, path)
    return path


def test_config_import_rejects_elevated_source_without_secret_on_lan(
    tmp_path: Path,
) -> None:
    path = _lan_cfg_path(tmp_path)
    patch = {
        "routing": {"sources": [{"id": "evil", "enabled": True, "allow_cloud": True}]}
    }
    result = runner.invoke(
        cli_main.app,
        ["config", "import", "--config", str(path)],
        input=json.dumps(patch),
    )
    assert result.exit_code == 1
    # The config on disk must be untouched by a rejected save.
    assert load_config(path).routing.sources == []


def test_config_import_accepts_elevated_source_with_secret_on_lan(
    tmp_path: Path,
) -> None:
    path = _lan_cfg_path(tmp_path)
    patch = {
        "routing": {
            "sources": [
                {
                    "id": "buzz",
                    "enabled": True,
                    "allow_cloud": True,
                    "secret": "s3cret",
                }
            ]
        }
    }
    result = runner.invoke(
        cli_main.app,
        ["config", "import", "--config", str(path)],
        input=json.dumps(patch),
    )
    assert result.exit_code == 0
    saved = load_config(path)
    assert saved.routing.sources[0].id == "buzz"
    assert saved.routing.sources[0].resolve_secret() == "s3cret"


def test_config_import_allows_elevated_source_on_loopback_bind(
    tmp_path: Path,
) -> None:
    """The gate is scoped to LAN-reachable binds — a loopback-only agent has
    no spoofing surface, so it must not start demanding secrets."""
    path = _cfg_path(tmp_path)
    patch = {
        "routing": {"sources": [{"id": "local", "enabled": True, "allow_cloud": True}]}
    }
    result = runner.invoke(
        cli_main.app,
        ["config", "import", "--config", str(path)],
        input=json.dumps(patch),
    )
    assert result.exit_code == 0
    assert load_config(path).routing.sources[0].id == "local"


def test_config_import_drops_self_referential_swarm_peer(tmp_path: Path) -> None:
    """Own-peer filtering was also HTTP-only; a peer URL pointing back at
    this agent would make it gossip with itself."""
    path = _lan_cfg_path(tmp_path)
    patch = {"swarm": {"peers": ["http://127.0.0.1:11400", "http://10.0.0.9:11400"]}}
    result = runner.invoke(
        cli_main.app,
        ["config", "import", "--config", str(path)],
        input=json.dumps(patch),
    )
    assert result.exit_code == 0
    assert load_config(path).swarm.peers == ["http://10.0.0.9:11400"]
