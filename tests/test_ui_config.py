"""Tests for optional [ui] config section."""

from pathlib import Path

from netllm_core.models import NetllmConfig, UiConfig, default_log_dir


def test_ui_defaults() -> None:
    cfg = NetllmConfig()
    assert cfg.ui.auto_start_on_launch is True
    assert cfg.ui.log_dir == ""


def test_resolved_log_dir_default() -> None:
    cfg = NetllmConfig()
    assert cfg.resolved_log_dir() == default_log_dir()


def test_resolved_log_dir_override() -> None:
    cfg = NetllmConfig(ui=UiConfig(log_dir="/tmp/netllm-logs"))
    assert cfg.resolved_log_dir() == Path("/tmp/netllm-logs")

