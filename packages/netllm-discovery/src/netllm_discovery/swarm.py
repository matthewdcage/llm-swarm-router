"""Swarm peer registry and heartbeat merge."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from netllm_core.models import Backend, BackendHealth, NetllmConfig

from netllm_discovery.lan import is_lan_reachable_agent_url

logger = logging.getLogger(__name__)

# How a peer was learned about. Set at the point of discovery — the mDNS
# listener, the subnet scanner, the static-peer loader — never inferred
# afterwards. "heartbeat" is the honest answer for a peer that simply
# started talking to us and is the only value an older build can produce.
DISCOVERY_SOURCES = frozenset({"mdns", "subnet_scan", "static", "heartbeat", "join"})
DEFAULT_DISCOVERY_SOURCE = "heartbeat"

# Ceiling on the per-peer provider summary carried in every heartbeat. The
# body is gossiped to every peer every heartbeat_interval_s, so this list is
# multiplied by peers × interval; a machine with a pathological number of
# configured endpoints must not turn gossip into a bandwidth problem. Model
# *names* are deliberately not carried at all — they are already reachable
# via the peer's own /v1/models.
MAX_PEER_PROVIDERS = 32
# Same reasoning for the alternate-address set on a multi-homed host.
MAX_PEER_ALSO_REACHABLE = 8


def normalize_peer_providers(raw: Any) -> list[dict[str, Any]]:
    """Coerce a heartbeat's ``providers`` into ``[{id, provider, model_count}]``.

    Wire data: a peer running a different build (or nothing at all) may send
    a string, a list of strings, nulls, or negative counts. Absent or
    unreadable means "this peer told us nothing", i.e. ``[]`` — never an
    exception, and never a partially-built row.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id", "") or "")
        if not pid:
            continue
        try:
            count = int(entry.get("model_count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        out.append(
            {
                "id": pid,
                "provider": str(entry.get("provider", "") or ""),
                "model_count": max(0, count),
            }
        )
        if len(out) >= MAX_PEER_PROVIDERS:
            break
    return out


def normalize_peer_urls(raw: Any) -> list[str]:
    """Coerce a heartbeat's ``also_reachable_at`` into a clean URL list."""
    if isinstance(raw, str) or not isinstance(raw, (list, tuple, set)):
        return []
    seen: list[str] = []
    for item in raw:
        url = str(item or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.append(url)
        if len(seen) >= MAX_PEER_ALSO_REACHABLE:
            break
    return seen


def normalize_peer_endpoints(raw: Any) -> list[dict[str, str]]:
    """Coerce a heartbeat's ``reachable_at`` into ``[{url, kind, interface}]``.

    Same wire-data discipline as ``normalize_peer_urls``: a peer on another
    build may send a string, nulls, or entries that are not objects. A ``kind``
    this build does not recognise is kept verbatim — a newer agent may know a
    classification we do not, and every client already prints an unrecognised
    label rather than dropping the row.
    """
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url", "") or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "url": url,
                "kind": str(entry.get("kind", "") or ""),
                "interface": str(entry.get("interface", "") or ""),
            }
        )
        # +1: this list carries the peer's advertised URL as well as its
        # alternates, which the flat list does not.
        if len(out) >= MAX_PEER_ALSO_REACHABLE + 1:
            break
    return out


