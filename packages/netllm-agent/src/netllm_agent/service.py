"""Agent service — discovery, pool, routing orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from netllm_core.anthropic_bridge import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    translate_openai_stream_to_anthropic,
)
from netllm_core.capabilities import model_capability
from netllm_core.cloud_providers import get_provider_spec
from netllm_core.models import (
    ANTHROPIC_CLOUD_BASE_URL,
    BACKEND_PIN_HEADER,
    DEFAULT_SOURCE_ID,
    HOPS_HEADER,
    LOCAL_ONLY_HEADER,
    MAX_FORWARD_HOPS,
    OPENAI_CLOUD_BASE_URL,
    SOURCE_HEADER,
    STRATEGY_HEADER,
    Backend,
    BackendHealth,
    NetllmConfig,
    SourceConfig,
)
from netllm_core.openai_responses_bridge import (
    chat_to_responses_response,
    responses_to_chat_request,
    translate_chat_stream_to_responses,
)
from netllm_core.pool import RouterPool, is_capacity_error
from netllm_core.routing_policy import ResolvedRouting, resolve_routing
from netllm_core.scenarios import Scenario, classify_scenario
from netllm_core.source_identity import (
    ResolvedSource,
    is_netllm_placeholder_key,
    resolve_source,
)
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

from netllm_agent.candidates import CandidateSchedule, excluded_api_formats
from netllm_agent.metrics import (
    BACKEND_HEALTH,
    BACKEND_IN_FLIGHT,
    COMPLETION_TOKENS_TOTAL,
    PROMPT_TOKENS_TOTAL,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
    SCENARIO_REQUESTS_TOTAL,
    SOURCE_REQUESTS_TOTAL,
)
from netllm_agent.request_plan import RequestPlan, api_format_for
from netllm_agent.shard import (
    BatchRequestLedger,
    ShardContext,
    backend_for_url,
    extract_shard_context,
)
from netllm_agent.taxonomy import ExhaustionContext, Surface, exhaustion_error
from netllm_agent.telemetry import TelemetryService

logger = logging.getLogger(__name__)

# Backend ids produced only by the legacy env/header cloud key path
# (_legacy_openai_cloud_backend / _legacy_anthropic_cloud_backend). These
# rows are request-scoped: they never enter the pool and their upstream
# clients are never cached, because their credential can come from the
# calling request (docs/architecture/07-findings-register.md F-04).
LEGACY_CLOUD_BACKEND_IDS = frozenset({"openai-cloud", "anthropic-cloud"})


def _token_count(value: Any) -> int:
    """Backend-supplied usage values are untrusted; never raise mid-stream."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class SourceCapacityExceeded(Exception):
    """Raised when a routing.sources entry is at its max_concurrency cap.

    Caught in app.py and translated to a 429 -- a fast, explicit
    rejection instead of silently queuing or oversubscribing the mesh.
    """

    def __init__(self, source_id: str, limit: int) -> None:
        self.source_id = source_id
        self.limit = limit
        super().__init__(
            f"source {source_id!r} is at its configured max_concurrency ({limit})"
        )


