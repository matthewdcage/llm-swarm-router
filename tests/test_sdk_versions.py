"""Assert installed vendor SDK versions match uv.lock."""

from __future__ import annotations

import re
from pathlib import Path

import anthropic
import openai
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "uv.lock"
LOCK_RE = re.compile(
    (
        r'^\[\[package\]\]\nname = "(?P<name>openai|anthropic)"\n'
        r'version = "(?P<version>[^"]+)"'
    ),
    re.MULTILINE,
)


def _locked_versions() -> dict[str, str]:
    if not LOCK_PATH.is_file():
        pytest.skip("uv.lock not present")
    text = LOCK_PATH.read_text(encoding="utf-8")
    return {m.group("name"): m.group("version") for m in LOCK_RE.finditer(text)}


def test_uv_lock_lists_vendor_sdks() -> None:
    locked = _locked_versions()
    assert "openai" in locked
    assert "anthropic" in locked


def test_openai_version_matches_lockfile() -> None:
    locked = _locked_versions()
    assert openai.__version__ == locked["openai"]


def test_anthropic_version_matches_lockfile() -> None:
    locked = _locked_versions()
    assert anthropic.__version__ == locked["anthropic"]
