"""Tests for config load/save."""

from __future__ import annotations

from pathlib import Path

from netllm_core.models import NetllmConfig, load_config, save_config


def test_save_and_load_config(tmp_path: Path) -> None:
    cfg = NetllmConfig()
    cfg.agent.listen = "127.0.0.1:11401"
    cfg.routing.default_strategy = "round_robin"
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.agent.listen == "127.0.0.1:11401"
    assert loaded.routing.default_strategy == "round_robin"
