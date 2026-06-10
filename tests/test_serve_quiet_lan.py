"""Regression: menubar `serve -q` with LAN listen must not crash on startup warnings."""

from __future__ import annotations

from unittest.mock import patch

from netllm_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_serve_quiet_lan_warnings_reaches_uvicorn(tmp_path) -> None:
    """Quiet LAN serve must print warnings without Rich file= kwarg."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[agent]
listen = "0.0.0.0:11400"
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
        with patch("netllm_cli.main.asyncio.run", return_value=[]):
            with patch("netllm_agent.app.create_app", return_value=object()):
                with patch("uvicorn.run") as uvicorn_run:
                    result = runner.invoke(
                        app,
                        ["serve", "-q", "--config", str(cfg)],
                    )

    assert result.exit_code == 0, result.output
    assert "unexpected keyword argument 'file'" not in result.output
    uvicorn_run.assert_called_once()
