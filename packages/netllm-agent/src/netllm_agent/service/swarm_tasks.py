"""Background swarm lifecycle — cluster ``swarm_tasks.py`` (plan §1).

mDNS advertise/browse, heartbeat intake, the re-discovery loop, the subnet
scan fallback and background-task bookkeeping.

[Seam S3] ``_maybe_follow_gateway`` still decides whether to adopt the
gateway's strategy, but the write goes through
``core.apply_runtime_strategy`` instead of assigning
``self.config.routing.default_strategy`` from here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from netllm_discovery.swarm import (
    PeerRecord,
    normalize_peer_endpoints,
    normalize_peer_providers,
    normalize_peer_urls,
)

# discover_lan_agents tags every row with where it came from; PeerRecord
# names the same four things slightly differently. Anything unrecognised
# falls back to the honest default rather than guessing.
_SCAN_SOURCE_TO_DISCOVERY = {
    "mdns": "mdns",
    "config": "static",
    "subnet": "subnet_scan",
    "scan": "subnet_scan",
}

logger = logging.getLogger(__name__)

__all__ = ["SwarmTasksMixin"]


class SwarmTasksMixin:
    """Everything that runs outside a request."""

    def _maybe_follow_gateway(self, payload: dict[str, Any]) -> None:
        """Adopt the gateway's strategy (runtime only) on peer-role agents.

        Prevents accidental strategy drift across the mesh: the gateway
        is authoritative unless routing.follow_gateway = false.
        """
        from netllm_core.routing_policy import VALID_STRATEGIES

        if not self.config.routing.follow_gateway:
            return
        if self.config.agent.role == "gateway":
            return
        if payload.get("role") != "gateway":
            return
        remote = str(payload.get("routing_strategy") or "")
        if not remote or remote not in VALID_STRATEGIES:
            return
        if remote == self.config.routing.default_strategy:
            return
        logger.info(
            "adopting gateway strategy %r (was %r; routing.follow_gateway)",
            remote,
            self.config.routing.default_strategy,
        )
        self.apply_runtime_strategy(remote)

    async def handle_heartbeat(self, payload: dict[str, Any]) -> None:
        agent_id = payload.get("agent_id", "")
        if not agent_id or agent_id == self.config.agent.agent_id:
            return
        self._maybe_follow_gateway(payload)
        self.swarm.register_peer(
            PeerRecord(
                agent_id=agent_id,
                listen_url=payload.get("listen_url", ""),
                role=payload.get("role", "peer"),
                hostname=payload.get("hostname", ""),
                backends=payload.get("backends", []),
                routing_strategy=payload.get("routing_strategy", ""),
                version=payload.get("version", ""),
                max_concurrency=int(payload.get("max_concurrency", 0) or 0),
                draining=bool(payload.get("draining", False)),
                # UI-4a, absent-tolerant: a peer on an older build sends
                # neither key and reads as [] / [], which is the correct
                # outcome — the UI shows "—" rather than inventing a mix.
                # discovered_via is deliberately NOT read from the body: the
                # sender cannot know how we found it, and register_peer
                # carries forward a more specific answer if one exists.
                also_reachable_at=normalize_peer_urls(payload.get("also_reachable_at")),
                reachable_at=normalize_peer_endpoints(payload.get("reachable_at")),
                providers=normalize_peer_providers(payload.get("providers")),
            )
        )
        await self.refresh_local_backends()

    def _try_start_mdns(self) -> Exception | None:
        """Start (or re-start) the mDNS advertiser + browser.

        Returns the failure, or None on success. Safe to call again
        later: a startup name collision (e.g. a draining predecessor
        process still registered) must not disable LAN advertising for
        the whole agent lifetime — the rediscovery loop retries.
        """
        if not (self.config.agent.advertise and self.config.swarm.mdns):
            return None
        if self._mdns_advertiser is not None:
            return None
        loop = asyncio.get_running_loop()
        try:
            from netllm_discovery.mdns import MdnsAdvertiser, MdnsBrowser

            self._mdns_advertiser = MdnsAdvertiser(
                self.config.agent.listen,
                self.config.agent.agent_id,
                self.config.agent.role,
            )
            self._mdns_advertiser.start()

            async def on_peer(url: str, props: dict[str, str]) -> None:
                agent_id = props.get("agent_id", url)
                if agent_id == self.config.agent.agent_id:
                    return
                if props.get("reachable") == "false":
                    # Loopback-bound peer — fetching its advertised URL
                    # would hit our own agent. Surfaced by `netllm peers`.
                    logger.info(
                        "mDNS peer %s is loopback-bound (unreachable); "
                        "it must serve with --host 0.0.0.0 to join",
                        agent_id,
                    )
                    return
                record = await self.swarm.fetch_peer(url, discovered_via="mdns")
                if record:
                    self.swarm.register_peer(record)
                else:
                    self.swarm.register_peer(
                        PeerRecord(
                            agent_id=agent_id,
                            listen_url=url,
                            role=props.get("role", "peer"),
                            discovered_via="mdns",
                        )
                    )
                await self.refresh_local_backends()

            def on_peer_sync(url: str, props: dict[str, str]) -> None:
                # _spawn_background retains the task reference —
                # a bare create_task here could be GC'd mid-flight.
                coro = on_peer(url, props)
                loop.call_soon_threadsafe(self._spawn_background, coro)

            self._mdns_browser = MdnsBrowser(on_peer_sync)
            self._mdns_browser.start()
            return None
        except Exception as exc:
            logger.warning("mDNS startup failed: %s", exc)
            if self._mdns_advertiser:
                self._mdns_advertiser.stop()
                self._mdns_advertiser = None
            if self._mdns_browser:
                self._mdns_browser.stop()
                self._mdns_browser = None
            return exc

    def start_background(self) -> list[str]:
        warnings: list[str] = []

        if self.config.agent.advertise and self.config.swarm.mdns:
            exc = self._try_start_mdns()
            if exc is not None:
                warnings.append(
                    f"Swarm mDNS disabled ({exc}). "
                    "A prior netllm serve may still be registered — the "
                    "agent retries automatically every "
                    f"{self.config.swarm.rediscover_interval_s:.0f}s. "
                    "Static peers in swarm.peers still work."
                )
        elif self.config.swarm.mdns and not self.config.agent.advertise:
            warnings.append(
                "swarm.mdns is true but agent.advertise is false — "
                "this agent will not broadcast on the LAN."
            )

        if self.config.swarm.subnet_scan:
            warnings.append(
                "subnet_scan enabled — probing LAN for agents on :11400 at startup."
            )

        self.swarm.start_gossip(lambda: self.status_payload())
        if self.config.swarm.subnet_scan:
            self._spawn_background(self._discover_subnet_peers())
        elif self._should_auto_subnet_fallback():
            self._spawn_background(self._mdns_fallback_subnet_scan())
        if self.config.swarm.rediscover_interval_s > 0:
            self._spawn_background(self._rediscovery_loop())
        self.startup_warnings = warnings
        return warnings

    async def _rediscovery_loop(self) -> None:
        """Bring back peers lost to sleep / Wi-Fi blips without a restart.

        The registry prunes peers after peer_stale_after_s; mDNS is
        edge-triggered and the subnet scan is one-shot, so without this
        loop a bidirectional heartbeat gap removes a peer permanently.
        """
        while True:
            interval = self.config.swarm.rediscover_interval_s
            if interval <= 0:
                return
            await asyncio.sleep(interval)
            try:
                # A startup mDNS failure (name collision with a draining
                # predecessor) must heal once the old process exits.
                if (
                    self.config.agent.advertise
                    and self.config.swarm.mdns
                    and self._mdns_advertiser is None
                    and self._try_start_mdns() is None
                ):
                    logger.info("mDNS advertiser recovered on retry")
                lost = self.swarm.lost_peer_urls()
                recovered = 0
                for url in lost:
                    record = await self.swarm.fetch_peer(url)
                    if record and record.agent_id != self.config.agent.agent_id:
                        self.swarm.register_peer(record)
                        recovered += 1
                if recovered:
                    await self.refresh_local_backends()
                    logger.info("re-discovery recovered %s peer(s)", recovered)
                if not self.swarm.peers and self.config.swarm.subnet_scan:
                    await self._discover_subnet_peers()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("re-discovery pass failed: %s", exc)

    def _spawn_background(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _should_auto_subnet_fallback(self) -> bool:
        """One-shot subnet scan when mDNS is on but may be blocked.

        Only for LAN binds: loopback-bound agents cannot mesh anyway, and
        default single-machine installs should never probe the subnet.
        """
        from netllm_discovery.lan import is_loopback_url

        if not (self.config.swarm.mdns and self.config.agent.advertise):
            return False
        return not is_loopback_url(self.swarm.local_agent_url())

    async def _mdns_fallback_subnet_scan(self, delay_s: float = 10.0) -> None:
        await asyncio.sleep(delay_s)
        if self.swarm.peers:
            return
        logger.info(
            "mDNS found no peers after %.0fs — running one-time subnet "
            "scan fallback (disable by adding static swarm.peers)",
            delay_s,
        )
        await self._discover_subnet_peers()

    async def _discover_subnet_peers(self) -> None:
        from netllm_discovery.lan import discover_lan_agents

        try:
            peers = await discover_lan_agents(
                self.config,
                use_mdns=False,
                use_subnet=True,
                timeout_s=0,
            )
            for data in peers:
                self.swarm.register_peer(
                    PeerRecord(
                        agent_id=data.get("agent_id", ""),
                        listen_url=data.get("listen_url", ""),
                        role=data.get("role", "peer"),
                        hostname=data.get("hostname", ""),
                        backends=data.get("backends", []),
                        # discover_lan_agents merges mDNS, static peers and
                        # the subnet probe into one list and tags each row,
                        # so this pass is not uniformly "subnet_scan".
                        discovered_via=_SCAN_SOURCE_TO_DISCOVERY.get(
                            str(data.get("source", "")), "subnet_scan"
                        ),
                        also_reachable_at=normalize_peer_urls(
                            data.get("also_reachable_at")
                        ),
                        reachable_at=normalize_peer_endpoints(data.get("reachable_at")),
                        providers=normalize_peer_providers(data.get("providers")),
                    )
                )
            if peers:
                await self.refresh_local_backends()
                logger.info("subnet scan found %s peer agent(s)", len(peers))
        except Exception as exc:
            logger.warning("subnet peer scan failed: %s", exc)

    def stop_background(self) -> None:
        self.swarm.stop_gossip()
        if self._mdns_advertiser:
            self._mdns_advertiser.stop()
        if self._mdns_browser:
            self._mdns_browser.stop()
