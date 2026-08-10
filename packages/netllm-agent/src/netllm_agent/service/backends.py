"""Backend discovery, pool refresh and upstream construction (plan §1).

Cluster ``backends.py``: the local provider scan (TTL-cached), the peer
merge, the health-metric publish, and the OpenAI/anthropic upstream client
construction with the peer loop-guard headers.

``scan_local_providers`` is imported *here*, which is what tests patch:
``patch("netllm_agent.service.backends.scan_local_providers")``.

[Seam S1] ``_update_health_metrics`` lives here rather than in ``status.py``.
It is a write sink, its callers are this module, the engine and the proxy
surfaces, and it had no caller inside ``status.py`` at all — leaving it there
was the sole cause of the ``backends.py`` ⇄ ``status.py`` cycle the AST
dependency graph found (dependency-graph.md §1.4).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path

from netllm_core.models import (
    DEFAULT_SOURCE_ID,
    HOPS_HEADER,
    LOCAL_ONLY_HEADER,
    SOURCE_HEADER,
    Backend,
)
from netllm_core.source_identity import resolve_source
from netllm_discovery.local import scan_local_providers, scan_results_to_backends
from netllm_sdk_openai.client import OpenAIUpstream

from netllm_agent.metrics import BACKEND_HEALTH, BACKEND_IN_FLIGHT

from .cloud import LEGACY_CLOUD_BACKEND_IDS

__all__ = ["BackendsMixin", "scan_local_providers"]


class BackendsMixin:
    """Discovery, pool refresh, health publish and upstream clients."""

    async def refresh_local_backends(
        self,
        *,
        persist_provider_urls: bool = False,
        config_path: Path | None = None,
        force_scan: bool = False,
    ) -> list[Backend]:
        """Merge local providers + LAN peers into the pool.

        The provider port scan (with its 1-token diagnose probes) is
        TTL-cached — it used to run on every proxied request. Peer rows
        are always re-merged so heartbeat updates apply immediately.
        """

        def _cached_scan() -> list[Backend] | None:
            if force_scan or persist_provider_urls:
                return None
            if self._local_scan_cache is None:
                return None
            if time.monotonic() - self._local_scan_at >= self._local_scan_ttl_s:
                return None
            return self._local_scan_cache

        local = _cached_scan()
        if local is None:
            async with self._local_scan_lock:
                # Another waiter may have refreshed while we queued.
                local = _cached_scan()
                if local is None:
                    local = await self._scan_local_backends(
                        persist_provider_urls=persist_provider_urls,
                        config_path=config_path,
                    )
                    self._local_scan_cache = local
                    self._local_scan_at = time.monotonic()
        remote = (
            self.swarm.peer_agent_backends() if self.config.routing.allow_remote else []
        )
        self.pool.merge_backends(local + remote)
        # The registry is authoritative for peers: rows for peers it no
        # longer tracks must not linger in the pool.
        self.pool.prune_peer_rows({b.base_url for b in remote})
        # `local` is authoritative for every non-peer, non-cloud row: it is
        # the current scan *plus* a synthesised row per enabled
        # [[routing.backends]] override. Anything else in the pool came
        # from a config that no longer exists (a removed override, a
        # provider dropped from discovery.providers) and must not stay
        # routable until restart. An enabled-but-unreachable override still
        # has a row in `local`, so a failed probe never prunes it.
        self.pool.prune_local_rows({b.base_url for b in local})
        self._update_health_metrics()
        return local

    async def _scan_local_backends(
        self,
        *,
        persist_provider_urls: bool,
        config_path: Path | None,
    ) -> list[Backend]:
        from netllm_core.models import save_config
        from netllm_discovery.local import merge_discovered_provider_urls

        results = await scan_local_providers(self.config)
        if persist_provider_urls and config_path is not None:
            before = dict(self.config.discovery.provider_urls)
            merge_discovered_provider_urls(self.config, results)
            if self.config.discovery.provider_urls != before:
                save_config(self.config, config_path)
        local = scan_results_to_backends(
            results,
            agent_id=self.config.agent.agent_id,
            local=True,
            config=self.config,
        )
        for override in self.config.routing.backends:
            if not override.enabled:
                continue
            key = override.resolve_api_key()
            found = False
            for b in local:
                if b.base_url.rstrip("/") == override.base_url.rstrip("/"):
                    b.api_key = key
                    b.api_format = override.resolved_api_format()
                    b.max_concurrency = override.max_concurrency
                    found = True
            if not found:
                local.append(
                    Backend(
                        id=override.base_url,
                        base_url=override.base_url.rstrip("/"),
                        provider=override.provider,
                        api_format=override.resolved_api_format(),
                        api_key=key,
                        enabled=True,
                        local=override.local,
                        agent_id=self.config.agent.agent_id,
                        max_concurrency=override.max_concurrency,
                    )
                )
        return local

    def invalidate_local_scan_cache(self) -> None:
        """Drop the TTL-cached provider scan.

        [Seam S4] ``core.apply_config`` used to assign
        ``self._local_scan_cache = None`` directly — a reach-in from the
        constructor module into this module's private cache
        (dependency-graph.md §1.5). Same effect, named entry point.
        """
        self._local_scan_cache = None

    def _update_health_metrics(self) -> None:
        """Publish the router's current belief. Never probes.

        This runs from refresh_local_backends() — i.e. on every proxied
        request — and again in the finally of every attempt. Calling
        is_healthy() here issued blocking sync HTTP probes on the event loop
        whenever a cache entry was stale, stalling every concurrent request
        (F-03). It also refreshed the cache as a side effect, so
        any_health_stale() was almost always False by the time selection ran
        and _offload_if_probing never fired — the guard existed but had been
        pre-empted. Probing belongs to the offloaded selection path and to
        the explicit refresh routes (status?probe=1/probe_peers=1, doctor).
        """
        for b in self.pool.backends:
            healthy = 1 if self.pool.cached_online(b) else 0
            BACKEND_HEALTH.labels(backend=b.base_url, provider=b.provider).set(healthy)
            BACKEND_IN_FLIGHT.labels(backend=b.base_url).set(b.in_flight)

    def _peer_forward_headers(
        self, backend: Backend, incoming: Mapping[str, str] | None = None
    ) -> dict[str, str] | None:
        """Loop guard: agent-hop forwards must terminate at the peer.

        Without this header a peer running a distributing strategy
        (round_robin, least_load, ...) could bounce the request back,
        ping-ponging it across the mesh. The hop counter is a second
        line of defense should the local-only header ever be dropped.

        The resolved source id also rides along (F-47) so per-source
        telemetry on the serving peer attributes mesh traffic to the real
        caller instead of "default". Attribution-only: the receiving peer
        re-resolves the header itself, and resolve_source never grants a
        secret-gated identity from a bare header — only a virtual key
        carrying the correct secret does.
        """
        if backend.id.startswith("peer:"):
            hdrs = self._normalize_headers(incoming)
            hops = self._incoming_hops(hdrs)
            out = {
                LOCAL_ONLY_HEADER: "1",
                HOPS_HEADER: str(hops + 1),
            }
            resolved = resolve_source(headers=hdrs, sources=self.config.routing.sources)
            if resolved.id != DEFAULT_SOURCE_ID:
                out[SOURCE_HEADER] = resolved.id
            return out
        return None

    def _upstream_api_key(self, backend: Backend) -> str:
        """API key for an upstream call; peer forwards authenticate with
        the cluster token so token-enforcing peers accept mesh traffic."""
        key = backend.resolve_api_key()
        if key:
            return key
        if backend.id.startswith("peer:") and self.config.swarm.cluster_token:
            return self.config.swarm.cluster_token
        return "netllm-local"

    def _openai_upstream(
        self, backend: Backend, headers: Mapping[str, str] | None
    ) -> OpenAIUpstream:
        fwd = self._peer_forward_headers(backend, headers)
        api_key = self._upstream_api_key(backend)
        if backend.id in LEGACY_CLOUD_BACKEND_IDS:
            # Request-scoped row: its key may have come from this caller's
            # headers, so the client must not outlive the request in the
            # shared cache (F-04).
            return OpenAIUpstream(
                backend.base_url,
                api_key=api_key,
                default_headers=fwd,
                connect_timeout=self.config.routing.upstream_connect_timeout_s,
                read_timeout=self.config.routing.upstream_read_timeout_s,
            )
        cache_key = (
            backend.base_url,
            api_key,
            tuple(sorted((fwd or {}).items())),
        )
        client = self._upstream_cache.get(cache_key)
        if client is None:
            if len(self._upstream_cache) > 64:
                self._upstream_cache.clear()
            client = OpenAIUpstream(
                backend.base_url,
                api_key=api_key,
                default_headers=fwd,
                connect_timeout=self.config.routing.upstream_connect_timeout_s,
                read_timeout=self.config.routing.upstream_read_timeout_s,
            )
            self._upstream_cache[cache_key] = client
        return client

    def _anthropic_upstream_headers(
        self, backend: Backend, headers: Mapping[str, str]
    ) -> dict[str, str]:
        """[D13] Default headers for an anthropic-format upstream call.

        The OpenAI upstream constructor has always attached the peer
        loop-guard headers (``_peer_forward_headers``); the anthropic-format
        arm never did, so a ``peer:`` row that happened to be anthropic
        would be forwarded *without* ``x-netllm-local-only`` and without an
        incremented hop count — the receiving peer would consider itself
        free to re-distribute, and the request could ping-pong across the
        mesh. Nothing constructs such a row today (peers are discovered as
        openai-format agents), which is why this was latent rather than
        broken; attaching them here is defensive, and costs a dict merge.

        The guard headers win the merge on purpose: they are the router's
        own routing control plane, not caller pass-through.
        """
        return {
            **self._anthropic_default_headers(headers),
            **(self._peer_forward_headers(backend, headers) or {}),
        }
