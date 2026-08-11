"""Regression: menubar `serve -q` with LAN listen must not crash on startup warnings."""

from __future__ import annotations

from unittest.mock import patch

from netllm_cli.main import app
from netllm_core.config_migrations import CURRENT_SCHEMA_VERSION
from typer.testing import CliRunner

runner = CliRunner()


def test_serve_quiet_lan_warnings_reaches_uvicorn(tmp_path) -> None:
    """Quiet LAN serve must print warnings without Rich file= kwarg."""
    cfg = tmp_path / "config.toml"
    # Ephemeral port avoids the singleton lock held by a dev machine's menubar
    # agent on :11400; current schema_version avoids a gen-1 migration detour.
    cfg.write_text(
        f"""
schema_version = {CURRENT_SCHEMA_VERSION}

[agent]
listen = "0.0.0.0:11499"
role = "peer"
advertise = true

[discovery]
providers = ["omlx"]

[swarm]
mdns = true

[routing]
default_strategy = "local_first"
""".strip(),
        encoding="utf-8",
    )

    with patch("netllm_discovery.runtime.check_listen_port", return_value=None):
        with patch("netllm_cli.commands.serve_lifecycle.asyncio.run", return_value=[]):
            with patch("netllm_agent.app.create_app", return_value=object()):
                with patch("uvicorn.run") as uvicorn_run:
                    result = runner.invoke(
                        app,
                        ["serve", "-q", "--config", str(cfg)],
                    )

    assert result.exit_code == 0, result.output
    assert "unexpected keyword argument 'file'" not in result.output
    uvicorn_run.assert_called_once()