@dataclass
class PeerRecord:
    agent_id: str
    listen_url: str
    role: str = "peer"
    hostname: str = ""
    last_seen: float = field(default_factory=time.time)
    backends: list[dict[str, Any]] = field(default_factory=list)
    # Advertised by heartbeats/status for config-drift detection; empty
    # when the peer predates these fields.
    routing_strategy: str = ""
    version: str = ""
    # Self-declared by the peer's own agent.max_concurrency (0 = peer
    # imposes no ceiling of its own). Copied onto its routable Backend
    # row in peer_agent_backends() so pool.select_backend's capacity
    # guard respects it.
    max_concurrency: int = 0
    # Peer asked (via its own drain toggle) not to receive new work.
    # Existing in-flight requests it's already serving are unaffected —
    # this only removes it from future selection.
    draining: bool = False
    # ---- UI-4a. All three are absent-tolerant in both directions: an older
    # peer omits them and reads as the defaults below; a newer peer sending
    # them is ignored field-by-field by an older build, which constructs
    # PeerRecord from explicit .get() calls (docs/mesh-upgrade.md skew
    # promise, same convention as max_concurrency/draining above).
    #
    # How this agent learned about the peer. Receiver-side knowledge — it is
    # NOT carried in the heartbeat, because "how you found me" is not a fact
    # the peer possesses.
    discovered_via: str = DEFAULT_DISCOVERY_SOURCE
    # Other URLs this same agent answers on (multi-homed: Wi-Fi + Ethernet,
    # DHCP drift). Union of what the peer advertises about itself and what
    # this agent has observed it at.
    also_reachable_at: list[str] = field(default_factory=list)
    # UI-4a. The peer's own classification of those addresses:
    # [{url, kind, interface}], kinds from netllm_discovery.lan.ADDRESS_KINDS,
    # ordered most-useful-first and including the peer's advertised URL. It is
    # a *lookup keyed on url*, not a replacement for also_reachable_at: this
    # agent also observes addresses the peer never advertised (a subnet probe
    # answering on an IP the peer does not know it has), and those stay in the
    # flat list with no classification. Empty for a peer that predates it —
    # which reads as "unclassified", not "no alternates".
    reachable_at: list[dict[str, str]] = field(default_factory=list)
    # Summary of what the peer actually serves: [{id, provider, model_count}].
    # A peer is materialised as ONE Backend with provider="custom"
    # (peer_agent_backends below), so without this its real provider mix is
    # invisible to every other agent in the mesh.
    providers: list[dict[str, Any]] = field(default_factory=list)


