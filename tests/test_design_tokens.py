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


def test_design_tokens_json_has_required_modes() -> None:
    data = json.loads(TOKENS_JSON.read_text(encoding="utf-8"))
    assert "light" in data
    assert "dark" in data
    assert "shared" in data
    assert data["shared"]["radius"] == "10px"
    assert data["light"]["accent"] == "#007aff"


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
