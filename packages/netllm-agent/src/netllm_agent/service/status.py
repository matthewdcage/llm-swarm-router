"""Read-only status surfaces — cluster ``status.py`` (plan §1).

``/netllm/v1/status`` and the aggregated model catalog. Deliberately free of
write sinks: ``_update_health_metrics`` moved to ``backends.py`` (Seam S1),
and the telemetry writes moved to ``accounting.py``, which is what leaves
this module with no outgoing edge back into either.
"""

from __future__ import annotations

import asyncio
from typing import Any

from netllm_core.capabilities import model_capability
from netllm_core.version import get_version
from netllm_discovery.local import find_omlx_admin_url, probe_omlx_admin_for_backends

__all__ = ["StatusMixin"]


class StatusMixin:
    """What this agent believes about itself and its mesh."""

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
            if peer.version and peer.version != my_version:
                warnings.append(
                    f"peer {name} runs netllm {peer.version} but this agent "
                    f"runs {my_version} — update the older machine"
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
