"""Read-only status surfaces — cluster ``status.py`` (plan §1).

``/netllm/v1/status`` and the aggregated model catalog. Deliberately free of
write sinks: ``_update_health_metrics`` moved to ``backends.py`` (Seam S1),
and the telemetry writes moved to ``accounting.py``, which is what leaves
this module with no outgoing edge back into either.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from netllm_core.capabilities import model_capability
from netllm_core.update import compare_versions, is_version_like, mesh_skew
from netllm_core.version import get_version
from netllm_discovery.lan import (
    classified_agent_endpoints,
    is_lan_reachable_agent_url,
    last_peer_scan_at,
    local_ipv4_interfaces,
    local_lan_ip,
)
from netllm_discovery.local import (
    find_omlx_admin_url,
    last_provider_scan_at,
    probe_omlx_admin_for_backends,
)
from netllm_discovery.swarm import MAX_PEER_ALSO_REACHABLE, MAX_PEER_PROVIDERS

logger = logging.getLogger(__name__)

__all__ = ["StatusMixin"]


class StatusMixin:
    """What this agent believes about itself and its mesh."""

    def local_provider_summary(self) -> list[dict[str, Any]]:
        """`[{id, provider, model_count}]` for what THIS agent serves directly.

        Gossiped in the heartbeat (UI-4a). A peer is materialised remotely as
        a single ``Backend`` with ``provider="custom"``
        (``SwarmRegistry.peer_agent_backends``), so without this summary no
        other agent in the mesh can say what a peer actually runs.

        Local rows only, and no model *names*: remote rows in this payload are
        this agent's view of other agents, which would echo around the mesh,
        and names are already reachable via this agent's own ``/v1/models``.
        """
        out: list[dict[str, Any]] = []
        for b in self.pool.backends:
            if not b.enabled or not b.local:
                continue
            count = b.health.model_count or len(b.health.models)
            out.append(
                {
                    "id": b.id,
                    "provider": b.provider,
                    "model_count": max(0, int(count)),
                }
            )
            if len(out) >= MAX_PEER_PROVIDERS:
                break
        return out

    def own_reachable_endpoints(self) -> list[dict[str, str]]:
        """Where this agent answers, classified and ordered (UI-4a).

        ``[{"url", "kind", "interface"}]`` with the advertised ``listen_url``
        first and the rest ordered by ``lan.ADDRESS_KINDS``. This is the
        producer for ``also_reachable_at`` as well — the two keys are never
        computed separately, so they cannot disagree about the order.

        Only a wildcard bind earns any: ``agent_url_from_listen`` picks ONE
        address for a host with several interfaces (Wi-Fi + Ethernet, a VPN,
        Docker), so the advertised ``listen_url`` is a peer's only way in even
        though the socket answers on all of them. A bind to one concrete
        address is reachable at exactly that address and gets ``[]`` — the
        point of these keys is to stop a client guessing, not to make it guess
        more confidently.

        Loopback is excluded: it resolves to the *reader's* agent, not this
        one. Container-bridge addresses are kept, because "the host is
        reachable from your containers at 172.17.0.1" is an answer somebody
        wants — they are just labelled and ranked below the LAN address
        instead of being listed above it.

        Memoized on ``agent.listen``: this runs on every heartbeat and every
        ``/status`` read.
        """
        listen = self.config.agent.listen
        cached = getattr(self, "_own_endpoints_cache", None)
        if cached is not None and cached[0] == listen:
            return [dict(entry) for entry in cached[1]]
        endpoints = self._compute_reachable_endpoints(listen)
        self._own_endpoints_cache = (listen, endpoints)
        return [dict(entry) for entry in endpoints]

    def own_alternate_urls(self) -> list[str]:
        """The URLs from ``own_reachable_endpoints`` other than the advertised one."""
        primary = self.swarm.local_agent_url().rstrip("/")
        return [e["url"] for e in self.own_reachable_endpoints() if e["url"] != primary]

    def _compute_reachable_endpoints(self, listen: str) -> list[dict[str, str]]:
        from netllm_core.models import split_listen

        if listen.startswith("http"):
            return []
        host, port = split_listen(listen)
        if host not in ("", "0.0.0.0", "::"):
            return []
        primary = self.swarm.local_agent_url().rstrip("/")
        rows = local_ipv4_interfaces()
        # The socket-derived primary LAN IP is authoritative even on a
        # platform that refuses interface enumeration; an unnamed interface
        # classifies as "lan", and a named row for the same address (from
        # psutil, listed first) wins the dedupe in classified_agent_endpoints.
        lan_ip = local_lan_ip()
        if lan_ip:
            rows.append(("", lan_ip))
        endpoints = classified_agent_endpoints(
            port, interfaces=rows, primary_url=primary
        )
        # Loopback never goes on the wire; the cap is the same one the
        # receiving side applies to also_reachable_at, so a host with a
        # pathological interface list cannot inflate every heartbeat. The
        # ordering above means truncation drops the least useful addresses.
        usable = [e for e in endpoints if is_lan_reachable_agent_url(e["url"])]
        return usable[: MAX_PEER_ALSO_REACHABLE + 1]

    def discovery_scan_payload(self) -> dict[str, Any]:
        """When each discovery pass last completed, in epoch seconds (UI-3).

        ``None`` means "has not run in this process", which is a different
        statement from "ran and found nothing" and must stay distinguishable.
        Both clocks are stamped by the scans themselves, so a scan triggered
        by the rediscovery timer moves them just as an admin-triggered one
        does.
        """
        provider_at = last_provider_scan_at()
        peer_at = last_peer_scan_at()
        return {
            "last_scan_at": provider_at or None,
            "last_peer_scan_at": peer_at or None,
        }

    def status_payload(self) -> dict[str, Any]:
        omlx_admin = find_omlx_admin_url(self.pool.backends)
        payload: dict[str, Any] = {
            "agent_id": self.config.agent.agent_id,
            "hostname": self.config.agent.hostname,
            "role": self.config.agent.role,
            "listen_url": self.swarm.local_agent_url(),
            "backends": [b.model_dump(mode="json") for b in self.pool.backends],
            "peers": self.swarm.all_peer_urls(),
            "routing_strategy": self.config.routing.default_strategy,
            "routed_requests": dict(self.pool.routed_counts),
            "capacity_rejections": dict(self.pool.capacity_rejections),
            "source_requests": dict(self._source_counts),
            "scenario_requests": {
                f"{source_id}:{scenario}": count
                for (source_id, scenario), count in self._scenario_counts.items()
            },
            "shardless_fallbacks": self._shardless_fallbacks,
            "cluster_token_set": bool(self.config.swarm.cluster_token),
            "version": get_version(),
            "max_concurrency": self.config.agent.max_concurrency,
            "draining": self.draining,
            # UI-4a. This body is sent verbatim as the heartbeat
            # (`gossip_loop(status_provider=status_payload)`), so these two
            # keys are how a peer learns what this machine serves and where
            # else it answers. Additive: an older peer ignores both.
            "providers": self.local_provider_summary(),
            "also_reachable_at": self.own_alternate_urls(),
            # UI-4a. The same addresses, classified and ordered
            # (`netllm_discovery.lan.ADDRESS_KINDS`), so a client can tell a
            # LAN address from a Docker bridge gateway without re-deriving
            # "is 172.x a bridge?" — which it cannot, having no view of this
            # host's interfaces. Strictly additive: `also_reachable_at` is
            # unchanged and still complete on its own.
            "reachable_at": self.own_reachable_endpoints(),
            # UI-3. Wall clocks for the two discovery passes, so a client can
            # age them without subtracting our monotonic clock from its own.
            "discovery": self.discovery_scan_payload(),
            "cloud": {
                "enabled": self.config.cloud.enabled,
                "fallback": self.config.cloud.fallback,
                "fallback_enabled": self.config.cloud.fallback_enabled,
                "enabled_providers": sorted(
                    pid
                    for pid, pcfg in self.config.cloud.providers.items()
                    if pcfg.enabled
                ),
            },
        }
        warnings = self.peer_config_warnings()
        if warnings:
            payload["peer_warnings"] = warnings
        if omlx_admin:
            payload["omlx_admin_url"] = omlx_admin
        return payload

    def peer_config_warnings(self) -> list[str]:
        """Config/version drift between this agent and live peers.

        Mismatched strategies are legal (routing is per-gateway) but
        usually unintentional — surface them instead of letting two
        machines silently run different policies for weeks.
        """
        warnings: list[str] = []
        my_strategy = self.config.routing.default_strategy
        my_version = get_version()
        for peer in self.swarm.peers.values():
            if peer.agent_id == self.config.agent.agent_id:
                continue
            name = peer.hostname or peer.agent_id
            if peer.routing_strategy and peer.routing_strategy != my_strategy:
                warnings.append(
                    f"peer {name} runs strategy '{peer.routing_strategy}' "
                    f"but this agent runs '{my_strategy}' — set both to the "
                    "same value (or 'auto') unless intentional"
                )
            if not peer.version:
                continue
            if not is_version_like(peer.version):
                # A version string this build cannot read is NOT version 0.
                # It used to be treated as one, so a peer sending anything
                # unparseable produced a confident "more than two minors of
                # skew, the mesh is not expected to work" about a version
                # nobody is running. Say what was actually observed and stop.
                warnings.append(
                    f"peer {name} reports an unreadable netllm version "
                    f"{peer.version!r} — version skew cannot be assessed for "
                    "this peer; check what is running on it"
                )
                continue
            # String inequality flagged "0.5.0.1" against "v0.5.0.1" as drift
            # and told the operator to "update the older machine" without
            # saying which one — advice that is wrong half the time. Order
            # them with the same comparator the updater uses.
            #
            # That comparator was itself wrong about prereleases until Phase 6
            # (netllm_core.update._version_key): it read "0.5.0rc1" as
            # [0,5,0,1], so a peer on a release candidate looked NEWER than one
            # on the final release and identical to one on 0.5.0.1. This is the
            # exposed caller for that bug — `fetch_latest_release` filters
            # prereleases out, this does not. Ordering is now pinned by
            # tests/contract/version-ordering.json.
            order = compare_versions(my_version, peer.version)
            if order == 0:
                continue
            older, newer = (
                (f"peer {name}", "this agent")
                if order > 0
                else ("this agent", f"peer {name}")
            )
            skew = mesh_skew(my_version, peer.version)
            warnings.append(
                f"peer {name} runs netllm {peer.version} but this agent "
                f"runs {my_version} — {older} is older than {newer}; "
                f"{skew.advice}"
            )
        return warnings

    async def status_payload_enriched(self) -> dict[str, Any]:
        payload = self.status_payload()
        omlx_stats = await probe_omlx_admin_for_backends(self.pool.backends)
        if omlx_stats:
            payload["omlx_stats"] = omlx_stats
        return payload

    async def list_models_aggregated(self) -> dict[str, Any]:
        await self.refresh_local_backends()
        self._materialize_cloud_provider_backends()

        def _probe_local() -> None:
            # Force-probe local providers only. Peer-agent rows are kept
            # fresh by heartbeats; probing them from a catalog handler
            # recurses (the peer's handler would probe us back).
            for b in self.pool.backends:
                if b.enabled and b.local:
                    self.pool.is_healthy(b, force_refresh=True)

        await asyncio.to_thread(_probe_local)
        seen: dict[str, dict[str, Any]] = {}
        for b in self.pool.backends:
            if not b.enabled:
                continue
            for mid in b.health.models:
                if mid not in seen:
                    seen[mid] = {
                        "id": mid,
                        "object": "model",
                        "owned_by": b.provider,
                        "capability": model_capability(mid),
                    }
        # Surface canonical alias names whose provider-specific IDs exist.
        for canonical, alias_ids in self.config.routing.model_aliases.items():
            if canonical in seen:
                continue
            if any(m == a or m.startswith(a + ":") for a in alias_ids for m in seen):
                seen[canonical] = {
                    "id": canonical,
                    "object": "model",
                    "owned_by": "netllm-alias",
                    "capability": model_capability(canonical),
                }
        return {"object": "list", "data": list(seen.values())}
