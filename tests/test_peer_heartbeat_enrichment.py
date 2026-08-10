"""UI-4a — provenance and reach on `status.peers[]`, across version skew.

Three facts the mesh could not previously carry:

* **`discovered_via`** — an mDNS arrival and a subnet-scan hit were
  indistinguishable once either started heartbeating, so the Peers page had to
  describe every connected agent as "heartbeat".
* **`also_reachable_at`** — a multi-homed host's alternate addresses existed
  only on scan rows, which vanish the moment the peer joins.
* **`providers`** — a peer is materialised locally as ONE `Backend` with
  `provider="custom"` (`SwarmRegistry.peer_agent_backends`), so nothing in the
  mesh knew whether the machine on the other end ran Ollama or oMLX.

**Compatibility is the hard constraint.** Heartbeats cross version
boundaries in both directions: an older peer omits every new field, and a
newer peer must not break an older one. The convention this follows is the
one `agent.max_concurrency` / `draining` already set (see
`packages/netllm-agent/AGENTS.md`) — absent-tolerant both ways, every field
read through an explicit `.get()` with a default that means "this peer told
us nothing".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from netllm_core.models import Backend, BackendHealth, NetllmConfig
from netllm_discovery.swarm import (
    MAX_PEER_ALSO_REACHABLE,
    MAX_PEER_PROVIDERS,
    PeerRecord,
    SwarmRegistry,
    normalize_peer_providers,
    normalize_peer_urls,
)

# A complete heartbeat body from an agent running this build.
NEW_HEARTBEAT: dict[str, object] = {
    "agent_id": "peer-new",
    "listen_url": "http://10.0.0.5:11400",
    "role": "peer",
    "hostname": "mac-mini-m4",
    "backends": [],
    "routing_strategy": "local_first",
    "version": "0.6.0",
    "max_concurrency": 4,
    "draining": False,
    "providers": [
        {"id": "ollama", "provider": "ollama", "model_count": 12},
        {"id": "omlx", "provider": "omlx", "model_count": 3},
    ],
    "also_reachable_at": ["http://10.0.0.6:11400"],
}

# The same agent one release back: no key the reader can rely on beyond the
# original seven, exactly as `docs/mesh-upgrade.md` promises.
OLD_HEARTBEAT: dict[str, object] = {
    k: v
    for k, v in NEW_HEARTBEAT.items()
    if k not in {"providers", "also_reachable_at", "max_concurrency", "draining"}
} | {"agent_id": "peer-old", "hostname": "old-box"}


def _service(**overrides: object):
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    for key, value in overrides.items():
        setattr(cfg.agent, key, value)
    return AgentService(cfg)


# ------------------------------------------------------- backward skew


@pytest.mark.asyncio
async def test_a_heartbeat_without_the_new_fields_still_registers_a_peer() -> None:
    """The required compatibility assertion, in the older-peer direction.

    Nothing about the peer's usefulness depends on the new keys: it still
    routes, still ages, still reports its strategy and version. What it must
    NOT do is raise, be dropped, or be described with invented data.
    """
    service = _service()
    await service.handle_heartbeat(dict(OLD_HEARTBEAT))

    record = service.swarm.peers["peer-old"]
    assert record.listen_url == "http://10.0.0.5:11400"
    assert record.hostname == "old-box"
    assert record.routing_strategy == "local_first"
    assert record.version == "0.6.0"
    # Absent means absent — empty, never a guess and never a placeholder row.
    assert record.providers == []
    assert record.also_reachable_at == []
    # And "heartbeat" is the honest provenance for a peer that simply started
    # talking to us: the sender cannot tell us how we found it.
    assert record.discovered_via == "heartbeat"

    wire = service.swarm.all_peer_urls()[0]
    assert wire["providers"] == []
    assert wire["also_reachable_at"] == []
    assert wire["discovered_via"] == "heartbeat"


@pytest.mark.asyncio
async def test_the_old_and_new_bodies_produce_the_same_routable_peer() -> None:
    """Version skew changes what the UI can *say*, never where traffic goes."""
    old, new = _service(), _service()
    await old.handle_heartbeat(dict(OLD_HEARTBEAT))
    await new.handle_heartbeat(dict(NEW_HEARTBEAT))

    def routable(service: object) -> list[str]:
        return [b.base_url for b in service.swarm.peer_agent_backends()]

    assert routable(old) == routable(new) == ["http://10.0.0.5:11400/v1"]


# -------------------------------------------------------- forward skew


@pytest.mark.asyncio
async def test_a_heartbeat_from_a_newer_agent_is_read_field_by_field() -> None:
    """The other direction: this build receiving keys it has never heard of.

    `UI-4b` will add `host` (GPU/VRAM) and `UI-1` will add `rps_60s` to this
    same body. A reader that validated the body as a closed shape would start
    rejecting its own mesh the day one machine upgraded.
    """
    service = _service()
    future = dict(NEW_HEARTBEAT) | {
        "host": {"gpu_percent": 41.0, "vram_total_gb": 24.0},
        "rps_60s": 3.5,
        "authenticated": True,
        "some_key_from_2027": {"nested": ["anything"]},
    }
    await service.handle_heartbeat(future)

    record = service.swarm.peers["peer-new"]
    assert record.hostname == "mac-mini-m4"
    assert [p["provider"] for p in record.providers] == ["ollama", "omlx"]
    assert record.also_reachable_at == ["http://10.0.0.6:11400"]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "ollama",
        ["ollama", "omlx"],
        [None, 3, {"no_id": True}],
        {"ollama": 12},
    ],
)
def test_a_malformed_providers_list_yields_nothing_rather_than_raising(
    raw: object,
) -> None:
    """Peer records arrive from whatever is on the LAN. The containment rule
    from `test_mesh_version_skew` applies: they may produce *text*, never an
    exception and never a decision about this node."""
    assert normalize_peer_providers(raw) == []


def test_provider_rows_are_normalised_not_trusted() -> None:
    out = normalize_peer_providers(
        [
            {"id": "ollama", "provider": "ollama", "model_count": "12"},
            {"id": "weird", "model_count": -5},
        ]
    )
    assert out == [
        {"id": "ollama", "provider": "ollama", "model_count": 12},
        {"id": "weird", "provider": "", "model_count": 0},
    ]


def test_a_string_is_not_a_url_list() -> None:
    """Iterating a str yields characters — the classic way this shape breaks."""
    assert normalize_peer_urls("http://10.0.0.5:11400") == []


def test_the_gossiped_lists_are_capped() -> None:
    """The body is sent to every peer every `heartbeat_interval_s`, so both
    lists are multiplied by peers × interval. Model *names* are not carried at
    all; they are already reachable via the peer's own `/v1/models`."""
    providers = [
        {"id": f"p{i}", "provider": "custom", "model_count": 1} for i in range(500)
    ]
    assert len(normalize_peer_providers(providers)) == MAX_PEER_PROVIDERS
    urls = [f"http://10.0.0.{i}:11400" for i in range(1, 100)]
    assert len(normalize_peer_urls(urls)) == MAX_PEER_ALSO_REACHABLE


