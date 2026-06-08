"""Doctor behavior when menubar supervises the agent but port state disagrees."""

from __future__ import annotations

import json
from unittest.mock import patch

from netllm_cli.main import app
from netllm_discovery.runtime import PortConflict
from typer.testing import CliRunner

runner = CliRunner()


def test_doctor_flags_menubar_supervisor_not_running(tmp_path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[agent]\nlisten = "127.0.0.1:11400"\nrole = "peer"\nadvertise = true\n',
        encoding="utf-8",
    )
    conflict = PortConflict(
        port=11400,
        pid=9999,
        url="http://127.0.0.1:11400",
        occupied_by_netllm=True,
        agent_id="orphan",
        hostname="test-host",
    )
    with patch.dict("os.environ", {"NETLLM_SUPERVISED": "menubar"}, clear=False):
        with patch("netllm_cli.main.control_socket_path") as sock:
            sock.return_value.exists.return_value = True
            with patch("netllm_cli.lifecycle.darwin.send_app_control") as send:
                send.return_value = {"ok": True, "state": "failed", "pid": None}
                with patch(
                    "netllm_discovery.runtime.check_listen_port",
                    return_value=conflict,
                ):
                    with patch(
                        "netllm_cli.main.asyncio.run",
                        return_value=[{"status": "online", "id": "omlx"}],
                    ):
                        with patch(
                            "netllm_cli.main.mdns_available",
                            return_value=False,
                        ):
                            result = runner.invoke(
                                app,
                                ["doctor", "--json", "--config", str(cfg)],
                            )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    titles = [issue["title"] for issue in payload["issues"]]
    assert any("Menubar supervisor reports agent not running" in t for t in titles)
