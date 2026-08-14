"""Dashboard telemetry UI contract tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "packages" / "netllm-agent" / "src" / "netllm_agent" / "static"
TOKENS = ROOT / "apps" / "netllm-mac" / "design-tokens.json"


def test_dashboard_has_serving_telemetry() -> None:
    """The Serving tab merged into Overview — same panels, new home.

    Every marker below is the same fact as before, re-pointed at the file that
    now renders it: `pages/overview.js` for the panels, `dashboard.js` for the
    fetch and the poll timer that feed them.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    overview = (STATIC / "pages" / "overview.js").read_text(encoding="utf-8")
    core = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert 'data-page="overview"' in html
    assert 'id="page-overview"' in html
    assert "function renderOverviewPage" in overview
    assert 'registerPage("overview", renderOverviewPage)' in overview
    # routerScopeBlock -> ovRouterScopeTable: the router session/all-time
    # counter block, including the server-supplied total_tokens.
    assert "function ovRouterScopeTable" in overview
    assert "function ovRenderThroughput" in overview
    assert "Routed requests (by backend id)" in overview
    assert "Requests by source (harness)" in overview
    assert "Requests by scenario" in overview
    assert "loadTelemetry" in core
    assert "startMetricsPolling" in core
    assert "METRICS_PAGES" in core
    assert "function telemetryWindowCounts" in core
    assert "function ovRenderTrafficByBackend" in overview
    assert "Traffic by backend" in overview


def test_design_tokens_include_chart_colors() -> None:
    import json

    data = json.loads(TOKENS.read_text(encoding="utf-8"))
    assert "ppColor" in data["shared"]
    assert "tgColor" in data["shared"]
    css = (STATIC / "dashboard-tokens.css").read_text(encoding="utf-8")
    assert "--pp-color:" in css
    assert "--tg-color:" in css