class SwarmRegistry:
    """Tracks local and remote swarm members."""

    def __init__(self, config: NetllmConfig) -> None:
        self.config = config
        self.peers: dict[str, PeerRecord] = {}
        # Every listen_url ever registered — re-discovery re-probes these
        # so peers lost to sleep/network blips rejoin without a restart.
        self.known_peer_urls: set[str] = set()
        # listen_url -> the specific discovery mechanism last recorded for it.
        # Outlives the PeerRecord: prune_stale drops peers after
        # peer_stale_after_s, and the rediscovery loop then re-fetches them
        # from known_peer_urls with no idea how they were originally found.
        # Without this, every laptop that closes its lid comes back labelled
        # "heartbeat".
        self._url_discovery: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None

    def local_agent_url(self) -> str:
        from netllm_discovery.lan import agent_url_from_listen

        return agent_url_from_listen(self.config.agent.listen)

    def register_peer(self, record: PeerRecord) -> None:
        record.last_seen = time.time()
        previous = self.peers.get(record.agent_id)
        if previous is not None:
            self._carry_forward_provenance(previous, record)
        record.also_reachable_at = normalize_peer_urls(record.also_reachable_at)
        record.reachable_at = normalize_peer_endpoints(record.reachable_at)
        url = record.listen_url.rstrip("/")
        if record.discovered_via == DEFAULT_DISCOVERY_SOURCE:
            remembered = self._url_discovery.get(url, "")
            if remembered in DISCOVERY_SOURCES:
                record.discovered_via = remembered
        elif record.discovered_via not in DISCOVERY_SOURCES:
            record.discovered_via = DEFAULT_DISCOVERY_SOURCE
        self.peers[record.agent_id] = record
        if url:
            self.known_peer_urls.add(url)
            if record.discovered_via != DEFAULT_DISCOVERY_SOURCE:
                self._url_discovery[url] = record.discovered_via

    @staticmethod
    def _carry_forward_provenance(previous: PeerRecord, record: PeerRecord) -> None:
        """Keep what re-registration would otherwise erase.

        A peer found over mDNS starts heartbeating within one interval, and
        ``handle_heartbeat`` rebuilds the record from the heartbeat body —
        which cannot say how we found the sender. Overwriting blindly would
        relabel every peer in the mesh as "heartbeat" within seconds of
        discovery, which is exactly the state the Peers page is stuck in
        today. The first *specific* answer therefore wins; only a later
        discovery that is itself specific may replace it (a pinned peer that
        later turns up over mDNS keeps the pin as its origin, because
        ``register_peer`` sees "static" already recorded).

        Alternate addresses accumulate for the same reason: they are observed
        one probe at a time and a heartbeat that mentions none must not erase
        the ones a subnet scan found.
        """
        if (
            record.discovered_via == DEFAULT_DISCOVERY_SOURCE
            and previous.discovered_via in DISCOVERY_SOURCES
        ):
            record.discovered_via = previous.discovered_via
        current = record.listen_url.rstrip("/")
        merged = list(record.also_reachable_at)
        for url in [*previous.also_reachable_at, previous.listen_url.rstrip("/")]:
            if url and url != current and url not in merged:
                merged.append(url)
        record.also_reachable_at = merged
        # Classifications accumulate the same way, keyed on url: a probe that
        # carries no `reachable_at` (an older peer, or a scan row) must not
        # erase the labels a heartbeat already supplied.
        known = {entry.get("url", "") for entry in record.reachable_at}
        record.reachable_at = [
            *record.reachable_at,
            *(e for e in previous.reachable_at if e.get("url", "") not in known),
        ]

    def stale_peers(self, max_age_s: float | None = None) -> list[str]:
        max_age = (
            max_age_s if max_age_s is not None else self.config.swarm.peer_stale_after_s
        )
        now = time.time()
        return [pid for pid, p in self.peers.items() if now - p.last_seen > max_age]

    def prune_stale(self, max_age_s: float | None = None) -> None:
        for pid in self.stale_peers(max_age_s):
            del self.peers[pid]

    def lost_peer_urls(self) -> list[str]:
        """Previously seen peer URLs with no live registry entry."""
        live = {p.listen_url.rstrip("/") for p in self.peers.values()}
        return sorted(self.known_peer_urls - live)

    def _peer_local_rows(self, peer: PeerRecord) -> list[Backend]:
        """Validated `local=true` rows from a peer's heartbeat payload.

        Remote rows in a peer's status are its own view of *other*
        agents; using those here would echo backends transitively
        around the mesh, inflate catalogs, and invite multi-hop chains.
        """
        rows: list[Backend] = []
        for raw in peer.backends:
            try:
                b = Backend.model_validate(raw)
            except Exception:
                logger.debug("skip invalid peer backend: %s", raw)
                continue
            if b.local:
                rows.append(b)
        return rows

    def _peer_backend_models(self, peer: PeerRecord) -> list[str]:
        """Union model IDs the peer serves directly (local rows only)."""
        models: set[str] = set()
        for b in self._peer_local_rows(peer):
            models.update(b.health.models)
        return sorted(models)

    def _peer_in_flight(self, peer: PeerRecord) -> int:
        """Heartbeat-reported concurrent load on the peer's own providers."""
        return sum(max(0, b.in_flight) for b in self._peer_local_rows(peer))

    def peer_agent_backends(self) -> list[Backend]:
        """One routable backend per peer: the peer's agent OpenAI surface (/v1).

        A draining peer is omitted entirely — it asked not to receive new
        work, so it must vanish from every strategy's candidate list.
        Requests it's already serving are unaffected (this only stops
        *future* selection, on this gateway and every other one that
        receives its heartbeat).
        """
        out: list[Backend] = []
        for peer in self.peers.values():
            if peer.agent_id == self.config.agent.agent_id:
                continue
            if peer.draining:
                continue
            listen = peer.listen_url.rstrip("/")
            if not is_lan_reachable_agent_url(listen):
                logger.debug("skip peer with loopback listen_url: %s", listen)
                continue
            agent_base = f"{listen}/v1"
            models = self._peer_backend_models(peer)
            out.append(
                Backend(
                    id=f"peer:{peer.agent_id}",
                    base_url=agent_base,
                    provider="custom",
                    local=False,
                    agent_id=peer.agent_id,
                    health=BackendHealth(models=models, model_count=len(models)),
                    in_flight=self._peer_in_flight(peer),
                    max_concurrency=max(0, peer.max_concurrency),
                )
            )
        return out

    async def fetch_peer(
        self,
        base_url: str,
        *,
        discovered_via: str = DEFAULT_DISCOVERY_SOURCE,
    ) -> PeerRecord | None:
        url = base_url.rstrip("/") + "/netllm/v1/status"
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                probed = base_url.rstrip("/")
                reported = str(data.get("listen_url", "") or "").rstrip("/")
                # The URL we actually reached it on is reachable by
                # definition; the one it reports about itself may be a
                # loopback or a different interface. Keep both.
                also = normalize_peer_urls(data.get("also_reachable_at"))
                if probed and probed != reported and probed not in also:
                    also.append(probed)
                return PeerRecord(
                    agent_id=data.get("agent_id", ""),
                    listen_url=reported or probed,
                    role=data.get("role", "peer"),
                    hostname=data.get("hostname", ""),
                    backends=data.get("backends", []),
                    routing_strategy=data.get("routing_strategy", ""),
                    version=data.get("version", ""),
                    max_concurrency=int(data.get("max_concurrency", 0) or 0),
                    draining=bool(data.get("draining", False)),
                    discovered_via=discovered_via,
                    also_reachable_at=also,
                    reachable_at=normalize_peer_endpoints(data.get("reachable_at")),
                    providers=normalize_peer_providers(data.get("providers")),
                )
        except Exception as exc:
            logger.debug("peer fetch failed %s: %s", base_url, exc)
            return None

    async def send_heartbeat(self, payload: dict[str, Any], peer_url: str) -> bool:
        url = peer_url.rstrip("/") + "/netllm/v1/heartbeat"
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                return resp.status_code in (200, 204)
        except Exception:
            return False

    async def refresh_static_peers(self) -> None:
        """Re-fetch every configured static peer concurrently.

        Sequential awaits here (and in the heartbeat fan-out below) meant one
        unreachable peer added its full 5 s timeout to the cycle: with a
        10 s interval and three dead peers the effective period passed the
        45 s stale window, so healthy peers were pruned because a *different*
        peer was down (docs/architecture/07-findings-register.md F-12).
        """
        peers = list(self.config.swarm.peers)
        if not peers:
            return
        records = await self._gather_bounded(
            [self.fetch_peer(peer_url, discovered_via="static") for peer_url in peers]
        )
        for record in records:
            if isinstance(record, PeerRecord):
                self.register_peer(record)

    @staticmethod
    async def _gather_bounded(coros: list[Any], *, concurrency: int = 32) -> list[Any]:
        """gather() with a ceiling, mirroring lan.subnet_scan_agents.

        Exceptions are returned rather than raised: one unreachable peer must
        never abort the rest of the cycle.
        """
        sem = asyncio.Semaphore(concurrency)

        async def run(coro: Any) -> Any:
            async with sem:
                return await coro

        return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)

    async def gossip_loop(
        self,
        status_provider: Any,
        *,
        interval_s: float | None = None,
    ) -> None:
        interval = interval_s or self.config.swarm.heartbeat_interval_s
        while True:
            try:
                await self.refresh_static_peers()
                self.prune_stale()
                payload = status_provider()
                targets = [
                    peer.listen_url
                    for peer in list(self.peers.values())
                    if peer.agent_id != self.config.agent.agent_id
                ]
                if targets:
                    await self._gather_bounded(
                        [self.send_heartbeat(payload, url) for url in targets]
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("gossip loop error: %s", exc)
            await asyncio.sleep(interval)

    def start_gossip(self, status_provider: Any) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self.gossip_loop(status_provider))

    def stop_gossip(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.swarm.cluster_token
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    def all_peer_urls(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": p.agent_id,
                "listen_url": p.listen_url,
                "role": p.role,
                "hostname": p.hostname,
                "last_seen": p.last_seen,
                "routing_strategy": p.routing_strategy,
                "version": p.version,
                # UI-4a. Present for every peer, whatever the peer's build:
                # `discovered_via` is this agent's own knowledge, and the
                # other two default to empty for a peer that sends neither.
                "discovered_via": p.discovered_via or DEFAULT_DISCOVERY_SOURCE,
                "also_reachable_at": list(p.also_reachable_at),
                # UI-4a. Classification for the URLs above; empty for a peer
                # whose build predates it, which every client reads as
                # "unclassified" and renders exactly as it did before.
                "reachable_at": [dict(entry) for entry in p.reachable_at],
                "providers": [dict(entry) for entry in p.providers],
            }
            for p in self.peers.values()
        ]
