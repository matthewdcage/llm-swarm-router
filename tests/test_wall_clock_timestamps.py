"""UI-3 — every value the UI ages carries an epoch clock from the server.

The rule this file pins: *a client never infers an age from its own fetch
time, and never from a clock only the emitting process can read.*

`BackendHealth.last_check` is `time.monotonic()`. A browser subtracting it
from `Date.now()` gets an age of roughly fifty-six years, which is why the
Backends page rendered no probe age at all. The fix is additive — a wall-clock
sibling stamped by the same probe — and the constraint is that `last_check`
keeps its monotonic meaning, because `pool._freshness_s` and its three callers
do the health-cache arithmetic with it. Converting it would move the TTL
whenever NTP or a sleep/wake moved the wall clock.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from netllm_core.models import Backend, BackendHealth, NetllmConfig
from netllm_core.pool import RouterPool

_MOCK_ONLINE = {
    "status": "online",
    "http_status": 200,
    "models": ["m1"],
    "model_count": 1,
}


def _backend(**kwargs: object) -> Backend:
    defaults: dict[str, object] = {
        "id": "b1",
        "base_url": "http://127.0.0.1:8080/v1",
        "provider": "omlx",
        "local": True,
    }
    defaults.update(kwargs)
    return Backend(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------- backends


def test_a_never_probed_backend_reports_the_zero_sentinel() -> None:
    """`0.0`, not `null`.

    The dashboard has to render "never probed" rather than an age. Matching
    `last_check`'s existing sentinel keeps one convention instead of two, and
    keeps the field a plain float for every typed client.
    """
    health = BackendHealth()
    assert health.last_check_epoch_s == 0.0
    assert health.last_check == 0.0


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_a_probe_stamps_a_wall_clock_a_browser_can_subtract(_mock: object) -> None:
    pool = RouterPool()
    backend = _backend()
    pool.set_backends([backend])

    before = time.time()
    assert pool.is_healthy(backend, force_refresh=True) is True
    after = time.time()

    assert before <= backend.health.last_check_epoch_s <= after
    # And the monotonic field is untouched in meaning: on every platform this
    # runs on, monotonic() and time() have unrelated origins.
    assert backend.health.last_check != backend.health.last_check_epoch_s


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_the_epoch_sibling_does_not_drive_freshness(_mock: object) -> None:
    """The health TTL still counts in monotonic seconds.

    Regression guard for the tempting simplification — reusing the wall clock
    for `_freshness_s` — which would make an NTP step or a laptop waking from
    sleep either blackhole a healthy backend or re-probe every request.
    """
    pool = RouterPool(health_ttl_s=300.0)
    backend = _backend()
    pool.set_backends([backend])

    pool.is_healthy(backend, force_refresh=True)
    monotonic_at_probe = backend.health.last_check

    # Move the wall clock a decade forward. Freshness must not notice.
    with patch("netllm_core.pool.time.time", return_value=time.time() + 315_000_000):
        assert pool.is_healthy(backend) is True

    assert backend.health.last_check == monotonic_at_probe, (
        "a cached-fresh verdict re-probed after only the wall clock moved — "
        "freshness is reading the epoch field"
    )


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_marking_a_backend_online_or_offline_stamps_the_clock(_mock: object) -> None:
    """`mark_success` and the offline trip both move the health verdict, so
    both have to move the timestamp the UI ages it by. A row that flipped to
    offline five minutes ago must not read "probed 2h ago"."""
    pool = RouterPool(max_failures=1)
    backend = _backend()
    pool.set_backends([backend])

    before = time.time()
    pool.mark_success(backend, latency_ms=12.0)
    assert before <= backend.health.last_check_epoch_s <= time.time()

    stamped_on_success = backend.health.last_check_epoch_s
    pool.mark_failure(backend, status_code=500)
    assert backend.health.status == "offline"
    assert backend.health.last_check_epoch_s >= stamped_on_success


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_a_rebuilt_peer_row_keeps_the_probe_clock(_mock: object) -> None:
    """Peer rows are rebuilt from scratch on every heartbeat merge.

    `merge_backends` hydrates the display fields from the surviving health
    cache; without the epoch sibling in that hydration, a peer's probe age
    reset to "never" every heartbeat interval.
    """
    pool = RouterPool()
    peer = _backend(id="peer:abc", base_url="http://10.0.0.5:11400/v1", local=False)
    pool.set_backends([peer])
    pool.mark_success(peer)
    stamped = peer.health.last_check_epoch_s
    assert stamped > 0

    rebuilt = _backend(id="peer:abc", base_url="http://10.0.0.5:11400/v1", local=False)
    pool.merge_backends([rebuilt])

    merged = next(b for b in pool.backends if b.id == "peer:abc")
    assert merged.health.last_check_epoch_s == stamped


# ------------------------------------------------------------ scan clocks


@pytest.mark.asyncio
async def test_a_scanned_agent_row_carries_the_moment_it_answered() -> None:
    """`probed_at` is per row, not per scan.

    `dedupe_agents_by_id` collapses several probes of the same multi-homed
    host into one row, and a /16 sweep can be minutes wide — a single
    scan-level clock would mislabel both.
    """
    from netllm_discovery import lan

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"agent_id": "peer-a", "listen_url": "http://10.0.0.5:11400"}

    class FakeClient:
        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    before = time.time()
    row = await lan.fetch_agent_status("http://10.0.0.5:11400", FakeClient())
    assert row is not None
    assert before <= row["probed_at"] <= time.time()


@pytest.mark.asyncio
async def test_the_lan_scan_clock_is_stamped_by_the_scan_not_the_caller() -> None:
    """The trap this feature had to avoid.

    `POST /netllm/v1/admin/peers-scan` is one of four things that trigger a
    LAN sweep — startup, the mDNS fallback and the rediscovery loop are the
    others. Stamping in the route handler would leave the dashboard claiming
    the last scan happened whenever someone last clicked the button.
    """
    from netllm_discovery import lan

    lan._last_peer_scan_at = 0.0
    assert lan.last_peer_scan_at() == 0.0

    before = time.time()
    # No CIDR resolves to a host here, so nothing is probed — the scan still
    # ran, and "ran and found nothing" must be distinguishable from "never".
    await lan.subnet_scan_agents(["192.168.255.0/32"])
    assert before <= lan.last_peer_scan_at() <= time.time()


@pytest.mark.asyncio
async def test_the_provider_scan_clock_is_stamped_by_the_scan() -> None:
    from netllm_discovery import local

    local._last_provider_scan_at = 0.0
    cfg = NetllmConfig()
    cfg.discovery.providers = []
    cfg.discovery.custom_endpoints = []

    before = time.time()
    await local.scan_local_providers(cfg)
    assert before <= local.last_provider_scan_at() <= time.time()


# ------------------------------------------------------------ status shape


def test_status_distinguishes_never_scanned_from_scanned_and_empty() -> None:
    """`null` means "has not run in this process".

    A `0.0` here would render as 1970 and a `time.time()` default would claim
    a scan that never happened; both are worse than saying nothing.
    """
    from netllm_agent.service import AgentService
    from netllm_discovery import lan, local

    lan._last_peer_scan_at = 0.0
    local._last_provider_scan_at = 0.0

    service = AgentService(NetllmConfig())
    discovery = service.status_payload()["discovery"]
    assert discovery == {"last_scan_at": None, "last_peer_scan_at": None}

    local._last_provider_scan_at = 1_786_786_400.0
    assert service.status_payload()["discovery"]["last_scan_at"] == 1_786_786_400.0


def test_the_scan_routes_echo_the_clock_they_just_stamped() -> None:
    """A caller that pressed the button should not need a second request.

    Both routes stamp `discovery.{last_scan_at,last_peer_scan_at}` as a side
    effect, so the value is reachable via `/status` either way — the echo just
    spares the round-trip. `None` still means "has not run", so a caller can
    tell "scan produced nothing" from "scan never ran".
    """
    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app
    from netllm_discovery import lan, local

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False

    lan._last_peer_scan_at = 0.0
    local._last_provider_scan_at = 0.0

    with TestClient(create_app(cfg)) as client:
        discover = client.post("/netllm/v1/admin/discover")
        assert discover.status_code == 200, discover.text
        assert "last_scan_at" in discover.json()

        local._last_provider_scan_at = 1_786_786_400.0
        again = client.post("/netllm/v1/admin/discover")
        # The scan runs for real and restamps, so assert the shape and that a
        # stamped clock is echoed as a number rather than pinning the instant.
        assert isinstance(again.json()["last_scan_at"], float)

        scan = client.post("/netllm/v1/admin/peers-scan?save=false")
        assert scan.status_code == 200, scan.text
        assert "last_peer_scan_at" in scan.json()


# ------------------------------------------------------------- the page


BACKENDS_JS = (
    Path(__file__).resolve().parents[1]
    / "packages/netllm-agent/src/netllm_agent/static/pages/backends.js"
).read_text(encoding="utf-8")


def test_the_backends_page_ages_probes_off_the_epoch_field() -> None:
    """It could not before: the page's own comment said `last_check` "is a
    time.monotonic() value and cannot be turned into a wall clock here"."""
    assert "last_check_epoch_s" in BACKENDS_JS
    assert "cannot be turned into a wall clock here" not in BACKENDS_JS
    assert "backendProbeAgeText" in BACKENDS_JS
    assert "Last probe" in BACKENDS_JS