# --------------------------------------------------------- provenance


@pytest.mark.asyncio
async def test_a_peer_found_over_mdns_does_not_become_a_heartbeat_peer() -> None:
    """THE regression this feature exists to prevent.

    mDNS discovery is followed by a heartbeat within one interval, and
    `handle_heartbeat` rebuilds the record from a body that cannot say how we
    found the sender. Overwriting blindly relabels the entire mesh
    "heartbeat" within seconds — which is the state the Peers page was stuck
    in, and why it hedged its Provenance column.
    """
    service = _service()
    service.swarm.register_peer(
        PeerRecord(
            agent_id="peer-new",
            listen_url="http://10.0.0.5:11400",
            discovered_via="mdns",
        )
    )
    await service.handle_heartbeat(dict(NEW_HEARTBEAT))

    assert service.swarm.peers["peer-new"].discovered_via == "mdns"


@pytest.mark.asyncio
async def test_provenance_survives_a_peer_going_to_sleep() -> None:
    """`prune_stale` drops the record; the rediscovery loop re-fetches the URL
    with no idea how it was originally found. Without the URL-keyed memory,
    every laptop that closes its lid comes back labelled "heartbeat"."""
    service = _service()
    service.swarm.register_peer(
        PeerRecord(
            agent_id="peer-new",
            listen_url="http://10.0.0.5:11400",
            discovered_via="subnet_scan",
        )
    )
    service.swarm.prune_stale(max_age_s=-1.0)
    assert "peer-new" not in service.swarm.peers

    await service.handle_heartbeat(dict(NEW_HEARTBEAT))
    assert service.swarm.peers["peer-new"].discovered_via == "subnet_scan"


def test_an_unknown_mechanism_from_the_future_falls_back_to_the_default() -> None:
    registry = SwarmRegistry(NetllmConfig())
    registry.register_peer(
        PeerRecord(
            agent_id="p",
            listen_url="http://10.0.0.9:11400",
            discovered_via="telepathy",
        )
    )
    assert registry.peers["p"].discovered_via == "heartbeat"


