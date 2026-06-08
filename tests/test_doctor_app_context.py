"""Doctor checks that differ when run from the macOS app bundle."""

from __future__ import annotations

import json
from unittest.mock import patch

from netllm_cli.install_detect import skip_global_path_doctor_check
from netllm_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_skip_global_path_doctor_check_in_bundle() -> None:
    env = {"NETLLM_BUNDLE_PATH": "/Applications/netllm-mac.app"}
    with patch.dict("os.environ", env):
        assert skip_global_path_doctor_check() is True


def test_skip_global_path_doctor_check_when_supervised() -> None:
    with patch.dict("os.environ", {"NETLLM_SUPERVISED": "menubar"}, clear=False):
        assert skip_global_path_doctor_check() is True


def test_doctor_json_omits_global_path_issue_in_app_bundle(tmp_path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[agent]\nlisten = "127.0.0.1:11400"\nrole = "peer"\nadvertise = true\n',
        encoding="utf-8",
    )
    env = {"NETLLM_BUNDLE_PATH": "/Applications/netllm-mac.app"}
    with patch.dict("os.environ", env):
        with patch("netllm_cli.main.global_netllm_installed", return_value=True):
            with patch("netllm_cli.main.global_cli_on_path", return_value=False):
                with patch(
                    "netllm_cli.main.asyncio.run",
                    return_value=[{"status": "online", "id": "omlx"}],
                ):
                    with patch(
                        "netllm_discovery.runtime.check_listen_port",
                        return_value=None,
                    ):
                        with patch("netllm_cli.main.mdns_available", return_value=True):
                            result = runner.invoke(
                                app,
                                ["doctor", "--json", "--config", str(cfg)],
                            )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    titles = [issue["title"] for issue in payload["issues"]]
    assert not any("PATH" in title for title in titles)
