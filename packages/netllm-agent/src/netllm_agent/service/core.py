"""Construction and config application — cluster ``core.py`` (plan §1).

Holds ``AgentService.__init__`` and the *only* sanctioned write paths for
``self.config``: :meth:`apply_config` for a whole merged config, and
:meth:`apply_runtime_strategy` for the runtime-only gateway-follow adoption
that used to reach into ``config.routing.default_strategy`` from
``swarm_tasks`` (dependency-graph.md §1.5, recommendation 2).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from netllm_core.models import NetllmConfig
from netllm_core.pool import RouterPool
from netllm_discovery.swarm import SwarmRegistry

from netllm_agent.shard import BatchRequestLedger
from netllm_agent.telemetry import TelemetryService

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netllm_core.models import Backend
    from netllm_sdk_openai.client import OpenAIUpstream

logger = logging.getLogger(__name__)

__all__ = ["AgentServiceCore", "SourceCapacityExceeded"]


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


class AgentServiceCore:
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
        # discovery edits take effect on the next request. [Seam S4]
        # The cache belongs to ``backends.py``; reach it through that
        # module's entry point instead of poking its private attribute.
        self.invalidate_local_scan_cache()

    def apply_runtime_strategy(self, strategy: str) -> None:
        """Adopt a routing strategy for this process only (never persisted).

        [Seam S3] ``swarm_tasks._maybe_follow_gateway`` used to assign
        ``self.config.routing.default_strategy`` directly, which made
        ``self.config`` a two-writer attribute across seven clusters
        (dependency-graph.md §1.5). The decision stays there; the write
        lands here, beside ``apply_config``.
        """
        self.config.routing.default_strategy = strategy  # type: ignore[assignment]