@pytest.mark.asyncio
async def test_the_subnet_pass_records_where_each_row_actually_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`discover_lan_agents` merges mDNS, static peers and the subnet probe
    into one list and tags each row, so the pass is not uniformly
    "subnet_scan". Asserted through the real registration path, not by
    setting the field."""
    service = _service()

    async def fake_discover(*args: object, **kwargs: object) -> list[dict[str, object]]:
        return [
            {"agent_id": "a", "listen_url": "http://10.0.0.1:11400", "source": "mdns"},
            {
                "agent_id": "b",
                "listen_url": "http://10.0.0.2:11400",
                "source": "subnet",
            },
            {
                "agent_id": "c",
                "listen_url": "http://10.0.0.3:11400",
                "source": "config",
            },
            {"agent_id": "d", "listen_url": "http://10.0.0.4:11400"},
        ]

    monkeypatch.setattr("netllm_discovery.lan.discover_lan_agents", fake_discover)
    await service._discover_subnet_peers()

    via = {pid: p.discovered_via for pid, p in service.swarm.peers.items()}
    assert via == {
        "a": "mdns",
        "b": "subnet_scan",
        "c": "static",
        "d": "subnet_scan",
    }


@pytest.mark.asyncio
async def test_a_pinned_peer_is_recorded_as_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from netllm_discovery import swarm as swarm_mod

    cfg = NetllmConfig()
    cfg.swarm.peers = ["http://10.0.0.7:11400"]
    registry = swarm_mod.SwarmRegistry(cfg)

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"agent_id": "pinned-one", "listen_url": "http://10.0.0.7:11400"}

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(swarm_mod.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    await registry.refresh_static_peers()

    assert registry.peers["pinned-one"].discovered_via == "static"


# --------------------------------------------------------- reachability


@pytest.mark.asyncio
async def test_alternate_addresses_accumulate_instead_of_being_overwritten() -> None:
    """A multi-homed host is observed one probe at a time. A heartbeat that
    mentions no alternates must not erase the address a subnet scan found."""
    service = _service()
    service.swarm.register_peer(
        PeerRecord(
            agent_id="peer-new",
            listen_url="http://10.0.0.5:11400",
            also_reachable_at=["http://192.168.1.5:11400"],
            discovered_via="subnet_scan",
        )
    )
    await service.handle_heartbeat(dict(NEW_HEARTBEAT))

    record = service.swarm.peers["peer-new"]
    assert set(record.also_reachable_at) == {
        "http://10.0.0.6:11400",  # self-advertised in the heartbeat
        "http://192.168.1.5:11400",  # observed earlier by the scan
    }
    assert record.listen_url not in record.also_reachable_at


@pytest.mark.asyncio
async def test_the_url_we_reached_a_peer_on_is_kept_when_it_reports_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer's own `listen_url` may name an interface we cannot use. The
    address that just answered is reachable by definition, so it is recorded
    rather than discarded."""
    from netllm_discovery import swarm as swarm_mod

    registry = swarm_mod.SwarmRegistry(NetllmConfig())

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"agent_id": "multi", "listen_url": "http://192.168.1.5:11400"}

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(swarm_mod.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    record = await registry.fetch_peer("http://10.0.0.5:11400")

    assert record is not None
    assert record.listen_url == "http://192.168.1.5:11400"
    assert record.also_reachable_at == ["http://10.0.0.5:11400"]


# ------------------------------------------------------- what we send


def test_this_agent_advertises_what_it_actually_serves() -> None:
    """The heartbeat body is `status_payload()` verbatim, so this is the only
    place a peer's provider mix originates."""
    service = _service()
    service.pool.set_backends(
        [
            Backend(
                id="ollama",
                base_url="http://127.0.0.1:11434/v1",
                provider="ollama",
                local=True,
                health=BackendHealth(models=["a", "b"], model_count=2),
            ),
            Backend(
                id="peer:other",
                base_url="http://10.0.0.9:11400/v1",
                provider="custom",
                local=False,
                health=BackendHealth(models=["x"], model_count=1),
            ),
        ]
    )
    payload = service.status_payload()

    # Local rows only: a remote row here is this agent's view of ANOTHER
    # agent, and echoing it would inflate catalogs around the mesh.
    assert payload["providers"] == [
        {"id": "ollama", "provider": "ollama", "model_count": 2}
    ]


def test_a_single_address_bind_advertises_no_alternates() -> None:
    """The point of `also_reachable_at` is to stop a client guessing. An agent
    bound to one concrete address is reachable at exactly that address."""
    service = _service(listen="127.0.0.1:11400")
    assert service.status_payload()["also_reachable_at"] == []


# ------------------------------------------------------------- the page


PEERS_JS = (
    Path(__file__).resolve().parents[1]
    / "packages/netllm-agent/src/netllm_agent/static/pages/peers.js"
).read_text(encoding="utf-8")


def test_the_peers_page_reads_provenance_instead_of_hedging() -> None:
    """The page used to hard-code "heartbeat" for every connected agent and
    said so in a comment. It now reads the field, and the hedge is gone."""
    assert 'row.sources.has("live")) parts.push("heartbeat")' not in PEERS_JS
    assert "p.discovered_via" in PEERS_JS
    assert "PEER_DISCOVERY_LABELS" in PEERS_JS


def test_the_peers_page_no_longer_keeps_its_own_scan_clock() -> None:
    """`peersLastScanAt` was tab-local: it reset on reload and knew nothing
    about scans this page did not trigger. `status.discovery.last_peer_scan_at`
    replaces it."""
    assert "peersLastScanAt" not in PEERS_JS
    assert "discovery?.last_peer_scan_at" in PEERS_JS


def test_the_peers_page_reads_reach_and_providers_off_live_rows() -> None:
    assert "p.also_reachable_at" in PEERS_JS
    assert "peersProviderSummary" in PEERS_JS
