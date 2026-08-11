"""A multi-homed peer's address list, driven through the real page.

The reported bug: on a host running Docker, the Peers page listed the one
address another machine can dial and then five `172.x.0.1` bridge gateways
under it, all in the same weight, so the useful line was the least visible
thing in the cell. Nothing there was false — a container on that host really
does reach the agent at its bridge gateway — it was ordered and weighted as
if every address were equally dialable.

The fix is server-side classification (`status.reachable_at`, kinds from
`netllm_discovery.lan.ADDRESS_KINDS`) plus a presentation that ranks and
collapses. This drives it end to end: a heartbeat arrives over the real HTTP
surface, the registry stores it, `/netllm/v1/status` republishes it, and the
browser renders the cell. Nothing is stubbed, so it also proves the payload
survives `normalize_peer_endpoints` and `all_peer_urls`.
"""

from __future__ import annotations

import httpx
from playwright.sync_api import expect

from .conftest import RunningServer

# One peer as a Docker host actually reports itself.
DOCKER_PEER = {
    "agent_id": "peer-docker",
    "listen_url": "http://10.0.0.29:11400",
    "role": "peer",
    "hostname": "workshop",
    "backends": [],
    "routing_strategy": "local_first",
    "version": "0.6.0",
    "also_reachable_at": [
        "http://172.17.0.1:11400",
        "http://172.18.0.1:11400",
        "http://172.19.0.1:11400",
        "http://172.20.0.1:11400",
        "http://172.21.0.1:11400",
    ],
    "reachable_at": [
        {"url": "http://10.0.0.29:11400", "kind": "lan", "interface": "wlp3s0"},
        {"url": "http://172.17.0.1:11400", "kind": "container", "interface": "docker0"},
        {"url": "http://172.18.0.1:11400", "kind": "container", "interface": "br-1a2b"},
        {"url": "http://172.19.0.1:11400", "kind": "container", "interface": "br-3c4d"},
        {"url": "http://172.20.0.1:11400", "kind": "container", "interface": "br-5e6f"},
        {"url": "http://172.21.0.1:11400", "kind": "container", "interface": "br-7a8b"},
    ],
}


def _peers_cell(dash) -> str:  # noqa: ANN001
    dash.click('.nav-item[data-page="peers"]')
    section = dash.locator("#page-peers")
    expect(section).to_be_visible()
    return section.inner_text()


def _send_heartbeat(dash, agent: RunningServer, body: dict) -> None:  # noqa: ANN001
    """Post a heartbeat and pull it into the page.

    The explicit `refresh()` is not impatience: `startStatusPolling` only
    polls while the Overview page is showing, so a Peers page left open reads
    whatever `/status` said at boot.
    """
    resp = httpx.post(f"{agent.base_url}/netllm/v1/heartbeat", json=body, timeout=10)
    resp.raise_for_status()
    dash.evaluate("() => refresh()")
    dash.wait_for_timeout(1000)


def test_a_docker_hosts_bridge_addresses_do_not_bury_its_lan_address(
    dash, agent: RunningServer
) -> None:  # noqa: ANN001
    _send_heartbeat(dash, agent, DOCKER_PEER)
    text = _peers_cell(dash)

    assert "http://10.0.0.29:11400" in text, text
    # Five bridge gateways collapse to one affordance rather than five lines.
    assert "+5 more" in text, text
    assert "http://172.17.0.1:11400" not in text
    assert dash.console_errors == []


def test_the_collapsed_addresses_are_one_click_away_and_labelled(
    dash, agent: RunningServer
) -> None:  # noqa: ANN001
    """Collapsed, never dropped — and the label is what turns a bridge gateway
    from noise into the answer to "what host do my containers use?"."""
    _send_heartbeat(dash, agent, DOCKER_PEER)
    _peers_cell(dash)

    dash.click("#page-peers button:has-text('+5 more')")
    text = dash.locator("#page-peers").inner_text()
    for host in ("172.17.0.1", "172.18.0.1", "172.19.0.1", "172.20.0.1", "172.21.0.1"):
        assert f"http://{host}:11400" in text, text
    assert "from containers" in text, text
    assert dash.console_errors == []


def test_an_unclassified_peer_keeps_the_addresses_it_always_showed(
    dash, agent: RunningServer
) -> None:  # noqa: ANN001
    """The skew case. An agent that predates `reachable_at` sends addresses
    with no kinds; ranking them last would silently hide alternates that used
    to be visible, so absent must rank with the LAN."""
    old = {k: v for k, v in DOCKER_PEER.items() if k != "reachable_at"}
    old = old | {"agent_id": "peer-old", "hostname": "old-box"}
    _send_heartbeat(dash, agent, old)
    text = _peers_cell(dash)

    # Two inline (the budget), the rest behind the disclosure — but the first
    # ones are still there, unlabelled, exactly as before.
    assert "http://172.17.0.1:11400" in text, text
    assert "+3 more" in text, text
    assert dash.console_errors == []
