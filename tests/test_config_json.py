"""Tests for config JSON import/export."""

from __future__ import annotations

import json
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
