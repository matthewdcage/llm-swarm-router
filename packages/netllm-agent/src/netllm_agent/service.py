"""Agent service — discovery, pool, routing orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from netllm_core.anthropic_bridge import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    translate_openai_stream_to_anthropic,
)
from netllm_core.capabilities import model_capability
from netllm_core.models import (
    ANTHROPIC_CLOUD_BASE_URL,
    BACKEND_PIN_HEADER,
    HOPS_HEADER,
    LOCAL_ONLY_HEADER,
    MAX_FORWARD_HOPS,
    OPENAI_CLOUD_BASE_URL,
    STRATEGY_HEADER,
    Backend,
    NetllmConfig,
)
from netllm_core.pool import RouterPool, is_capacity_error
from netllm_core.routing_policy import ResolvedRouting, resolve_routing
from netllm_core.version import get_version
from netllm_discovery.local import (
    find_omlx_admin_url,
    probe_omlx_admin_for_backends,
    scan_local_providers,
    scan_results_to_backends,
)
from netllm_discovery.swarm import PeerRecord, SwarmRegistry
from netllm_sdk_anthropic.client import AnthropicUpstream, AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstream, OpenAIUpstreamError

from netllm_agent.metrics import (
    BACKEND_HEALTH,
    BACKEND_IN_FLIGHT,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
)
from netllm_agent.shard import (
    BatchRequestLedger,
    ShardContext,
    backend_for_url,
    extract_shard_context,
)

logger = logging.getLogger(__name__)


class AgentService:
    """Core agent state shared by HTTP handlers."""

    def __init__(self, config: NetllmConfig) -> None:
        self.config = config
        self.pool = RouterPool(
            allow_remote=config.routing.allow_remote,
            spillover_max_local_in_flight=(
                config.routing.spillover_max_local_in_flight
            ),
            model_aliases=config.routing.model_aliases,
            health_ttl_s=config.routing.health_ttl_s,
            offline_retry_s=config.routing.offline_retry_s,
            max_failures=config.routing.max_backend_failures,
            max_in_flight_per_backend=(config.routing.max_in_flight_per_backend),
        )
        self.swarm = SwarmRegistry(config)
        self._mdns_advertiser = None
        self._mdns_browser = None
        self._request_count = 0
        self._batch_ledger = BatchRequestLedger()
        # batch_shard requests that arrived without shard context and
        # fell back to round_robin — surfaced in /status so a degenerate
        # strategy choice is visible, not just a log whisper.
        self._shardless_fallbacks = 0
        self.startup_warnings: list[str] = []
        # Hold references so background tasks are not garbage collected.
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._local_scan_cache: list[Backend] | None = None
        self._local_scan_at = 0.0
        self._local_scan_ttl_s = 10.0
        # Dedupe concurrent scans at TTL expiry (cache stampede guard).
        self._local_scan_lock = asyncio.Lock()
        # Reused upstream clients per (base_url, api_key, forward headers):
        # constructing an SDK client (and its httpx pools) per attempt
        # wasted a TCP+TLS setup on every request.
        self._upstream_cache: dict[tuple, OpenAIUpstream] = {}

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
        # The scan is authoritative for discovery providers: a provider
        # removed from config (or vanished from the scan) must not keep
        # a stale routable row around until restart.
        self.pool.prune_local_provider_rows(
            {b.base_url for b in local},
            set(self.config.discovery.providers),
        )
        self._update_health_metrics()
        return local

    def apply_config(self, merged: NetllmConfig) -> None:
        """Hot-apply a config change to the live router (no restart).

        Anything the pool caches from config is re-synced here; the next
        refresh_local_backends() picks up backend/peer list changes.
        """
        self.config = merged
        self.swarm.config = merged
        routing = merged.routing
        self.pool.allow_remote = routing.allow_remote
        self.pool.spillover_max_local_in_flight = max(
            1, routing.spillover_max_local_in_flight
        )
        self.pool.model_aliases = routing.model_aliases
        self.pool.health_ttl_s = routing.health_ttl_s
        self.pool.offline_retry_s = min(routing.offline_retry_s, routing.health_ttl_s)
        self.pool.max_failures = max(1, routing.max_backend_failures)
        self.pool.max_in_flight_per_backend = max(0, routing.max_in_flight_per_backend)
        # Invalidate the provider-scan cache so backend overrides and
        # discovery edits take effect on the next request.
        self._local_scan_cache = None

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
                    )
                )
        return local

    def _update_health_metrics(self) -> None:
        for b in self.pool.backends:
            healthy = 1 if self.pool.is_healthy(b) else 0
            BACKEND_HEALTH.labels(backend=b.base_url, provider=b.provider).set(healthy)
            BACKEND_IN_FLIGHT.labels(backend=b.base_url).set(b.in_flight)

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
            "shardless_fallbacks": self._shardless_fallbacks,
            "cluster_token_set": bool(self.config.swarm.cluster_token),
            "version": get_version(),
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
        self.config.routing.default_strategy = remote  # type: ignore[assignment]

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
            )
        )
        await self.refresh_local_backends()

    async def list_models_aggregated(self) -> dict[str, Any]:
        await self.refresh_local_backends()

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

    def _mark_backend_failure(self, backend: Backend, exc: Exception) -> None:
        """Route failure accounting through capacity classification.

        Capacity rejections (busy model reload, rate limit, memory
        guard) exclude the backend for *this* request only; hard errors
        count toward the offline trip as before.
        """
        self.pool.mark_failure(
            backend,
            capacity=is_capacity_error(getattr(exc, "status_code", None), str(exc)),
        )

    @staticmethod
    def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        if not headers:
            return {}
        return {str(k).lower(): str(v) for k, v in headers.items()}

    @staticmethod
    def _incoming_hops(headers: Mapping[str, str]) -> int:
        raw = headers.get(HOPS_HEADER, "").strip()
        return int(raw) if raw.isdigit() else 0

    @staticmethod
    def _wants_local_only(headers: Mapping[str, str] | None) -> bool:
        hdrs = AgentService._normalize_headers(headers)
        raw = hdrs.get(LOCAL_ONLY_HEADER, "")
        if raw.strip().lower() in ("1", "true", "yes"):
            return True
        # Hop-count backstop: even if a peer strips the local-only
        # header, a request that has already crossed the mesh must not
        # be forwarded again.
        return AgentService._incoming_hops(hdrs) >= MAX_FORWARD_HOPS

    def _model_for_backend(self, model: str, backend: Backend) -> str:
        """Resolve the requested (canonical) model name to the ID this
        backend actually serves, via routing.model_aliases.

        Exact matches win; for tag-prefix matches (Ollama-style
        ``name:tag``) the full served ID is returned, since bare names
        only resolve to the ``latest`` tag upstream. Case-insensitive
        matches fall back to the served ID's exact casing — providers
        like oMLX reject differently-cased names.
        """
        served = backend.health.models
        if not served:
            return model
        names = self.pool.model_names_for(model)
        # Exact matches across the whole alias list win before any
        # prefix match — a backend may serve several tags of one base.
        for name in names:
            if name in served:
                return name
        for name in names:
            for served_id in served:
                if served_id.startswith(name + ":"):
                    return served_id
        folded = [n.casefold() for n in names]
        for name in folded:
            for served_id in served:
                sid = served_id.casefold()
                if sid == name or sid.startswith(name + ":"):
                    return served_id
        return model

    def _model_not_found_error(
        self, model: str, *, capability: str | None = None
    ) -> OpenAIUpstreamError:
        if not self.pool.backends:
            return OpenAIUpstreamError("No healthy backends available for model")
        known = self.pool.known_models(capability=capability) if capability else []
        if not known:
            known = self.pool.known_models()
        listing = ", ".join(known) if known else "none discovered yet"
        return OpenAIUpstreamError(
            f"Model '{model}' not found on any backend. "
            f"Known models: {listing}. "
            "Map provider-specific names with [routing.model_aliases].",
            status_code=404,
        )

    @staticmethod
    def _reject_non_chat_model(model: str) -> None:
        """Refuse chat requests against models that cannot chat.

        Encoders and audio models otherwise fail upstream with confusing
        errors ("tokenizer.chat_template is not set") after burning the
        whole retry budget. Unknown names pass through unchanged.
        """
        cap = model_capability(model)
        if cap == "chat":
            return
        hint = (
            " Use POST /v1/embeddings for embedding models."
            if cap == "embedding"
            else ""
        )
        raise OpenAIUpstreamError(
            f"Model '{model}' (capability: {cap}) cannot serve chat completions.{hint}",
            status_code=400,
        )

    @staticmethod
    def _reject_non_chat_messages_model(model: str) -> None:
        """Messages API variant of the non-chat model guard."""
        cap = model_capability(model)
        if cap == "chat":
            return
        raise AnthropicUpstreamError(
            f"Model '{model}' (capability: {cap}) cannot serve the Messages API.",
            status_code=400,
        )

    @staticmethod
    def _restore_sse_line_model(line: str, model: str) -> str:
        if line.startswith("data: ") and '"model"' in line:
            try:
                body = json.loads(line[len("data: ") :])
                body["model"] = model
                return f"data: {json.dumps(body)}"
            except (ValueError, TypeError):
                return line
        return line

    @staticmethod
    async def _restore_stream_model(
        chunks: AsyncIterator[str], model: str
    ) -> AsyncIterator[str]:
        """Rewrite the model field in SSE chunks back to the canonical name.

        Handles chunks carrying multiple SSE lines; unparseable lines
        pass through untouched.
        """
        async for chunk in chunks:
            if '"model"' not in chunk:
                yield chunk
                continue
            yield "\n".join(
                AgentService._restore_sse_line_model(line, model)
                for line in chunk.split("\n")
            )

    @staticmethod
    def _peer_forward_headers(
        backend: Backend, incoming: Mapping[str, str] | None = None
    ) -> dict[str, str] | None:
        """Loop guard: agent-hop forwards must terminate at the peer.

        Without this header a peer running a distributing strategy
        (round_robin, least_load, ...) could bounce the request back,
        ping-ponging it across the mesh. The hop counter is a second
        line of defense should the local-only header ever be dropped.
        """
        if backend.id.startswith("peer:"):
            hops = AgentService._incoming_hops(
                AgentService._normalize_headers(incoming)
            )
            return {
                LOCAL_ONLY_HEADER: "1",
                HOPS_HEADER: str(hops + 1),
            }
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
            )
            self._upstream_cache[cache_key] = client
        return client

    def _resolved_routing(
        self,
        model: str,
        *,
        api_format: str,
        headers: Mapping[str, str] | None,
    ) -> ResolvedRouting:
        hdrs = self._normalize_headers(headers)
        return resolve_routing(
            self.config.routing,
            model=model,
            api_format=api_format,  # type: ignore[arg-type]
            header_local_only=self._wants_local_only(hdrs),
            header_strategy=hdrs.get(STRATEGY_HEADER),
            header_backend=hdrs.get(BACKEND_PIN_HEADER),
        )

    def _select_backend_for_request(
        self,
        model: str,
        strategy: str,
        attempt: int,
        shard: ShardContext | None,
        *,
        local_only: bool = False,
        prefer_provider: str | None = None,
        exclude_ids: set[str] | None = None,
        pinned: str | None = None,
    ) -> Backend | None:
        if pinned:
            backend = self.pool.backend_by_id(pinned)
            if (
                backend is not None
                and not (local_only and not backend.local)
                and backend.id not in (exclude_ids or set())
            ):
                return backend
            if attempt == 1:
                logger.warning(
                    "pinned backend %r unavailable — falling back to %s",
                    pinned,
                    strategy,
                )
        if strategy == "auto":
            # Shard-context requests keep deterministic placement;
            # everything else balances by live in-flight load.
            strategy = "batch_shard" if shard else "least_load"
        if strategy == "batch_shard":
            if shard and shard.batch_id is not None and shard.index is not None:
                candidates = self.pool.backends_for_model(model)
                if attempt == 1:
                    url = self._batch_ledger.assign(
                        shard.batch_id, shard.index, candidates
                    )
                else:
                    current = self._batch_ledger.assignments.get(
                        (shard.batch_id, shard.index), ""
                    )
                    url = self._batch_ledger.reassign_failed(
                        shard.batch_id,
                        shard.index,
                        candidates,
                        current_url=current,
                    )
                if url:
                    return backend_for_url(url, candidates)
                return None

            shard_key = shard.shard_key if shard else None
            if shard_key is None and shard and shard.index is not None:
                shard_key = str(shard.index)
            if shard_key:
                use_strategy = "batch_shard" if attempt == 1 else "failover"
                return self.pool.select_backend(
                    model,
                    use_strategy,  # type: ignore[arg-type]
                    shard_key=shard_key,
                    attempt=attempt,
                    local_only=local_only,
                    prefer_provider=prefer_provider,
                    exclude_ids=exclude_ids,
                )

            if attempt == 1:
                self._shardless_fallbacks += 1
                # Every request hitting this path means the configured
                # strategy is degenerate for this traffic — say so once,
                # then keep a counter instead of spamming the log.
                count = self._shardless_fallbacks
                if count == 1 or count % 100 == 0:
                    logger.warning(
                        "batch_shard without shard context — falling back to "
                        "round_robin (%s such requests so far; consider "
                        "default_strategy = 'auto' or 'least_load')",
                        count,
                    )
                return self.pool.select_backend(
                    model,
                    "round_robin",
                    local_only=local_only,
                    prefer_provider=prefer_provider,
                    exclude_ids=exclude_ids,
                )
            return self.pool.select_backend(
                model,
                "failover",
                attempt=attempt,
                local_only=local_only,
                prefer_provider=prefer_provider,
                exclude_ids=exclude_ids,
            )

        # Load-aware strategies keep balancing on retries — exclude_ids
        # already guarantees progress past the failed backend. Dropping
        # to failover (local-first) on attempt 2 meant one flaky backend
        # funneled every retry to the local machine regardless of load.
        load_aware = {
            "least_load",
            "latency_weighted",
            "round_robin",
            "local_spillover",
        }
        if attempt == 1 or strategy in load_aware:
            use_strategy = strategy
        else:
            use_strategy = "failover"
        shard_key = shard.shard_key if shard else None
        return self.pool.select_backend(
            model,
            use_strategy,  # type: ignore[arg-type]
            shard_key=shard_key,
            attempt=attempt,
            local_only=local_only,
            prefer_provider=prefer_provider,
            exclude_ids=exclude_ids,
        )

    def _mark_shard_success(self, shard: ShardContext | None) -> None:
        if shard and shard.batch_id is not None and shard.index is not None:
            self._batch_ledger.mark_done(shard.batch_id, shard.index)

    async def _offload_if_probing(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Run selection in a worker thread only when a health probe could
        fire; fresh caches stay on the event loop (no thread overhead,
        no pool-exhaustion exposure under load)."""
        if self.pool.any_health_stale():
            return await asyncio.to_thread(fn, *args, **kwargs)
        return fn(*args, **kwargs)

    async def proxy_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        self._reject_non_chat_model(model)
        routing = self._resolved_routing(model, api_format="openai", headers=hdrs)
        shard = extract_shard_context(payload, hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_openai_cloud_backend(self._openai_api_key(hdrs))
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)
        tried: set[str] = set()

        while attempt < max_attempts:
            attempt += 1
            backend = await self._offload_if_probing(
                self._select_backend_for_request,
                model,
                routing.strategy,
                attempt,
                shard,
                local_only=routing.local_only,
                prefer_provider=routing.prefer_provider,
                exclude_ids=tried,
                pinned=routing.pinned_backend,
            )
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            t0 = time.monotonic()
            try:
                client = self._openai_upstream(backend, hdrs)
                upstream_model = self._model_for_backend(model, backend)
                upstream_payload = (
                    {**payload, "model": upstream_model}
                    if upstream_model != model
                    else payload
                )
                result = await client.chat_completion(upstream_payload)
                if upstream_model != model and isinstance(result, dict):
                    result["model"] = model
                latency = time.monotonic() - t0
                self.pool.mark_success(backend, latency * 1000)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="ok"
                ).inc()
                REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
                self._request_count += 1
                self._mark_shard_success(shard)
                return result
            except OpenAIUpstreamError as exc:
                last_error = exc
                tried.add(backend.id)
                self._mark_backend_failure(backend, exc)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="error"
                ).inc()
                logger.warning(
                    "backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
            finally:
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        raise self._model_not_found_error(model)

    async def proxy_chat_completion_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        self._reject_non_chat_model(model)
        routing = self._resolved_routing(model, api_format="openai", headers=hdrs)
        shard = extract_shard_context(payload, hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_openai_cloud_backend(self._openai_api_key(hdrs))
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)
        tried: set[str] = set()
        yielded_any = False

        while attempt < max_attempts:
            attempt += 1
            backend = await self._offload_if_probing(
                self._select_backend_for_request,
                model,
                routing.strategy,
                attempt,
                shard,
                local_only=routing.local_only,
                prefer_provider=routing.prefer_provider,
                exclude_ids=tried,
                pinned=routing.pinned_backend,
            )
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            try:
                client = self._openai_upstream(backend, hdrs)
                upstream_model = self._model_for_backend(model, backend)
                upstream_payload = (
                    {**payload, "model": upstream_model}
                    if upstream_model != model
                    else payload
                )
                stream = self._stream_with_metrics(
                    client, upstream_payload, backend, model, shard
                )
                if upstream_model != model:
                    stream = self._restore_stream_model(stream, model)
                async for chunk in stream:
                    yielded_any = True
                    yield chunk
                return
            except OpenAIUpstreamError as exc:
                last_error = exc
                tried.add(backend.id)
                logger.warning(
                    "stream backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
                if yielded_any:
                    # Content already reached the client; retrying would
                    # replay a second response into the same SSE stream.
                    # (The finally block below still releases the backend.)
                    yield (
                        "data: " + json.dumps({"error": {"message": str(exc)}}) + "\n\n"
                    )
                    yield "data: [DONE]\n\n"
                    return
            finally:
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        raise self._model_not_found_error(model)

    async def proxy_embeddings(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Route OpenAI-compatible POST /v1/embeddings.

        Same selection and failover loop as chat completions — including
        agent-hop spillover to LAN peers (peer agents expose the same
        /v1/embeddings surface). Anthropic-format backends are excluded:
        the Anthropic Messages API has no embeddings endpoint.
        """
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        routing = self._resolved_routing(model, api_format="openai", headers=hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_openai_cloud_backend(self._openai_api_key(hdrs))
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)
        tried: set[str] = {
            b.id for b in self.pool.backends if b.api_format == "anthropic"
        }

        while attempt < max_attempts:
            attempt += 1
            backend = await self._offload_if_probing(
                self._select_backend_for_request,
                model,
                routing.strategy,
                attempt,
                None,
                local_only=routing.local_only,
                prefer_provider=routing.prefer_provider,
                exclude_ids=tried,
                pinned=routing.pinned_backend,
            )
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            t0 = time.monotonic()
            try:
                client = self._openai_upstream(backend, hdrs)
                upstream_model = self._model_for_backend(model, backend)
                upstream_payload = (
                    {**payload, "model": upstream_model}
                    if upstream_model != model
                    else payload
                )
                result = await client.embeddings(upstream_payload)
                if upstream_model != model and isinstance(result, dict):
                    result["model"] = model
                latency = time.monotonic() - t0
                self.pool.mark_success(backend, latency * 1000)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="ok"
                ).inc()
                REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
                self._request_count += 1
                return result
            except OpenAIUpstreamError as exc:
                last_error = exc
                tried.add(backend.id)
                self._mark_backend_failure(backend, exc)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="error"
                ).inc()
                logger.warning(
                    "embeddings backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
            finally:
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        raise self._model_not_found_error(model, capability="embedding")

    @staticmethod
    def _anthropic_api_key(headers: Mapping[str, str]) -> str:
        return headers.get("x-api-key") or os.environ.get("ANTHROPIC_API_KEY", "")

    @staticmethod
    def _openai_api_key(headers: Mapping[str, str]) -> str:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token and token != "netllm-local":
                return token
        env_key = os.environ.get("OPENAI_API_KEY", "")
        if env_key and env_key != "netllm-local":
            return env_key
        return ""

    @staticmethod
    def _anthropic_default_headers(headers: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("anthropic-version", "anthropic-beta"):
            if key in headers:
                out[key] = headers[key]
        return out

    def _inject_anthropic_cloud_backend(self, api_key: str) -> None:
        if not api_key or api_key == "netllm-local":
            return
        if any(b.api_format == "anthropic" for b in self.pool.backends):
            return
        self.pool.merge_backends(
            [
                Backend(
                    id="anthropic-cloud",
                    base_url=ANTHROPIC_CLOUD_BASE_URL,
                    provider="anthropic",
                    api_format="anthropic",
                    api_key=api_key,
                    enabled=True,
                    local=False,
                    agent_id=self.config.agent.agent_id,
                )
            ]
        )

    def _inject_openai_cloud_backend(self, api_key: str) -> None:
        if not api_key:
            return
        cloud_url = OPENAI_CLOUD_BASE_URL.rstrip("/")
        if any(
            b.api_format == "openai" and b.base_url.rstrip("/") == cloud_url
            for b in self.pool.backends
        ):
            return
        self.pool.merge_backends(
            [
                Backend(
                    id="openai-cloud",
                    base_url=cloud_url,
                    provider="openai",
                    api_format="openai",
                    api_key=api_key,
                    enabled=True,
                    local=False,
                    agent_id=self.config.agent.agent_id,
                )
            ]
        )

    def _anthropic_fallback_backends(self, *, local_only: bool) -> list[Backend]:
        """Anthropic-format backends tried after the OpenAI-format mesh.

        Strategy selection runs over local providers and LAN peers; the
        Anthropic cloud (or any anthropic-format backend) stays a final
        fallback so it never shadows the local mesh in a rotation.
        """
        return [
            b
            for b in self.pool.backends
            if b.enabled
            and b.api_format == "anthropic"
            and (b.local or self.pool.allow_remote)
            and (not local_only or b.local)
        ]

    async def _messages_attempt(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        hdrs: Mapping[str, str],
        api_key: str,
        attempt: int,
    ) -> dict[str, Any]:
        """One acquire→call→account cycle for the Messages API."""
        self.pool.acquire(backend)
        BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
        t0 = time.monotonic()
        try:
            result = await self._messages_on_backend(
                backend, payload, model, hdrs, api_key
            )
            latency = time.monotonic() - t0
            self.pool.mark_success(backend, latency * 1000)
            REQUESTS_TOTAL.labels(
                backend=backend.base_url, model=model, status="ok"
            ).inc()
            REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
            self._request_count += 1
            return result
        except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
            self._mark_backend_failure(backend, exc)
            REQUESTS_TOTAL.labels(
                backend=backend.base_url, model=model, status="error"
            ).inc()
            logger.warning(
                "messages backend %s failed (attempt %s): %s",
                backend.base_url,
                attempt,
                exc,
            )
            raise
        finally:
            self.pool.release(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            self._update_health_metrics()

    async def proxy_messages(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        self._reject_non_chat_messages_model(model)
        api_key = self._anthropic_api_key(hdrs)
        routing = self._resolved_routing(model, api_format="anthropic", headers=hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_anthropic_cloud_backend(api_key)

        # Same strategy-driven selection loop as chat completions, so
        # round_robin / least_load / latency_weighted spread Messages
        # traffic across the mesh too. Anthropic-format backends are
        # excluded here and tried afterwards as the fallback tier.
        last_error: Exception | None = None
        tried: set[str] = {
            b.id for b in self.pool.backends if b.api_format == "anthropic"
        }
        attempt = 0
        max_attempts = max(len(self.pool.backends), 1)
        while attempt < max_attempts:
            attempt += 1
            backend = await self._offload_if_probing(
                self._select_backend_for_request,
                model,
                routing.strategy,
                attempt,
                None,
                local_only=routing.local_only,
                prefer_provider=routing.prefer_provider,
                exclude_ids=tried,
                pinned=routing.pinned_backend,
            )
            if backend is None:
                break
            try:
                return await self._messages_attempt(
                    backend, payload, model, hdrs, api_key, attempt
                )
            except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                last_error = exc
                tried.add(backend.id)

        for backend in self._anthropic_fallback_backends(local_only=routing.local_only):
            attempt += 1
            try:
                return await self._messages_attempt(
                    backend, payload, model, hdrs, api_key, attempt
                )
            except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                last_error = exc

        if last_error:
            raise last_error
        if not api_key:
            raise AnthropicUpstreamError(
                "ANTHROPIC_API_KEY required for cloud Messages API",
                status_code=401,
            )
        raise AnthropicUpstreamError("No healthy backends available for model")

    async def proxy_messages_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        self._reject_non_chat_messages_model(model)
        api_key = self._anthropic_api_key(hdrs)
        routing = self._resolved_routing(model, api_format="anthropic", headers=hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_anthropic_cloud_backend(api_key)

        last_error: Exception | None = None
        tried: set[str] = {
            b.id for b in self.pool.backends if b.api_format == "anthropic"
        }
        attempt = 0
        max_attempts = max(len(self.pool.backends), 1)
        yielded_any = False
        # Strategy loop over the OpenAI-format mesh, then anthropic-format
        # fallbacks — mirrors proxy_messages.
        candidates_exhausted = False
        fallback_iter = iter(())
        while True:
            if not candidates_exhausted and attempt < max_attempts:
                attempt += 1
                backend = await self._offload_if_probing(
                    self._select_backend_for_request,
                    model,
                    routing.strategy,
                    attempt,
                    None,
                    local_only=routing.local_only,
                    prefer_provider=routing.prefer_provider,
                    exclude_ids=tried,
                    pinned=routing.pinned_backend,
                )
                if backend is None:
                    candidates_exhausted = True
                    fallback_iter = iter(
                        self._anthropic_fallback_backends(local_only=routing.local_only)
                    )
                    continue
            else:
                if not candidates_exhausted:
                    candidates_exhausted = True
                    fallback_iter = iter(
                        self._anthropic_fallback_backends(local_only=routing.local_only)
                    )
                backend = next(fallback_iter, None)
                if backend is None:
                    break
                attempt += 1
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            try:
                async for chunk in self._messages_stream_on_backend(
                    backend, payload, model, hdrs, api_key
                ):
                    yielded_any = True
                    yield chunk
                return
            except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                last_error = exc
                tried.add(backend.id)
                self._mark_backend_failure(backend, exc)
                logger.warning(
                    "messages stream backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
                if yielded_any:
                    # Partial SSE already sent — do not replay another
                    # response; surface the error and end the stream.
                    yield (
                        "event: error\ndata: "
                        + json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "type": "upstream_error",
                                    "message": str(exc),
                                },
                            }
                        )
                        + "\n\n"
                    )
                    return
            finally:
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        if not api_key:
            raise AnthropicUpstreamError(
                "ANTHROPIC_API_KEY required for cloud Messages API",
                status_code=401,
            )
        raise AnthropicUpstreamError("No healthy backends available for model")

    async def _messages_on_backend(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        headers: Mapping[str, str],
        fallback_api_key: str,
    ) -> dict[str, Any]:
        if backend.api_format == "anthropic":
            key = backend.resolve_api_key() or fallback_api_key
            if not key:
                raise AnthropicUpstreamError(
                    "ANTHROPIC_API_KEY required", status_code=401
                )
            client = AnthropicUpstream(
                key,
                base_url=backend.base_url,
                default_headers=self._anthropic_default_headers(headers),
            )
            return await client.messages_create(payload)
        oai_payload = anthropic_to_openai_request(payload)
        oai_payload["model"] = self._model_for_backend(model, backend)
        client = self._openai_upstream(backend, headers)
        result = await client.chat_completion(oai_payload)
        return openai_to_anthropic_response(result, model=model)

    async def _messages_stream_on_backend(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        headers: Mapping[str, str],
        fallback_api_key: str,
    ) -> AsyncIterator[str]:
        if backend.api_format == "anthropic":
            key = backend.resolve_api_key() or fallback_api_key
            if not key:
                raise AnthropicUpstreamError(
                    "ANTHROPIC_API_KEY required", status_code=401
                )
            client = AnthropicUpstream(
                key,
                base_url=backend.base_url,
                default_headers=self._anthropic_default_headers(headers),
            )
            async for chunk in client.messages_stream(payload):
                yield chunk
            return
        oai_payload = anthropic_to_openai_request(payload)
        oai_payload["model"] = self._model_for_backend(model, backend)
        client = self._openai_upstream(backend, headers)
        async for chunk in translate_openai_stream_to_anthropic(
            client.chat_completion_stream(oai_payload),
            model=model,
        ):
            yield chunk

    async def _stream_with_metrics(
        self,
        client: OpenAIUpstream,
        payload: dict[str, Any],
        backend: Backend,
        model: str,
        shard: ShardContext | None = None,
    ) -> AsyncIterator[str]:
        t0 = time.monotonic()
        try:
            async for chunk in client.chat_completion_stream(payload):
                yield chunk
            latency = time.monotonic() - t0
            self.pool.mark_success(backend, latency * 1000)
            REQUESTS_TOTAL.labels(
                backend=backend.base_url, model=model, status="ok"
            ).inc()
            REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
            self._mark_shard_success(shard)
        except OpenAIUpstreamError as exc:
            self._mark_backend_failure(backend, exc)
            REQUESTS_TOTAL.labels(
                backend=backend.base_url, model=model, status="error"
            ).inc()
            raise

    def start_background(self) -> list[str]:
        warnings: list[str] = []
        loop = asyncio.get_running_loop()

        if self.config.agent.advertise and self.config.swarm.mdns:
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
                    record = await self.swarm.fetch_peer(url)
                    if record:
                        self.swarm.register_peer(record)
                    else:
                        self.swarm.register_peer(
                            PeerRecord(
                                agent_id=agent_id,
                                listen_url=url,
                                role=props.get("role", "peer"),
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
            except Exception as exc:
                warnings.append(
                    f"Swarm mDNS disabled ({exc}). "
                    "A prior netllm serve may still be registered — try "
                    "netllm serve --replace. Static peers in swarm.peers still work."
                )
                logger.warning("mDNS startup failed: %s", exc)
                if self._mdns_advertiser:
                    self._mdns_advertiser.stop()
                    self._mdns_advertiser = None
                if self._mdns_browser:
                    self._mdns_browser.stop()
                    self._mdns_browser = None
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
