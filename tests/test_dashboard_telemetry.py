"""Dashboard telemetry UI contract tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "packages" / "netllm-agent" / "src" / "netllm_agent" / "static"
TOKENS = ROOT / "apps" / "netllm-mac" / "design-tokens.json"


def test_dashboard_has_serving_tab() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert 'data-tab="serving"' in html
    assert "tab-serving" in html
    assert "function renderServingTab" in js
    assert "loadTelemetry" in js
    assert "startTelemetryPolling" in js


def test_design_tokens_include_chart_colors() -> None:
    import json

    data = json.loads(TOKENS.read_text(encoding="utf-8"))
    assert "ppColor" in data["shared"]
    assert "tgColor" in data["shared"]
    css = (STATIC / "dashboard-tokens.css").read_text(encoding="utf-8")
    assert "--pp-color:" in css
    assert "--tg-color:" in css
