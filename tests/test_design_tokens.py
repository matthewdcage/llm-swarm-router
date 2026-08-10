"""Design token contract — JSON source matches generated dashboard CSS."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS_JSON = ROOT / "apps" / "netllm-mac" / "design-tokens.json"
STATIC = ROOT / "packages" / "netllm-agent" / "src" / "netllm_agent" / "static"
OUTPUT_CSS = STATIC / "dashboard-tokens.css"
GENERATOR = ROOT / "scripts" / "generate-dashboard-tokens.py"
SWIFT_TOKENS = (
    ROOT / "apps" / "netllm-mac" / "Sources" / "Config" / "DesignTokens.swift"
)
SWIFT_GENERATOR = ROOT / "scripts" / "generate-swift-tokens.py"


def test_design_tokens_json_has_required_modes() -> None:
    data = json.loads(TOKENS_JSON.read_text(encoding="utf-8"))
    assert "light" in data
    assert "dark" in data
    assert "shared" in data
    assert data["shared"]["radius"] == "10px"
    assert data["light"]["accent"] == "#007aff"
    assert data["shared"]["ppColor"] == "#0a84ff"
    assert data["shared"]["tgColor"] == "#34c759"


def test_dashboard_tokens_css_is_generated_and_current() -> None:
    assert OUTPUT_CSS.is_file()
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_dashboard_tokens_css_exports_radius() -> None:
    css = OUTPUT_CSS.read_text(encoding="utf-8")
    assert "--radius: 10px;" in css
    assert "--accent: #007aff;" in css


def test_dashboard_tokens_css_supports_explicit_theme_override() -> None:
    """The Preferences theme control pins a mode via :root[data-theme]; without
    these blocks it could only ever follow the OS setting."""
    css = OUTPUT_CSS.read_text(encoding="utf-8")
    assert ':root[data-theme="light"]' in css
    assert ':root[data-theme="dark"]' in css


def test_swift_tokens_are_generated_and_current() -> None:
    assert SWIFT_TOKENS.is_file()
    result = subprocess.run(
        [sys.executable, str(SWIFT_GENERATOR), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_swift_and_css_tokens_come_from_the_same_source() -> None:
    """Both surfaces must expose the same token names, or a colour can drift
    between the macOS app and the dashboard without any test noticing."""
    data = json.loads(TOKENS_JSON.read_text(encoding="utf-8"))
    swift = SWIFT_TOKENS.read_text(encoding="utf-8")
    css = OUTPUT_CSS.read_text(encoding="utf-8")
    for token in data["light"]:
        if token == "shadow":  # CSS-only: a box-shadow has no Swift analogue
            continue
        assert f"static let {token} " in swift, f"{token} missing from Swift tokens"
    assert "--panel:" in css
    assert "static let panel " in swift


def test_swift_tokens_resolve_per_appearance() -> None:
    """Light/dark parity on macOS depends on dynamic NSColor, not one palette."""
    swift = SWIFT_TOKENS.read_text(encoding="utf-8")
    assert "func dynamic(light: NSColor, dark: NSColor)" in swift
    assert "darkAqua" in swift