class AttemptRecorder:
    """The one place per-attempt success and failure accounting lands.

    Before this existed, each of the five proxy paths (chat ns/s,
    embeddings, messages ns/s) inlined its own success block and its own
    failure block, which is why a fix could land on one loop only
    (behavior-matrix.md D1/D2, F-24). Every path now routes accounting
    through a recorder instead, so the pool ledger, ``REQUESTS_TOTAL``,
    ``REQUEST_LATENCY``, the token counters, ``telemetry.record_usage``,
    ``_request_count`` and shard completion are written by exactly one
    piece of code.

    One instance per proxied request; the instance is threaded into the
    per-attempt helpers (``_stream_with_metrics``, ``_messages_attempt``)
    so an inner wrapper and the outer failover loop share its dedup
    ledger.
    """

    __slots__ = ("_service", "_recorded_failures")

    def __init__(self, service: AgentService) -> None:
        self._service = service
        # Exceptions already accounted for, held by identity. A stream
        # wrapper records the failure and re-raises the same object to
        # the failover loop; without this the loop's own except clause
        # would count it a second time (D2 dedup guard).
        self._recorded_failures: list[Exception] = []

    def success(
        self,
        *,
        backend: Backend,
        model: str,
        latency_s: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        shard: ShardContext | None = None,
    ) -> None:
        """Full success accounting for one completed attempt."""
        service = self._service
        service.pool.mark_success(backend, latency_s * 1000)
        REQUESTS_TOTAL.labels(backend=backend.base_url, model=model, status="ok").inc()
        REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency_s)
        if prompt_tokens or completion_tokens:
            PROMPT_TOKENS_TOTAL.labels(backend=backend.base_url, model=model).inc(
                prompt_tokens
            )
            COMPLETION_TOKENS_TOTAL.labels(backend=backend.base_url, model=model).inc(
                completion_tokens
            )
        service.telemetry.record_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prefill_duration=latency_s * 0.3 if prompt_tokens else 0.0,
            generation_duration=latency_s * 0.7 if completion_tokens else 0.0,
        )
        service._request_count += 1
        service._mark_shard_success(shard)

    def success_from_result(
        self,
        *,
        backend: Backend,
        model: str,
        result: Any,
        latency_s: float,
        shard: ShardContext | None = None,
    ) -> None:
        """Non-streaming twin of :meth:`success` — tokens come from the
        response body's ``usage`` object instead of an SSE chunk."""
        prompt, completion = AgentService._usage_from_response(result)
        self.success(
            backend=backend,
            model=model,
            latency_s=latency_s,
            prompt_tokens=prompt,
            completion_tokens=completion,
            shard=shard,
        )

    def failure(self, *, backend: Backend, model: str, exc: Exception) -> None:
        """Failure accounting for one attempt, at most once per exception.

        This is the *only* caller of ``is_capacity_error`` in the agent:
        capacity rejections (busy model reload, rate limit, memory guard)
        exclude the backend for this request only, while hard errors
        count toward the offline trip (pool.py p:39-55, p:241-262).
        Classifying anywhere else is how the two halves drifted apart.
        """
        if self.already_recorded(exc):
            return
        self._recorded_failures.append(exc)
        status_code = getattr(exc, "status_code", None)
        self._service.pool.mark_failure(
            backend,
            capacity=is_capacity_error(status_code, str(exc)),
            status_code=status_code,
        )
        REQUESTS_TOTAL.labels(
            backend=backend.base_url, model=model, status="error"
        ).inc()

    def already_recorded(self, exc: Exception) -> bool:
        return any(recorded is exc for recorded in self._recorded_failures)


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
            model_pools=config.routing.model_pools,
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
        # Per-source request counters (docs/cli-source-routing-plan.md
        # Phase 1) -- id -> count, surfaced in status_payload() and as
        # SOURCE_REQUESTS_TOTAL. Unregistered/anonymous callers count
        # under DEFAULT_SOURCE_ID, same as before this feature existed.
        self._source_counts: dict[str, int] = {}
        # Per-source concurrency (SourceConfig.max_concurrency, Phase 2)
        # -- id -> requests currently in flight for that source, across
        # all its attempts/retries combined.
        self._source_in_flight: dict[str, int] = {}
        # Per (source, scenario) request counters (Phase 3) -- for
        # tuning scenario rules, surfaced in status_payload().
        self._scenario_counts: dict[tuple[str, str], int] = {}
        self.startup_warnings: list[str] = []
        # Runtime-only (never persisted to config.toml): set via
        # POST /netllm/v1/admin/drain ahead of a planned restart/shutdown.
        # Broadcast in status_payload()/heartbeat so every peer stops
        # selecting this agent for new work; existing in-flight requests
        # finish normally, nothing here cancels them. Resets to False on
        # the next process start.
        self.draining = False
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
        self.telemetry = TelemetryService()

    @staticmethod
    def _usage_from_response(result: Any) -> tuple[int, int]:
        if not isinstance(result, dict):
            return 0, 0
        usage = result.get("usage")
        if not isinstance(usage, dict):
            return 0, 0
        prompt = _token_count(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _token_count(
            usage.get("completion_tokens") or usage.get("output_tokens")
        )
        return prompt, completion

    @staticmethod
    def _usage_from_sse_chunk(chunk: str) -> tuple[int, int]:
        """Token usage carried by one SSE chunk, if any.

        Understands both wire formats a streaming wrapper can see:
        Anthropic (message_start's message.usage / message_delta's usage,
        input_tokens/output_tokens) and OpenAI (the stream_options
        include_usage chunk, prompt_tokens/completion_tokens).
        """
        prompt = completion = 0
        for line in chunk.split("\n"):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            usage = obj.get("usage")
            if not isinstance(usage, dict):
                message = obj.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            prompt = max(
                prompt,
                _token_count(usage.get("input_tokens") or usage.get("prompt_tokens")),
            )
            completion = max(
                completion,
                _token_count(
                    usage.get("output_tokens") or usage.get("completion_tokens")
                ),
            )
        return prompt, completion

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
        self.pool.model_pools = routing.model_pools
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
                max_concurrency=int(payload.get("max_concurrency", 0) or 0),
                draining=bool(payload.get("draining", False)),
            )
        )
        await self.refresh_local_backends()

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

        If nothing matches via aliases and the backend is a
        routing.model_pools member, fall back to whichever pool-allowed
        model it actually serves — the requested name is irrelevant once
        a pool backend was selected for it (see RouterPool.resolve_via_pool).
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
        pool_model = self.pool.resolve_via_pool(backend, model)
        if pool_model is not None:
            return pool_model
        return model

    def _exhausted(
        self,
        surface: Surface,
        requested_model: str,
        last_error: Exception | None,
        *,
        capability: str | None = None,
        api_key: str = "",
    ) -> Exception:
        """Every failover loop's terminal error, via the taxonomy."""
        return exhaustion_error(
            ExhaustionContext(
                surface=surface,
                pool=self.pool,
                requested_model=requested_model,
                capability=capability,
                api_key=api_key,
            ),
            last_error,
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
    def _reject_non_embedding_model(model: str) -> None:
        """[D4] The embeddings surface's capability guard — new in Phase 4c.

        /v1/embeddings had no guard of any kind: a chat model sent here was
        dispatched to every backend in turn, each one 400/500-ing, until the
        retry budget ran out (contract vector
        ``guards-emb-chat-model-burns-retry-budget``). The chat and Messages
        surfaces have rejected the mirror-image mistake since forever.

        **User-visible tightening, release-note it.** ``model_capability``
        classifies by name and returns ``"chat"`` for anything it does not
        recognize, so an embedding model with an unrecognized name (no
        ``embed`` substring and none of the known encoder-family tokens:
        bge, gte, e5, minilm, bert, modernbert, colbert, splade) now gets a
        400 here where it used to route. Callers in that position should
        rename the served model, or map it with a ``[routing.model_aliases]``
        entry whose *request* name carries an embedding token.
        """
        cap = model_capability(model)
        if cap == "embedding":
            return
        hint = (
            " Use POST /v1/chat/completions for chat models." if cap == "chat" else ""
        )
        raise OpenAIUpstreamError(
            f"Model '{model}' (capability: {cap}) cannot serve embeddings.{hint}",
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

    def _resolved_routing(
        self,
        model: str,
        *,
        api_format: str,
        headers: Mapping[str, str] | None,
        source: SourceConfig | None = None,
        scenario: str | None = None,
        surface: Surface | None = None,
    ) -> ResolvedRouting:
        hdrs = self._normalize_headers(headers)
        return resolve_routing(
            self.config.routing,
            model=model,
            api_format=api_format,  # type: ignore[arg-type]
            header_local_only=self._wants_local_only(hdrs),
            header_strategy=hdrs.get(STRATEGY_HEADER),
            header_backend=hdrs.get(BACKEND_PIN_HEADER),
            cloud=self.config.cloud,
            source=source,
            scenario=scenario,
            surface=surface.value if surface is not None else None,
        )

    def _attribute_source(self, headers: Mapping[str, str] | None) -> ResolvedSource:
        """Resolve and count the caller's source for this request.

        Called once per proxy entry point, alongside _resolved_routing.
        Counting here (rather than per-strategy-attempt) means retries
        against a second backend don't inflate a source's request count.
        """
        hdrs = self._normalize_headers(headers)
        resolved = resolve_source(headers=hdrs, sources=self.config.routing.sources)
        self._source_counts[resolved.id] = self._source_counts.get(resolved.id, 0) + 1
        SOURCE_REQUESTS_TOTAL.labels(
            source=resolved.id, resolved_via=resolved.resolved_via
        ).inc()
        return resolved

    def _source_config(self, source_id: str) -> SourceConfig | None:
        for s in self.config.routing.sources:
            if s.id == source_id:
                return s
        return None

    @staticmethod
    def _apply_source_model_rewrite(source: SourceConfig | None, model: str) -> str:
        if source is None or not source.model_rewrites:
            return model
        return source.model_rewrites.get(model, model)

    def _classify_and_record_scenario(
        self,
        payload: Mapping[str, Any],
        *,
        api_format: str,
        surface: Surface,
        source_id: str,
        headers: Mapping[str, str],
    ) -> Scenario:
        """Classify this request's scenario (Phase 3) and count it.

        Called once per proxy entry point, mirroring _attribute_source --
        counting here (not per-attempt) keeps retries from inflating a
        scenario's request count.
        """
        scenario = classify_scenario(
            payload,
            api_format=api_format,
            surface=surface.value,
            user_agent=headers.get("user-agent", ""),
        )
        key = (source_id, scenario)
        self._scenario_counts[key] = self._scenario_counts.get(key, 0) + 1
        SCENARIO_REQUESTS_TOTAL.labels(source=source_id, scenario=scenario).inc()
        return scenario

    @staticmethod
    def _apply_scenario_model(
        source: SourceConfig | None,
        scenario: Scenario,
        model: str,
        *,
        surface: Surface | None = None,
    ) -> str:
        if source is None:
            return model
        rule = source.scenarios.get(scenario)
        # [D14] Same gate resolve_routing applies to the rule's strategy /
        # local_only / allow_cloud fields, so a surface-qualified rule cannot
        # half-fire: either the whole rule applies here or none of it does.
        if rule is not None and not rule.applies_to(
            surface.value if surface is not None else None
        ):
            return model
        if rule is not None and rule.model:
            return rule.model
        return model

    def _source_admit(self, source_id: str, source: SourceConfig | None) -> None:
        """Per-source admission control: check and reserve in one step.

        Raises rather than queuing — a source at its configured
        max_concurrency gets a fast, clear rejection (HTTP 429) instead of
        silently piling onto the mesh.

        Check and increment must not be separated: they used to be, with an
        `await refresh_local_backends()` in between, so N concurrent requests
        for the same source all observed in_flight < cap and all proceeded.
        The cap was advisory under exactly the load it exists to bound
        (docs/architecture/07-findings-register.md F-08). This method runs
        before the first await in every proxy path, and the reservation is
        held for the whole request — including retries — by a single
        _source_release in the caller's finally.
        """
        cap = source.max_concurrency if source is not None else 0
        if cap > 0 and self._source_in_flight.get(source_id, 0) >= cap:
            raise SourceCapacityExceeded(source_id, cap)
        self._source_in_flight[source_id] = self._source_in_flight.get(source_id, 0) + 1

    def _source_release(self, source_id: str) -> None:
        self._source_in_flight[source_id] = max(
            0, self._source_in_flight.get(source_id, 0) - 1
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
        prefer_cloud: bool = False,
        exclude_ids: set[str] | None = None,
        pinned: str | None = None,
        cloud_provider_allowlist: frozenset[str] | None = None,
        extra_candidates: list[Backend] | None = None,
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
                candidates = self.pool.backends_for_model(
                    model, extra_candidates=extra_candidates
                )
                # [D5/D8/D17] The batch-ledger arm is the one selection
                # route that never consulted exclude_ids: it handed the raw
                # candidate list to the ledger. Feeding the shard to EMB and
                # the Messages surfaces makes that reachable with a *dialect*
                # exclusion in the set — and an anthropic row assigned by
                # the ledger for a /v1/embeddings request is precisely what
                # schedule.ineligible_ids exists to prevent.
                #
                # [D17] This is NOT invisible on the chat paths. The filter
                # also removes the backend that just failed, and
                # BatchRequestLedger.reassign_failed walks by *index*
                # (shard.py:75-83: `urls.index(current_url)`, then
                # `urls[pos + 1:]`). Dropping the failed row makes that
                # lookup raise ValueError, so pos = -1 and the walk restarts
                # at the HEAD of the candidate list instead of continuing
                # past the failed position. Chat batch_shard failover order
                # and attempt count therefore change whenever the ledger
                # assigned a non-first backend: with three rows and shard
                # index 2, a failure used to end the request after one
                # attempt (urls[3:] is empty) and now walks rows 0..1.
                # Deliberate — the old forward-only walk gave a shard pinned
                # to the last row zero failover, and post-D5 an unfiltered
                # list could re-dispatch to an ineligible dialect. Pinned by
                # the chain-batch-shard-reassign-restarts-at-head-* vectors.
                if exclude_ids:
                    candidates = [b for b in candidates if b.id not in exclude_ids]
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
                    prefer_cloud=prefer_cloud,
                    exclude_ids=exclude_ids,
                    cloud_provider_allowlist=cloud_provider_allowlist,
                    extra_candidates=extra_candidates,
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
                    prefer_cloud=prefer_cloud,
                    exclude_ids=exclude_ids,
                    cloud_provider_allowlist=cloud_provider_allowlist,
                    extra_candidates=extra_candidates,
                )
            return self.pool.select_backend(
                model,
                "failover",
                attempt=attempt,
                local_only=local_only,
                prefer_provider=prefer_provider,
                prefer_cloud=prefer_cloud,
                exclude_ids=exclude_ids,
                cloud_provider_allowlist=cloud_provider_allowlist,
                extra_candidates=extra_candidates,
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
            prefer_cloud=prefer_cloud,
            exclude_ids=exclude_ids,
            cloud_provider_allowlist=cloud_provider_allowlist,
            extra_candidates=extra_candidates,
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

    def build_request_plan(
        self,
        payload: dict[str, Any],
        headers: Mapping[str, str] | None,
        *,
        surface: Surface,
    ) -> RequestPlan:
        """The one prologue every proxy surface runs (plan §3 Phase 4).

        Ordering is load-bearing and reproduces the five hand-copied
        prologues exactly:

        1. normalize headers once (D12),
        2. attribute the source and count it (once per request, not per
           attempt),
        3. classify and count the scenario,
        4. apply the model rewrite chain (source rewrites, then the
           scenario override),
        5. run the surface's capability guard,
        6. resolve routing,
        7. extract shard context,
        8. admit against the per-source cap — **last**, and before the
           caller's first ``await``, so a request rejected by a guard above
           never reserves a slot. The matching ``_source_release`` belongs
           in the caller's ``finally``.

        Steps 5 and 8 raise; nothing before step 8 has taken a reservation,
        so an exception out of this method needs no cleanup.
        """
        api_format = api_format_for(surface)
        hdrs = self._normalize_headers(headers)
        requested_model = payload.get("model", "")
        resolved_source = self._attribute_source(hdrs)
        source_cfg = self._source_config(resolved_source.id)
        scenario = self._classify_and_record_scenario(
            payload,
            api_format=api_format,
            surface=surface,
            source_id=resolved_source.id,
            headers=hdrs,
        )
        model = self._apply_source_model_rewrite(source_cfg, requested_model)
        model = self._apply_scenario_model(source_cfg, scenario, model, surface=surface)

        api_key = ""
        if surface is Surface.MESSAGES:
            # [D10] The Messages surfaces used to rewrite payload["model"]
            # here, up-front and once, which pinned every later attempt to
            # the first backend's idea of the name. The payload is now
            # immutable on every surface and the upstream name is derived
            # per backend at call time (_messages_on_backend /
            # _messages_stream_on_backend), so a retry onto a backend with a
            # different alias sends *that* backend's served ID.
            self._reject_non_chat_messages_model(model)
            api_key = self._anthropic_api_key(hdrs)
        elif surface is Surface.CHAT:
            self._reject_non_chat_model(model)
        else:
            # [D4] Phase 4c: the guard is now a plan step on EVERY surface.
            # /v1/embeddings used to have none at all.
            self._reject_non_embedding_model(model)

        routing = self._resolved_routing(
            model,
            api_format=api_format,
            headers=hdrs,
            source=source_cfg,
            scenario=scenario,
            surface=surface,
        )
        # [D5] Extracted on every surface, but only the chat paths pass it
        # to selection today; Phase 5 flips the rest.
        shard = extract_shard_context(payload, hdrs)
        self._source_admit(resolved_source.id, source_cfg)
        return RequestPlan(
            surface=surface,
            headers=hdrs,
            source=resolved_source,
            source_config=source_cfg,
            scenario=scenario,
            requested_model=requested_model,
            model=model,
            routing=routing,
            shard=shard,
            payload=payload,
            api_key=api_key,
        )

    def build_candidates(
        self, plan: RequestPlan, cloud_extra: Sequence[Backend]
    ) -> CandidateSchedule:
        """The one candidate schedule every proxy surface runs (Phase 5).

        Call it *after* ``refresh_local_backends()`` and cloud
        materialization: the pool it measures must be the pool the loop
        will select from.

        Three decisions, one place (candidates.py has the long form):

        - **D8** — dialect eligibility becomes ``ineligible_ids``, computed
          once over the pool *and* the request-scoped extras, instead of
          three different pre-seedings of the loop's ``tried`` set. ``tried``
          is now purely "this backend failed this request".
        - **D6** — the two cloud topologies become data: OpenAI surfaces get
          ``extra_candidates`` + ``prefer_cloud``, the Messages surfaces get
          an ordered ``fallback_tiers`` entry. Neither is a code path any
          more.
        - **D7** — ``max_attempts`` is the sum over both phases, so the
          legacy cloud row buys the attempt it can actually use and the
          Messages fallback tier stops running past the cap.
        """
        excluded = excluded_api_formats(plan.surface)
        extras = tuple(cloud_extra)
        selectable = [*self.pool.backends, *extras]
        ineligible = frozenset(b.id for b in selectable if b.api_format in excluded)
        eligible = [b for b in selectable if b.id not in ineligible]

        tiers: tuple[tuple[Backend, ...], ...] = ()
        if plan.surface is Surface.MESSAGES:
            tier = tuple(
                self._anthropic_fallback_backends(
                    local_only=plan.routing.local_only, extra=list(extras)
                )
            )
            if tier:
                tiers = (tier,)

        return CandidateSchedule(
            strategy_attempts=max(len(eligible), 1),
            extra_candidates=extras,
            prefer_cloud=plan.routing.cloud_leads,
            ineligible_ids=ineligible,
            fallback_tiers=tiers,
        )

    async def proxy_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        plan = self.build_request_plan(payload, headers, surface=Surface.CHAT)
        payload, hdrs, model = plan.payload, plan.headers, plan.model
        requested_model, routing, shard = plan.requested_model, plan.routing, plan.shard
        try:
            await self.refresh_local_backends()
            cloud_extra: list[Backend] = []
            if routing.allow_cloud_inject:
                cloud_extra = self._legacy_openai_cloud_backend(
                    self._openai_api_key(hdrs)
                )
                self._materialize_cloud_provider_backends()
            schedule = self.build_candidates(plan, cloud_extra)
            attempt = 0
            recorder = AttemptRecorder(self)
            last_error: Exception | None = None
            tried: set[str] = set()

            while attempt < schedule.strategy_attempts:
                attempt += 1
                backend = await self._offload_if_probing(
                    self._select_backend_for_request,
                    model,
                    routing.strategy,
                    attempt,
                    shard,
                    local_only=routing.local_only,
                    prefer_provider=routing.prefer_provider,
                    prefer_cloud=schedule.prefer_cloud,
                    exclude_ids=schedule.exclusions(tried),
                    pinned=routing.pinned_backend,
                    cloud_provider_allowlist=routing.cloud_provider_allowlist,
                    extra_candidates=list(schedule.extra_candidates),
                )
                if backend is None:
                    break
                self.pool.acquire(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
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
                    if upstream_model != requested_model and isinstance(result, dict):
                        result["model"] = requested_model
                    latency = time.monotonic() - t0
                    recorder.success_from_result(
                        backend=backend,
                        model=model,
                        result=result,
                        latency_s=latency,
                        shard=shard,
                    )
                    return result
                except OpenAIUpstreamError as exc:
                    last_error = exc
                    tried.add(backend.id)
                    recorder.failure(backend=backend, model=model, exc=exc)
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

            raise self._exhausted(Surface.CHAT, requested_model, last_error)
        finally:
            self._source_release(plan.source.id)

    async def proxy_chat_completion_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        plan = self.build_request_plan(payload, headers, surface=Surface.CHAT)
        payload, hdrs, model = plan.payload, plan.headers, plan.model
        requested_model, routing, shard = plan.requested_model, plan.routing, plan.shard
        try:
            await self.refresh_local_backends()
            cloud_extra: list[Backend] = []
            if routing.allow_cloud_inject:
                cloud_extra = self._legacy_openai_cloud_backend(
                    self._openai_api_key(hdrs)
                )
                self._materialize_cloud_provider_backends()
            schedule = self.build_candidates(plan, cloud_extra)
            attempt = 0
            recorder = AttemptRecorder(self)
            last_error: Exception | None = None
            tried: set[str] = set()
            yielded_any = False

            while attempt < schedule.strategy_attempts:
                attempt += 1
                backend = await self._offload_if_probing(
                    self._select_backend_for_request,
                    model,
                    routing.strategy,
                    attempt,
                    shard,
                    local_only=routing.local_only,
                    prefer_provider=routing.prefer_provider,
                    prefer_cloud=schedule.prefer_cloud,
                    exclude_ids=schedule.exclusions(tried),
                    pinned=routing.pinned_backend,
                    cloud_provider_allowlist=routing.cloud_provider_allowlist,
                    extra_candidates=list(schedule.extra_candidates),
                )
                if backend is None:
                    break
                self.pool.acquire(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                try:
                    client = self._openai_upstream(backend, hdrs)
                    upstream_model = self._model_for_backend(model, backend)
                    upstream_payload = (
                        {**payload, "model": upstream_model}
                        if upstream_model != model
                        else payload
                    )
                    stream = self._stream_with_metrics(
                        client,
                        upstream_payload,
                        backend,
                        model,
                        shard,
                        recorder=recorder,
                    )
                    if upstream_model != requested_model:
                        stream = self._restore_stream_model(stream, requested_model)
                    async for chunk in stream:
                        yielded_any = True
                        yield chunk
                    return
                except OpenAIUpstreamError as exc:
                    last_error = exc
                    tried.add(backend.id)
                    # [D2] Failures raised before _stream_with_metrics'
                    # own try block gets to run (upstream-client
                    # construction, per-backend model resolution) used to
                    # be counted nowhere: this loop deferred all failure
                    # accounting to the wrapper. Record here instead, and
                    # let the recorder's dedup guard drop the call when
                    # the wrapper already accounted for this very
                    # exception -- exactly once, either way.
                    recorder.failure(backend=backend, model=model, exc=exc)
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
                            "data: "
                            + json.dumps({"error": {"message": str(exc)}})
                            + "\n\n"
                        )
                        yield "data: [DONE]\n\n"
                        return
                finally:
                    self.pool.release(backend)
                    BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                        backend.in_flight
                    )
                    self._update_health_metrics()

            raise self._exhausted(Surface.CHAT, requested_model, last_error)
        finally:
            self._source_release(plan.source.id)

    async def proxy_responses(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Codex CLI wiring (docs/cli-source-routing-plan.md): Codex
        requires wire_api = "responses" for every custom provider --
        Chat Completions support was fully removed from Codex in Feb
        2026. Translation happens only at this edge; everything else
        (source identity, per-source routing, scenario classification,
        capacity, failover) is the same proxy_chat_completion path every
        other OpenAI-compatible client already uses.
        """
        chat_payload = responses_to_chat_request(payload)
        result = await self.proxy_chat_completion(chat_payload, headers=headers)
        return chat_to_responses_response(result, model=payload.get("model", ""))

    async def proxy_responses_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        chat_payload = responses_to_chat_request(payload)
        stream = self.proxy_chat_completion_stream(chat_payload, headers=headers)
        async for event in translate_chat_stream_to_responses(
            stream, model=payload.get("model", "")
        ):
            yield event

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
        plan = self.build_request_plan(payload, headers, surface=Surface.EMBEDDINGS)
        payload, hdrs, model = plan.payload, plan.headers, plan.model
        requested_model, routing = plan.requested_model, plan.routing
        # [D5] Phase 5: the embeddings surface extracted shard context in
        # the plan but threw it away here, passing None to selection —
        # `batch_shard` and deterministic placement silently degraded to
        # round_robin for every embedding request. It is fed to selection
        # now, exactly as the chat paths always have.
        shard = plan.shard
        try:
            await self.refresh_local_backends()
            cloud_extra: list[Backend] = []
            if routing.allow_cloud_inject:
                cloud_extra = self._legacy_openai_cloud_backend(
                    self._openai_api_key(hdrs)
                )
                self._materialize_cloud_provider_backends()
            schedule = self.build_candidates(plan, cloud_extra)
            attempt = 0
            recorder = AttemptRecorder(self)
            last_error: Exception | None = None
            tried: set[str] = set()

            while attempt < schedule.strategy_attempts:
                attempt += 1
                backend = await self._offload_if_probing(
                    self._select_backend_for_request,
                    model,
                    routing.strategy,
                    attempt,
                    shard,
                    local_only=routing.local_only,
                    prefer_provider=routing.prefer_provider,
                    prefer_cloud=schedule.prefer_cloud,
                    exclude_ids=schedule.exclusions(tried),
                    pinned=routing.pinned_backend,
                    cloud_provider_allowlist=routing.cloud_provider_allowlist,
                    extra_candidates=list(schedule.extra_candidates),
                )
                if backend is None:
                    break
                self.pool.acquire(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
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
                    if upstream_model != requested_model and isinstance(result, dict):
                        result["model"] = requested_model
                    latency = time.monotonic() - t0
                    recorder.success_from_result(
                        backend=backend,
                        model=model,
                        result=result,
                        latency_s=latency,
                        # [D5] Closing the ledger entry is the other half of
                        # honouring the shard: a batch assignment that is
                        # never marked done keeps its backend reserved for a
                        # request that already succeeded.
                        shard=shard,
                    )
                    return result
                except OpenAIUpstreamError as exc:
                    last_error = exc
                    tried.add(backend.id)
                    recorder.failure(backend=backend, model=model, exc=exc)
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

            raise self._exhausted(
                Surface.EMBEDDINGS,
                requested_model,
                last_error,
                capability="embedding",
            )
        finally:
            self._source_release(plan.source.id)

    @staticmethod
    def _anthropic_api_key(headers: Mapping[str, str]) -> str:
        key = headers.get("x-api-key", "")
        if key and not is_netllm_placeholder_key(key):
            return key
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if env_key and not is_netllm_placeholder_key(env_key):
            return env_key
        return ""

    @staticmethod
    def _openai_api_key(headers: Mapping[str, str]) -> str:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token and not is_netllm_placeholder_key(token):
                return token
        env_key = os.environ.get("OPENAI_API_KEY", "")
        if env_key and not is_netllm_placeholder_key(env_key):
            return env_key
        return ""

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

    @staticmethod
    def _anthropic_default_headers(headers: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("anthropic-version", "anthropic-beta"):
            if key in headers:
                out[key] = headers[key]
        return out

    def _legacy_openai_cloud_backend(self, api_key: str) -> list[Backend]:
        """Request-scoped OpenAI cloud row for the legacy env/header key path.

        Deliberately NOT merged into the pool. These rows carry a credential
        that may have come from the calling request's Authorization header,
        and the pooled version deduped on backend *existence* rather than on
        the key — so the first caller to present a real key seeded a routable
        row that then served every later request from any client on the LAN,
        billed to that first caller (F-04). A request-scoped row is visible to
        selection for exactly one request and then discarded.

        Configured providers ([cloud.providers.*]) are unaffected: their keys
        are server-owned config, so they stay pooled via
        _materialize_cloud_provider_backends.
        """
        if not self.config.cloud.enabled or not api_key:
            return []
        cloud_url = OPENAI_CLOUD_BASE_URL.rstrip("/")
        if any(
            b.api_format == "openai" and b.base_url.rstrip("/") == cloud_url
            for b in self.pool.backends
        ):
            # A configured provider already covers this endpoint with a
            # server-owned key — prefer it over the caller's.
            return []
        return [
            Backend(
                id="openai-cloud",
                base_url=cloud_url,
                provider="openai",
                api_format="openai",
                api_key=api_key,
                enabled=True,
                local=False,
                agent_id=self.config.agent.agent_id,
                cloud_provider="openai",
            )
        ]

    def _legacy_anthropic_cloud_backend(self, api_key: str) -> list[Backend]:
        """Request-scoped Anthropic cloud row — see
        _legacy_openai_cloud_backend for why this is not pooled."""
        if not self.config.cloud.enabled:
            return []
        if not api_key or is_netllm_placeholder_key(api_key):
            return []
        if any(b.api_format == "anthropic" for b in self.pool.backends):
            return []
        return [
            Backend(
                id="anthropic-cloud",
                base_url=ANTHROPIC_CLOUD_BASE_URL,
                provider="anthropic",
                api_format="anthropic",
                api_key=api_key,
                enabled=True,
                local=False,
                agent_id=self.config.agent.agent_id,
                cloud_provider="anthropic",
            )
        ]

    def _materialize_cloud_provider_backends(self) -> None:
        """Sync [cloud.providers.*] into ephemeral pool rows.

        Additive to the legacy env-key injects above: a provider entry is
        only materialized when both the cloud master switch and the
        provider's own `enabled` flag are on. Disabling either prunes the
        row immediately (no restart needed) via prune_cloud_provider_rows.
        """
        cloud_cfg = self.config.cloud
        if not cloud_cfg.enabled:
            self.pool.prune_cloud_provider_rows(set())
            return
        new_backends: list[Backend] = []
        keep_ids: set[str] = set()
        for provider_id, provider_cfg in cloud_cfg.providers.items():
            if not provider_cfg.enabled:
                continue
            spec = get_provider_spec(provider_id)
            if spec is None:
                continue
            api_format = provider_cfg.api_format or spec.default_api_format
            endpoint = spec.endpoint(provider_cfg.region or None)
            base_url = provider_cfg.base_url or (
                endpoint.anthropic_base_url
                if api_format == "anthropic"
                else endpoint.openai_base_url
            )
            if not base_url:
                # Provider doesn't offer this api_format at this
                # region/profile — skip rather than materialize a dead row.
                continue
            api_key = (
                provider_cfg.api_key
                or (
                    os.environ.get(provider_cfg.api_key_env, "")
                    if provider_cfg.api_key_env
                    else ""
                )
                or os.environ.get(spec.api_key_env, "")
            )
            if not api_key and provider_cfg.auth == "plan_token":
                # Anthropic plan_token mode (`claude setup-token`): the
                # official env var is CLAUDE_CODE_OAUTH_TOKEN, not
                # ANTHROPIC_API_KEY. Unofficial for third-party routers —
                # documented by Anthropic for Claude Code CI only.
                api_key = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            if not api_key and provider_cfg.auth in ("api_key", "plan_token"):
                # Enabled but keyless: keep it out of the routable pool
                # rather than injecting a backend guaranteed to 401. The
                # CLI/admin surfaces (phase 2) will flag this state.
                continue
            backend_id = f"cloud-{provider_id}"
            keep_ids.add(backend_id)
            auth_mode = "bearer" if provider_cfg.auth == "plan_token" else "api_key"
            existing = self.pool.backend_by_id(backend_id)
            if (
                existing is not None
                and existing.base_url == base_url
                and existing.api_format == api_format
                and existing.api_key == api_key
                and existing.auth_mode == auth_mode
            ):
                # Already materialized and unchanged this session — skip
                # the rebuild so accumulated health/probe state (real
                # model catalog for providers with a live /models probe)
                # isn't wiped on every request.
                continue
            models = list(provider_cfg.models) or (
                [] if spec.models_endpoint else list(spec.static_models)
            )
            new_backends.append(
                Backend(
                    id=backend_id,
                    base_url=base_url,
                    provider="custom",
                    api_format=api_format,
                    api_key=api_key,
                    enabled=True,
                    local=False,
                    agent_id=self.config.agent.agent_id,
                    cloud_provider=provider_id,
                    auth_mode=auth_mode,
                    health=BackendHealth(
                        status="unknown",
                        models=models,
                        model_count=len(models),
                    ),
                )
            )
        self.pool.merge_backends(new_backends)
        # The legacy env/header key rows (ids in LEGACY_CLOUD_BACKEND_IDS) are
        # request-scoped now and never enter the pool, so nothing has to be
        # held back from pruning on their behalf: only enabled, configured
        # providers survive here.
        self.pool.prune_cloud_provider_rows(keep_ids)

    async def cloud_provider_models_probe(
        self, provider_id: str
    ) -> dict[str, Any] | None:
        """Full model catalog for one cloud provider, straight from the
        provider's API (GET /netllm/v1/cloud/providers/{id}/models).

        Deliberately ignores the `models` allowlist: the materialized
        backend's health.models IS the allowlist once one is set, so the
        allowlist-editing UI needs this separate probe to show what
        could be enabled. Providers without a live catalog endpoint (or
        an unreachable/keyless probe) fall back to the registry's
        static_models with source "static". Returns None for unknown
        provider ids.
        """
        spec = get_provider_spec(provider_id)
        if spec is None:
            return None
        provider_cfg = self.config.cloud.providers.get(provider_id)
        endpoint = spec.endpoint(provider_cfg.region or None if provider_cfg else None)
        api_key = ""
        if provider_cfg is not None:
            api_key = provider_cfg.api_key or (
                os.environ.get(provider_cfg.api_key_env, "")
                if provider_cfg.api_key_env
                else ""
            )
        api_key = api_key or os.environ.get(spec.api_key_env, "")

        def static_payload(status: str, detail: str | None = None) -> dict[str, Any]:
            return {
                "provider": provider_id,
                "source": "static",
                "status": status,
                "detail": detail,
                "models": list(spec.static_models),
                "configured": list(provider_cfg.models) if provider_cfg else [],
            }

        if not spec.models_endpoint:
            return static_payload("static_catalog")
        if not api_key:
            return static_payload("no_api_key", f"Set {spec.api_key_env} first")

        from netllm_core.health import status_from_exception, status_from_response

        try:
            async with httpx.AsyncClient() as client:
                if endpoint.openai_base_url:
                    resp = await client.get(
                        endpoint.openai_base_url.rstrip("/") + "/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=10.0,
                    )
                elif endpoint.anthropic_base_url:
                    # Anthropic-only providers list models at /v1/models
                    # with x-api-key auth (same {"data": [{"id": …}]}
                    # shape status_from_response parses).
                    resp = await client.get(
                        endpoint.anthropic_base_url.rstrip("/") + "/v1/models",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                        timeout=10.0,
                    )
                else:
                    return static_payload("no_endpoint")
            probe = status_from_response(resp)
        except Exception as exc:  # noqa: BLE001 — probe surface, never raise
            probe = status_from_exception(exc, 10.0)
        models = probe.get("models") or []
        if probe.get("status") != "online" or not models:
            return static_payload(
                str(probe.get("status", "error")), probe.get("detail")
            )
        return {
            "provider": provider_id,
            "source": "live",
            "status": "online",
            "detail": None,
            "models": models,
            "configured": list(provider_cfg.models) if provider_cfg else [],
        }

    def _anthropic_fallback_backends(
        self, *, local_only: bool, extra: list[Backend] | None = None
    ) -> list[Backend]:
        """Anthropic-format backends tried after the OpenAI-format mesh.

        Strategy selection runs over local providers and LAN peers; the
        Anthropic cloud (or any anthropic-format backend) stays a final
        fallback so it never shadows the local mesh in a rotation. `extra`
        carries the request-scoped legacy cloud row, which is not in the pool
        (F-04) but must still be reachable as this final tier.
        """
        return [
            b
            for b in [*self.pool.backends, *(extra or [])]
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
        *,
        requested_model: str | None = None,
        recorder: AttemptRecorder | None = None,
        shard: ShardContext | None = None,
    ) -> dict[str, Any]:
        """One acquire→call→account cycle for the Messages API."""
        recorder = recorder if recorder is not None else AttemptRecorder(self)
        self.pool.acquire(backend)
        BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
        t0 = time.monotonic()
        try:
            result = await self._messages_on_backend(
                backend, payload, model, hdrs, api_key
            )
            # [D10] Compare against what actually came back, not against the
            # canonical name. Once the anthropic arm resolves the served ID
            # per backend, `requested_model != model` is the wrong test: an
            # alias-only request (requested == canonical == "canon", served
            # ID "served-a") would leave the backend's own name in the
            # response body. Chat has always compared the upstream name.
            if (
                requested_model is not None
                and isinstance(result, dict)
                and result.get("model") != requested_model
            ):
                result["model"] = requested_model
            latency = time.monotonic() - t0
            recorder.success_from_result(
                backend=backend,
                model=model,
                result=result,
                latency_s=latency,
                # [D5] Close the batch-ledger entry the shard opened.
                shard=shard,
            )
            return result
        except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
            recorder.failure(backend=backend, model=model, exc=exc)
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
        plan = self.build_request_plan(payload, headers, surface=Surface.MESSAGES)
        payload, hdrs, model = plan.payload, plan.headers, plan.model
        requested_model, routing = plan.requested_model, plan.routing
        api_key = plan.api_key
        # [D5] Phase 5: the Messages surfaces passed None to selection even
        # though the plan carries the shard context — same silent degradation
        # the embeddings surface had.
        shard = plan.shard
        try:
            await self.refresh_local_backends()
            cloud_extra: list[Backend] = []
            if routing.allow_cloud_inject:
                cloud_extra = self._legacy_anthropic_cloud_backend(api_key)
                self._materialize_cloud_provider_backends()

            # Same strategy-driven selection loop as chat completions, so
            # round_robin / least_load / latency_weighted spread Messages
            # traffic across the mesh too. [D6/D8] Which rows that loop may
            # see, and which come back afterwards as the ordered fallback
            # tier, is now the schedule's decision, not this function's.
            schedule = self.build_candidates(plan, cloud_extra)
            recorder = AttemptRecorder(self)
            last_error: Exception | None = None
            tried: set[str] = set()
            attempt = 0
            while attempt < schedule.strategy_attempts:
                attempt += 1
                backend = await self._offload_if_probing(
                    self._select_backend_for_request,
                    model,
                    routing.strategy,
                    attempt,
                    shard,
                    local_only=routing.local_only,
                    prefer_provider=routing.prefer_provider,
                    prefer_cloud=schedule.prefer_cloud,
                    exclude_ids=schedule.exclusions(tried),
                    pinned=routing.pinned_backend,
                    cloud_provider_allowlist=routing.cloud_provider_allowlist,
                    extra_candidates=list(schedule.extra_candidates),
                )
                if backend is None:
                    break
                try:
                    return await self._messages_attempt(
                        backend,
                        payload,
                        model,
                        hdrs,
                        api_key,
                        attempt,
                        requested_model=requested_model,
                        recorder=recorder,
                        shard=shard,
                    )
                except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                    last_error = exc
                    tried.add(backend.id)

            # [D7] The fallback tier used to be an unbounded `for` over the
            # anthropic rows: it ran past max_attempts by construction,
            # because the cap only ever measured the pool. It is now part of
            # the budget it spends.
            for backend in schedule.fallback_backends():
                if attempt >= schedule.max_attempts:
                    break
                attempt += 1
                try:
                    return await self._messages_attempt(
                        backend,
                        payload,
                        model,
                        hdrs,
                        api_key,
                        attempt,
                        requested_model=requested_model,
                        recorder=recorder,
                        shard=shard,
                    )
                except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                    last_error = exc

            raise self._exhausted(
                Surface.MESSAGES, requested_model, last_error, api_key=api_key
            )
        finally:
            self._source_release(plan.source.id)

    async def proxy_messages_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        plan = self.build_request_plan(payload, headers, surface=Surface.MESSAGES)
        payload, hdrs, model = plan.payload, plan.headers, plan.model
        requested_model, routing = plan.requested_model, plan.routing
        api_key = plan.api_key
        # [D5] See proxy_messages — the stream arm dropped the shard too.
        shard = plan.shard
        try:
            await self.refresh_local_backends()
            cloud_extra: list[Backend] = []
            if routing.allow_cloud_inject:
                cloud_extra = self._legacy_anthropic_cloud_backend(api_key)
                self._materialize_cloud_provider_backends()

            schedule = self.build_candidates(plan, cloud_extra)
            recorder = AttemptRecorder(self)
            last_error: Exception | None = None
            tried: set[str] = set()
            attempt = 0
            yielded_any = False
            # Strategy phase over the OpenAI-format mesh, then the ordered
            # anthropic fallback tier — mirrors proxy_messages. [D6] Both
            # tiers come from the schedule now, so the bespoke
            # `candidates_exhausted`/lazy-`fallback_iter` state machine that
            # existed only in this function is gone: the fallback iterator is
            # built once, up front, from data.
            fallback_iter = schedule.fallback_backends()
            strategy_phase = True
            while True:
                if strategy_phase and attempt < schedule.strategy_attempts:
                    attempt += 1
                    backend = await self._offload_if_probing(
                        self._select_backend_for_request,
                        model,
                        routing.strategy,
                        attempt,
                        shard,
                        local_only=routing.local_only,
                        prefer_provider=routing.prefer_provider,
                        prefer_cloud=schedule.prefer_cloud,
                        exclude_ids=schedule.exclusions(tried),
                        pinned=routing.pinned_backend,
                        cloud_provider_allowlist=routing.cloud_provider_allowlist,
                        extra_candidates=list(schedule.extra_candidates),
                    )
                    if backend is None:
                        strategy_phase = False
                        continue
                else:
                    # [D7] Bounded by the same budget the strategy phase
                    # spends from; the old loop could not overrun because the
                    # tier is finite, but nothing said so.
                    strategy_phase = False
                    if attempt >= schedule.max_attempts:
                        break
                    backend = next(fallback_iter, None)
                    if backend is None:
                        break
                    attempt += 1
                self.pool.acquire(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                t0 = time.monotonic()
                prompt_tokens = completion_tokens = 0
                try:
                    async for chunk in self._messages_stream_on_backend(
                        backend, payload, model, hdrs, api_key
                    ):
                        if '"usage"' in chunk:
                            p, c = self._usage_from_sse_chunk(chunk)
                            prompt_tokens = max(prompt_tokens, p)
                            completion_tokens = max(completion_tokens, c)
                        yielded_any = True
                        yield chunk
                    # Same success accounting as _stream_with_metrics —
                    # streamed Messages traffic must not be invisible to
                    # the pool and telemetry (F-33).
                    recorder.success(
                        backend=backend,
                        model=model,
                        latency_s=time.monotonic() - t0,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        # [D5] Close the batch-ledger entry the shard opened.
                        shard=shard,
                    )
                    return
                except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                    last_error = exc
                    tried.add(backend.id)
                    recorder.failure(backend=backend, model=model, exc=exc)
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

            raise self._exhausted(
                Surface.MESSAGES, requested_model, last_error, api_key=api_key
            )
        finally:
            self._source_release(plan.source.id)

    def _anthropic_payload_for_backend(
        self, payload: dict[str, Any], model: str, backend: Backend
    ) -> dict[str, Any]:
        """[D10] Per-backend model resolution for the anthropic-format arm.

        The openai-format arm has always done this (`oai_payload["model"] =
        self._model_for_backend(...)`); the anthropic arm relied on the
        caller having mutated `payload["model"]` up-front, so it could only
        ever send one name for the whole request. Resolving here makes the
        Messages surfaces match chat/embeddings: the payload stays immutable
        and each attempt sends the name *its* backend serves.
        """
        upstream_model = self._model_for_backend(model, backend)
        if upstream_model == payload.get("model"):
            return payload
        return {**payload, "model": upstream_model}

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
                default_headers=self._anthropic_upstream_headers(backend, headers),
                auth_mode=backend.auth_mode,
                connect_timeout=self.config.routing.upstream_connect_timeout_s,
                read_timeout=self.config.routing.upstream_read_timeout_s,
            )
            return await client.messages_create(
                self._anthropic_payload_for_backend(payload, model, backend)
            )
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
                default_headers=self._anthropic_upstream_headers(backend, headers),
                auth_mode=backend.auth_mode,
                connect_timeout=self.config.routing.upstream_connect_timeout_s,
                read_timeout=self.config.routing.upstream_read_timeout_s,
            )
            async for chunk in client.messages_stream(
                self._anthropic_payload_for_backend(payload, model, backend)
            ):
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
        recorder: AttemptRecorder | None = None,
    ) -> AsyncIterator[str]:
        recorder = recorder if recorder is not None else AttemptRecorder(self)
        t0 = time.monotonic()
        prompt_tokens = completion_tokens = 0
        try:
            async for chunk in client.chat_completion_stream(payload):
                if '"usage"' in chunk:
                    # Present only when the caller asked for
                    # stream_options.include_usage; zero otherwise.
                    p, c = self._usage_from_sse_chunk(chunk)
                    prompt_tokens = max(prompt_tokens, p)
                    completion_tokens = max(completion_tokens, c)
                yield chunk
            recorder.success(
                backend=backend,
                model=model,
                latency_s=time.monotonic() - t0,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                shard=shard,
            )
        except OpenAIUpstreamError as exc:
            recorder.failure(backend=backend, model=model, exc=exc)
            raise

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
