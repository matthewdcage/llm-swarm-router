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


@dataclass
class PeerRecord:
    agent_id: str
    listen_url: str
    role: str = "peer"
    hostname: str = ""
    last_seen: float = field(default_factory=time.time)
    backends: list[dict[str, Any]] = field(default_factory=list)


class SwarmRegistry:
    """Tracks local and remote swarm members."""

    def __init__(self, config: NetllmConfig) -> None:
        self.config = config
        self.peers: dict[str, PeerRecord] = {}
        self._task: asyncio.Task[None] | None = None

    def local_agent_url(self) -> str:
        from netllm_discovery.lan import agent_url_from_listen

        return agent_url_from_listen(self.config.agent.listen)

    def register_peer(self, record: PeerRecord) -> None:
        record.last_seen = time.time()
        self.peers[record.agent_id] = record

    def stale_peers(self, max_age_s: float = 45.0) -> list[str]:
        now = time.time()
        return [pid for pid, p in self.peers.items() if now - p.last_seen > max_age_s]

    def prune_stale(self, max_age_s: float = 45.0) -> None:
        for pid in self.stale_peers(max_age_s):
            del self.peers[pid]

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
        """One routable backend per peer: the peer's agent OpenAI surface (/v1)."""
        out: list[Backend] = []
        for peer in self.peers.values():
            if peer.agent_id == self.config.agent.agent_id:
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
                    health=BackendHealth(models=models),
                    in_flight=self._peer_in_flight(peer),
                )
            )
        return out

    def peer_backends(self) -> list[Backend]:
        """Deprecated: use peer_agent_backends() for gateway routing."""
        return self.peer_agent_backends()

    async def fetch_peer(self, base_url: str) -> PeerRecord | None:
        url = base_url.rstrip("/") + "/netllm/v1/status"
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                return PeerRecord(
                    agent_id=data.get("agent_id", ""),
                    listen_url=data.get("listen_url", base_url),
                    role=data.get("role", "peer"),
                    hostname=data.get("hostname", ""),
                    backends=data.get("backends", []),
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
        for peer_url in self.config.swarm.peers:
            record = await self.fetch_peer(peer_url)
            if record:
                self.register_peer(record)

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
                for peer in list(self.peers.values()):
                    if peer.agent_id == self.config.agent.agent_id:
                        continue
                    await self.send_heartbeat(payload, peer.listen_url)
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

    def all_peer_urls(self) -> list[dict[str, str]]:
        return [
            {
                "agent_id": p.agent_id,
                "listen_url": p.listen_url,
                "role": p.role,
                "hostname": p.hostname,
                "last_seen": p.last_seen,
            }
            for p in self.peers.values()
        ]
