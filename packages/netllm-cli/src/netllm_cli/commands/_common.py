"""Shared CLI helpers used by every command module."""

from __future__ import annotations

from pathlib import Path

import typer
from netllm_core.config import default_config_path, load_config
from netllm_core.models import NetllmConfig

from netllm_cli.ui import print_error


def _config_path_option(path: Path | None) -> Path:
    return path or default_config_path()


def _require_config(cfg_path: Path) -> NetllmConfig:
    if not cfg_path.is_file():
        print_error(
            "Config not found",
            f"No config at [cyan]{cfg_path}[/]",
            hints=[
                "Create one: [cyan]netllm init[/]",
                "Custom path: [cyan]netllm init --config /path/to/config.toml[/]",
            ],
        )
        raise typer.Exit(1)
    try:
        return load_config(cfg_path)
    except Exception as exc:  # pydantic ValidationError / TOML parse
        print_error(
            "Config is invalid",
            f"Could not load [cyan]{cfg_path}[/]:\n{exc}",
            hints=[
                "Fix the value(s) above, or regenerate: [cyan]netllm init[/]",
                "Edit in $EDITOR: [cyan]netllm config-edit[/]",
            ],
        )
        raise typer.Exit(1) from exc


def _normalize_agent_url(url: str) -> str:
    from urllib.parse import urlparse

    base = url.strip().rstrip("/")
    if not base.startswith("http"):
        base = f"http://{base}"
    if urlparse(base).port is None:
        base = f"{base}:11400"
    return base
