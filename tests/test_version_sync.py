"""Ensure version strings stay aligned across the workspace."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from netllm_core.version import get_version

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pyproject_versions() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted(REPO_ROOT.glob("**/pyproject.toml")):
        if ".venv" in path.parts or "build" in path.parts or "rpm-stage" in path.parts:
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if version:
            out.append((str(path.relative_to(REPO_ROOT)), str(version)))
    return out


def test_get_version_matches_root_pyproject() -> None:
    root_ver = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))[
        "project"
    ]["version"]
    assert get_version() == root_ver


def test_workspace_pyproject_versions_match() -> None:
    root_ver = get_version()
    mismatches = [
        (rel, ver) for rel, ver in _pyproject_versions() if ver != root_ver
    ]
    assert not mismatches, f"Version drift in pyproject.toml: {mismatches}"


def test_agent_fastapi_version_matches() -> None:
    app_py = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/app.py"
    text = _read(app_py)
    match = re.search(r'FastAPI\([^)]*version=get_version\(\)', text)
    assert match, "app.py should use get_version() for FastAPI version"


def test_cli_version_imports_get_version() -> None:
    main_py = REPO_ROOT / "packages/netllm-cli/src/netllm_cli/main.py"
    text = _read(main_py)
    assert "from netllm_core.version import get_version" in text
    assert "__version__ = get_version()" in text
