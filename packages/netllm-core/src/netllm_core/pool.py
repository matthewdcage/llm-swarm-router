"""Endpoint pool: routing strategies, health cache, batch sharding."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from netllm_core.capabilities import model_capability
from netllm_core.health import (
    is_online,
    probe_anthropic_compat_sync,
    probe_openai_compat_sync,
)
from netllm_core.models import Backend, RoutingStrategy

logger = logging.getLogger(__name__)

# Defaults; per-pool values come from [routing] config.
HEALTH_TTL_S = 30.0
OFFLINE_RETRY_S = 10.0
MAX_FAILURES = 3


@dataclass
class _HealthEntry:
    last_check: float = 0.0
    online: bool = True
    failures: int = 0


@dataclass
class BatchShardPlan:
    assignments: dict[int, str]
    endpoints: list[str]


class RouterPool:
    """Manages backends with health cache and routing selection."""

    def __init__(
        self,
        *,
        allow_remote: bool = True,
        spillover_max_local_in_flight: int = 2,
        model_aliases: dict[str, list[str]] | None = None,
        health_ttl_s: float = HEALTH_TTL_S,
        offline_retry_s: float = OFFLINE_RETRY_S,
        max_failures: int = MAX_FAILURES,
        require_same_model_for_shard: bool = True,
    ) -> None:
        self._backends: list[Backend] = []
        self._health_cache: dict[str, _HealthEntry] = {}
        self._round_robin_idx = 0
        self.allow_remote = allow_remote
        self.spillover_max_local_in_flight = max(1, spillover_max_local_in_flight)
        self.model_aliases = model_aliases or {}
        self.health_ttl_s = health_ttl_s
        self.offline_retry_s = min(offline_retry_s, health_ttl_s)
        self.max_failures = max(1, max_failures)
        self.require_same_model_for_shard = require_same_model_for_shard
        # Our own active forwards per peer agent URL. Peer rows are
        # rebuilt from heartbeats on every refresh, so this ledger keeps
        # in-flight hop counts from being wiped between heartbeats.
        self._own_peer_hops: dict[str, int] = {}
        # Successful requests served per backend id — surfaces "peer is
        # discovered but idle" directly in status/dashboards.
        self.routed_counts: dict[str, int] = {}

    @property
    def backends(self) -> list[Backend]:
        return list(self._backends)

    def set_backends(self, backends: list[Backend]) -> None:
        self._backends = backends

    def merge_backends(self, new_backends: list[Backend]) -> None:
        by_url = {b.base_url: b for b in self._backends}
        for b in new_backends:
            existing = by_url.get(b.base_url)
            if b.id.startswith("peer:"):
                # Peer rows arrive with heartbeat-reported load; add our
                # own in-flight hops so load is visible between heartbeats.
                b.in_flight += self._own_peer_hops.get(b.base_url, 0)
                if existing is not None:
                    b.latency_ema_ms = existing.latency_ema_ms
                by_url[b.base_url] = b
                continue
            if existing is not None and not existing.id.startswith("peer:"):
                # Update in place to keep object identity: requests in
                # flight hold a reference to the existing instance, and
                # release() must decrement the same object that acquire()
                # incremented — replacing the row would leak its count.
                existing.id = b.id
                existing.provider = b.provider
                existing.api_format = b.api_format
                existing.api_key = b.api_key
                existing.enabled = b.enabled
                existing.local = b.local
                existing.agent_id = b.agent_id
                existing.health = b.health
                continue
            by_url[b.base_url] = b
        self._backends = list(by_url.values())

    def prune_peer_rows(self, keep_urls: set[str]) -> None:
        """Drop peer-agent rows no longer present in the swarm registry.

        Without this, a pruned/dead peer's row lingers forever and keeps
        attracting selection attempts; its hop ledger entry would leak.
        """
        removed = [
            b
            for b in self._backends
            if b.id.startswith("peer:") and b.base_url not in keep_urls
        ]
        if not removed:
            return
        gone = {b.base_url for b in removed}
        self._backends = [b for b in self._backends if b.base_url not in gone]
        for url in gone:
            self._own_peer_hops.pop(url, None)

    def backend_by_id(self, ref: str) -> Backend | None:
        """Resolve a pin reference: backend id, peer agent id, or base URL."""
        target = ref.strip()
        if not target:
            return None
        url_target = target.rstrip("/")
        for b in self._backends:
            if not b.enabled:
                continue
            if (
                b.id == target
                or b.id == f"peer:{target}"
                or b.base_url.rstrip("/") == url_target
            ):
                return b
        return None

    def acquire(self, backend: Backend) -> None:
        """Count a request as in flight on this backend."""
        backend.in_flight += 1
        if backend.id.startswith("peer:"):
            hops = self._own_peer_hops
            hops[backend.base_url] = hops.get(backend.base_url, 0) + 1

    def release(self, backend: Backend) -> None:
        """Mark a request complete on this backend."""
        backend.in_flight = max(0, backend.in_flight - 1)
        if backend.id.startswith("peer:"):
            hops = self._own_peer_hops
            hops[backend.base_url] = max(0, hops.get(backend.base_url, 0) - 1)

    def mark_failure(self, backend: Backend) -> None:
        key = backend.cache_key()
        entry = self._health_cache.setdefault(key, _HealthEntry())
        entry.failures += 1
        if entry.failures >= self.max_failures:
            entry.online = False
            # Stamp the trip time so the offline re-probe window
            # (offline_retry_s) counts from now, not the last probe.
            entry.last_check = time.monotonic()
            backend.health.status = "offline"

    def mark_success(self, backend: Backend, latency_ms: float | None = None) -> None:
        key = backend.cache_key()
        entry = self._health_cache.setdefault(key, _HealthEntry())
        entry.failures = 0
        entry.online = True
        entry.last_check = time.monotonic()
        backend.health.status = "online"
        self.routed_counts[backend.id] = self.routed_counts.get(backend.id, 0) + 1
        if latency_ms is not None:
            if backend.latency_ema_ms <= 0:
                backend.latency_ema_ms = float(latency_ms)
            else:
                backend.latency_ema_ms = 0.8 * backend.latency_ema_ms + 0.2 * latency_ms

    def any_health_stale(self) -> bool:
        """True when selecting a backend could trigger a sync HTTP probe.

        Callers use this to decide whether selection needs a worker
        thread (probe possible) or can stay on the event loop (all
        health entries fresh — pure in-memory work).
        """
        now = time.monotonic()
        for b in self._backends:
            if not b.enabled:
                continue
            cached = self._health_cache.get(b.cache_key())
            if cached is None or now - cached.last_check >= self._freshness_s(cached):
                return True
        return False

    def _freshness_s(self, entry: _HealthEntry) -> float:
        """Offline entries re-probe sooner than the healthy TTL so a
        backend tripped by transient failures is not blackholed."""
        return self.health_ttl_s if entry.online else self.offline_retry_s

    def is_healthy(self, backend: Backend, *, force_refresh: bool = False) -> bool:
        if not backend.enabled:
            return False
        key = backend.cache_key()
        cached = self._health_cache.get(key)
        now = time.monotonic()
        if (
            not force_refresh
            and cached is not None
            and now - cached.last_check < self._freshness_s(cached)
        ):
            return cached.online
        probe_key = backend.resolve_api_key() or None
        if backend.api_format == "anthropic":
            status = probe_anthropic_compat_sync(backend.base_url, api_key=probe_key)
        else:
            status = probe_openai_compat_sync(backend.base_url, api_key=probe_key)
        online = is_online(status)
        self._health_cache[key] = _HealthEntry(
            last_check=now, online=online, failures=0
        )
        backend.health.status = status.get("status", "unknown")
        backend.health.http_status = status.get("http_status")
        backend.health.detail = status.get("detail")
        probed_models = status.get("models") or []
        if probed_models or online:
            # A failed probe keeps the last known catalog (heartbeat- or
            # probe-sourced) instead of wiping it to [] and breaking
            # model matching until the next heartbeat.
            backend.health.models = probed_models
            backend.health.model_count = status.get("model_count", 0)
        backend.health.last_check = now
        return online

    def model_names_for(self, model: str) -> list[str]:
        """Requested name plus configured aliases, request name first.

        Alias keys match case-insensitively so clients sending a
        differently-cased model name still resolve.
        """
        aliases = self.model_aliases.get(model)
        if aliases is None:
            folded = model.casefold()
            for key, ids in self.model_aliases.items():
                if key.casefold() == folded:
                    aliases = ids
                    break
        return [model, *(aliases or [])]

    @staticmethod
    def _serves_model(served: list[str], names: list[str]) -> bool:
        folded = [n.casefold() for n in names]
        return any(
            m == n or m.startswith(n + ":")
            for n in folded
            for m in (s.casefold() for s in served)
        )

    def known_models(
        self, *, limit: int = 25, capability: str | None = None
    ) -> list[str]:
        """Distinct model IDs across enabled backends (for 404 messages)."""
        seen: dict[str, None] = {}
        for b in self._backends:
            if not b.enabled:
                continue
            for m in b.health.models:
                if capability is not None and model_capability(m) != capability:
                    continue
                seen.setdefault(m)
        return list(seen)[:limit]

    def backends_for_model(
        self, model: str, *, local_only: bool = False
    ) -> list[Backend]:
        names = self.model_names_for(model)

        def collect() -> list[Backend]:
            out: list[Backend] = []
            for b in self._backends:
                if not b.enabled:
                    continue
                if not b.local and not self.allow_remote:
                    continue
                if local_only and not b.local:
                    continue
                models = b.health.models
                if not models and self.is_healthy(b):
                    models = b.health.models
                if not models:
                    # Unknown catalog (unprobed, auth-gated, cloud inject):
                    # keep as a candidate rather than guessing wrong.
                    out.append(b)
                    continue
                if self._serves_model(models, names):
                    out.append(b)
            return out

        candidates = collect()
        if not candidates:
            # Catalogs may be stale (model pulled moments ago) — refresh
            # once and rematch instead of spraying every backend.
            for b in self._backends:
                if b.enabled and b.local:
                    self.is_healthy(b, force_refresh=True)
            candidates = collect()
        if not candidates:
            return []
        healthy = [b for b in candidates if self.is_healthy(b)]
        return healthy or candidates

    def select_backend(
        self,
        model: str,
        strategy: RoutingStrategy,
        *,
        shard_key: str | None = None,
        attempt: int = 1,
        local_only: bool = False,
        prefer_provider: str | None = None,
        exclude_ids: set[str] | None = None,
    ) -> Backend | None:
        if local_only:
            all_candidates = self.backends_for_model(model, local_only=True)
        else:
            local = self.backends_for_model(model, local_only=True)
            remote = [b for b in self.backends_for_model(model) if not b.local]
            all_candidates = local + remote
        if exclude_ids:
            # Backends that already failed this request: never burn retry
            # attempts re-hitting them — walk on to the next candidate
            # (typically a healthy LAN peer) instead.
            all_candidates = [b for b in all_candidates if b.id not in exclude_ids]
        if not all_candidates:
            return None

        if prefer_provider:
            preferred = [b for b in all_candidates if b.provider == prefer_provider]
            if preferred:
                all_candidates = preferred

        if strategy == "failover":
            if exclude_ids:
                # Failed candidates are already filtered out, so the first
                # remaining backend is the next untried one in preference
                # order (local before remote).
                return all_candidates[0]
            idx = min(max(attempt - 1, 0), len(all_candidates) - 1)
            return all_candidates[idx]

        if strategy == "round_robin":
            b = all_candidates[self._round_robin_idx % len(all_candidates)]
            self._round_robin_idx += 1
            return b

        if strategy == "least_load":
            return min(all_candidates, key=lambda x: x.in_flight)

        if strategy == "latency_weighted":
            with_latency = [b for b in all_candidates if b.latency_ema_ms > 0]
            pool = with_latency or all_candidates
            return min(pool, key=lambda x: x.latency_ema_ms)

        if strategy == "local_first":
            local_pool = [b for b in all_candidates if b.local]
            pool = (
                local_pool if local_pool else [b for b in all_candidates if not b.local]
            )
            if not pool:
                return None
            if shard_key and len(pool) > 1:
                idx = shard_index(shard_key, len(pool))
                return pool[idx]
            return pool[0]

        if strategy == "local_spillover":
            return self._select_local_spillover(all_candidates)

        if strategy == "batch_shard" and shard_key:
            idx = shard_index(shard_key, len(all_candidates))
            return all_candidates[idx]

        return all_candidates[0]

    def _select_local_spillover(self, candidates: list[Backend]) -> Backend | None:
        """Serve locally while under the in-flight threshold; above it,
        spill to a LAN peer only when the peer is genuinely less loaded."""
        local_pool = [b for b in candidates if b.local]
        remote_pool = [b for b in candidates if not b.local]
        if not local_pool:
            if not remote_pool:
                return None
            return min(remote_pool, key=lambda b: b.in_flight)
        best_local = min(local_pool, key=lambda b: b.in_flight)
        if best_local.in_flight < self.spillover_max_local_in_flight:
            return best_local
        if not remote_pool:
            return best_local
        best_remote = min(remote_pool, key=lambda b: b.in_flight)
        if best_remote.in_flight < best_local.in_flight:
            return best_remote
        return best_local

    def plan_batch_shard(
        self,
        model: str,
        num_prompts: int,
        *,
        strategy: RoutingStrategy = "batch_shard",
    ) -> BatchShardPlan:
        backends = self.backends_for_model(model)
        urls = [b.base_url for b in backends if b.enabled]
        if not urls:
            return BatchShardPlan(assignments={}, endpoints=[])
        if strategy != "batch_shard":
            base = urls[0]
            return BatchShardPlan(
                assignments={i: base for i in range(num_prompts)},
                endpoints=[base],
            )
        models_set = {b.health.models[0] if b.health.models else "" for b in backends}
        if self.require_same_model_for_shard and len({m for m in models_set if m}) > 1:
            raise ValueError(
                "batch_shard requires the same model on every backend; "
                "found multiple model sets (set "
                "routing.require_same_model_for_shard = false to override)"
            )
        assignments = {i: urls[i % len(urls)] for i in range(num_prompts)}
        return BatchShardPlan(
            assignments=assignments,
            endpoints=list(dict.fromkeys(urls)),
        )


def shard_index(shard_key: str, num_endpoints: int) -> int:
    """Map shard key to backend index (numeric keys use index % N)."""
    if num_endpoints <= 1:
        return 0
    if shard_key.lstrip("-").isdigit():
        return int(shard_key) % num_endpoints
    return _stable_shard_index(shard_key, num_endpoints)


def _stable_shard_index(shard_key: str, num_endpoints: int) -> int:
    if num_endpoints <= 1:
        return 0
    digest = hashlib.sha256(shard_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % num_endpoints
