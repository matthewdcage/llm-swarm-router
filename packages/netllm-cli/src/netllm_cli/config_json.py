"""JSON import/export for config.toml (macOS settings UI)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from netllm_core.models import (
    NetllmConfig,
    ensure_lan_mesh_defaults,
    load_config,
    save_config,
)


def export_config(path: Path | None = None) -> dict[str, Any]:
    cfg = load_config(path)
    return cfg.model_dump(mode="json")


def import_config(data: dict[str, Any], path: Path | None = None) -> Path:
    cfg = NetllmConfig.model_validate(data)
    ensure_lan_mesh_defaults(cfg)
    return save_config(cfg, path)


def emit_export(path: Path | None = None) -> None:
    sys.stdout.write(json.dumps(export_config(path)))


def read_import(path: Path | None = None) -> Path:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    saved = import_config(data, path)
    sys.stdout.write(json.dumps({"path": str(saved)}))
    return saved
