"""Tests for config JSON import/export."""

from __future__ import annotations

from pathlib import Path

from netllm_cli.config_json import export_config, import_config
from netllm_core.models import NetllmConfig


def test_export_import_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = NetllmConfig()
    original.agent.listen = "127.0.0.1:11401"
    original.swarm.cluster_token = "secret"
    original.routing.default_strategy = "failover"
    original.ui.auto_start_on_launch = False

    from netllm_core.models import save_config

    save_config(original, path)

    exported = export_config(path)
    assert exported["agent"]["listen"] == "127.0.0.1:11401"
    assert exported["swarm"]["cluster_token"] == "secret"

    exported["discovery"]["custom_endpoints"] = ["http://127.0.0.1:9999/v1"]
    import_config(exported, path)

    reloaded = export_config(path)
    assert reloaded["discovery"]["custom_endpoints"] == ["http://127.0.0.1:9999/v1"]


def test_import_applies_lan_mesh_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = NetllmConfig().model_dump(mode="json")
    data["agent"]["listen"] = "0.0.0.0:11400"
    import_config(data, path)
    exported = export_config(path)
    assert exported["routing"]["default_strategy"] == "local_spillover"
    assert exported["swarm"]["subnet_scan"] is True
