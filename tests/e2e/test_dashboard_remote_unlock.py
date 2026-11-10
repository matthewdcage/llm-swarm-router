"""A browser that is not on the agent's machine can unlock the dashboard.

Linux and Windows nodes have no native app, so `/ui/` opened from another
machine is their only GUI. Admin routes accept "local client or Bearer
cluster token", but the dashboard had no way to send the token: with a token
configured it showed "Could not reach the agent" for an agent that was up.

The browser here is local, so the local-trust shortcut is switched off for
the test; everything else is the real agent and the real page.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig, save_config

from .conftest import (
    DASHBOARD_READY,
    DASHBOARD_READY_TIMEOUT_MS,
    RunningServer,
    _free_port,
    _serve,
    _shutdown,
)

TOKEN = "e2e-cluster-token"


@pytest.fixture
def remote_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[RunningServer]:
    for target in (
        "netllm_agent.admin.local_admin_client_hosts",
        "netllm_core.platform.local_admin_client_hosts",
    ):
        monkeypatch.setattr(target, lambda: frozenset())
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.agent.hostname = "e2e-remote"
    cfg.swarm.cluster_token = TOKEN
    cfg_path = tmp_path / "config.toml"
    save_config(cfg, cfg_path)
    running = _serve(create_app(cfg, config_path=cfg_path), _free_port())
    try:
        yield running
    finally:
        _shutdown(running)


def _open(page, agent: RunningServer) -> None:  # noqa: ANN001 - playwright type
    page.goto(f"{agent.base_url}/ui/", wait_until="load")
    page.wait_for_function(DASHBOARD_READY, timeout=DASHBOARD_READY_TIMEOUT_MS)


def test_locked_dashboard_asks_for_the_token_instead_of_unreachable(
    page,  # noqa: ANN001
    remote_agent: RunningServer,
) -> None:
    _open(page, remote_agent)
    bar = page.locator("#auth-bar")
    assert bar.is_visible()
    assert "Read-only" in bar.inner_text()
    assert "Could not reach the agent" not in page.locator("body").inner_text()
    assert "Unreachable" not in page.locator("#status-badge").inner_text()


def test_wrong_token_is_rejected_and_not_kept(
    page,  # noqa: ANN001
    remote_agent: RunningServer,
) -> None:
    _open(page, remote_agent)
    page.fill("#auth-token-input", "not-the-token")
    page.click("#auth-token-submit")
    page.wait_for_function(
        "() => document.getElementById('auth-bar').textContent.includes('not accepted')"
    )
    assert page.evaluate("() => sessionStorage.getItem('netllm.clusterToken')") is None
    assert page.evaluate("() => state.config") is None


def test_correct_token_unlocks_config_for_the_tab(
    page,  # noqa: ANN001
    remote_agent: RunningServer,
) -> None:
    _open(page, remote_agent)
    page.fill("#auth-token-input", TOKEN)
    page.click("#auth-token-submit")
    page.wait_for_function("() => state.config !== null", timeout=90_000)
    # refresh() is still settling when config lands; the bar goes last.
    page.locator("#auth-bar").wait_for(state="hidden", timeout=90_000)
    assert page.evaluate("() => state.status && state.status.hostname") == "e2e-remote"
    # The token is never rendered into the page.
    assert TOKEN not in page.content()
