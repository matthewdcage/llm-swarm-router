"""JSON import/export for config.toml (macOS settings UI)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from netllm_core.config_guards import apply_config_guards
from netllm_core.config_merge import apply_config_patch
from netllm_core.models import load_config, save_config


def export_config(path: Path | None = None) -> dict[str, Any]:
    cfg = load_config(path)
    return cfg.model_dump(mode="json")


def import_config(data: dict[str, Any], path: Path | None = None) -> Path:
    # Merge over the existing config instead of replacing it: the macOS
    # settings UI round-trips only the fields its Swift structs model,
    # so a straight replace would silently drop everything else. Merge
    # mechanics (what "omitted from the patch" means per field) live in
    # netllm_core.config_merge, and the post-merge guards in
    # netllm_core.config_guards -- both shared with the dashboard's save
    # path so the two writers cannot diverge on what they enforce. See
    # docs/config-guards-audit.md and docs/architecture/07-findings-register.md
    # F-02 (this path previously skipped the guards entirely).
    from netllm_discovery.lan import own_agent_urls

    cfg = apply_config_patch(load_config(path), data)
    apply_config_guards(cfg, own_agent_urls=own_agent_urls(cfg.agent.listen))
    return save_config(cfg, path)


def emit_export(path: Path | None = None) -> None:
    sys.stdout.write(json.dumps(export_config(path)))


def read_import(path: Path | None = None) -> Path:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    saved = import_config(data, path)
    sys.stdout.write(json.dumps({"path": str(saved)}))
    return saved
