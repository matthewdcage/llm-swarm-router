"""Endpoint pool: routing strategies, health cache, batch sharding."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

from netllm_core.health import (
    is_online,
    probe_anthropic_compat_sync,
    probe_openai_compat_sync,
)
from netllm_core.models import Backend, RoutingStrategy

logger = logging.getLogger(__name__)

HEALTH_TTL_S = 30.0
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


@dataclass
class BatchDedupLedger:
    completed: set[int] = field(default_factory=set)
    assignments: dict[int, str] = field(default_factory=dict)

    def mark_done(self, index: int) -> None:
        self.completed.add(index)

    def pending_indices(self, total: int) -> list[int]:
        return [i for i in range(total) if i not in self.completed]

    def reassign_failed(
        self,
        failed_index: int,
        backends: list[Backend],
        *,
        current_url: str,
    ) -> str | None:
        urls = [b.base_url for b in healthy_backends(backends)]
        if not urls:
            urls = [b.base_url for b in backends if b.enabled]
        try:
            pos = urls.index(current_url)
        except ValueError:
            pos = -1
        for url in urls[pos + 1 :]:
            if url != current_url:
                self.assignments[failed_index] = url
                return url
        return None


class RouterPool:
    """Manages backends with health cache and routing selection."""

    def __init__(
        self,
        *,
        allow_remote: bool = True,
        spillover_max_local_in_flight: int = 2,
        model_aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self._backends: list[Backend] = []
        self._health_cache: dict[str, _HealthEntry] = {}
        self._round_robin_idx = 0
        self.allow_remote = allow_remote
        self.spillover_max_local_in_flight = max(1, spillover_max_local_in_flight)
        self.model_aliases = model_aliases or {}
        # Our own active forwards per peer agent URL. Peer rows are
        # rebuilt from heartbeats on every refresh, so this ledger keeps
        # in-flight hop counts from being wiped between heartbeats.
        self._own_peer_hops: dict[str, int] = {}

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
            elif existing is not None:
                b.in_flight = existing.in_flight
            if existing is not None:
                b.latency_ema_ms = existing.latency_ema_ms
            by_url[b.base_url] = b
        self._backends = list(by_url.values())

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
        if entry.failures >= MAX_FAILURES:
            entry.online = False
            backend.health.status = "offline"

    def mark_success(self, backend: Backend, latency_ms: float | None = None) -> None:
        key = backend.cache_key()
        entry = self._health_cache.setdefault(key, _HealthEntry())
        entry.failures = 0
        entry.online = True
        entry.last_check = time.monotonic()
        backend.health.status = "online"
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
            if cached is None or now - cached.last_check >= HEALTH_TTL_S:
                return True
        return False

    def is_healthy(self, backend: Backend, *, force_refresh: bool = False) -> bool:
        if not backend.enabled:
            return False
        key = backend.cache_key()
        cached = self._health_cache.get(key)
        now = time.monotonic()
        if (
            not force_refresh
            and cached is not None
            and now - cached.last_check < HEALTH_TTL_S
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
        backend.health.models = status.get("models") or []
        backend.health.model_count = status.get("model_count", 0)
        backend.health.last_check = now
        return online

    def model_names_for(self, model: str) -> list[str]:
        """Requested name plus configured aliases, request name first."""
        return [model, *self.model_aliases.get(model, [])]

    @staticmethod
    def _serves_model(served: list[str], names: list[str]) -> bool:
        return any(m == n or m.startswith(n + ":") for n in names for m in served)

    def known_models(self, *, limit: int = 25) -> list[str]:
        """Distinct model IDs across enabled backends (for 404 messages)."""
        seen: dict[str, None] = {}
        for b in self._backends:
            if not b.enabled:
                continue
            for m in b.health.models:
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
    ) -> Backend | None:
        if local_only:
            all_candidates = self.backends_for_model(model, local_only=True)
        else:
            local = self.backends_for_model(model, local_only=True)
            remote = [b for b in self.backends_for_model(model) if not b.local]
            all_candidates = local + remote
        if not all_candidates:
            return None

        if prefer_provider:
            preferred = [b for b in all_candidates if b.provider == prefer_provider]
            if preferred:
                all_candidates = preferred

        if strategy == "failover":
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
        if len({m for m in models_set if m}) > 1:
            raise ValueError(
                "batch_shard requires the same model on every backend; "
                "found multiple model sets"
            )
        assignments = {i: urls[i % len(urls)] for i in range(num_prompts)}
        return BatchShardPlan(
            assignments=assignments,
            endpoints=list(dict.fromkeys(urls)),
        )


def healthy_backends(
    backends: list[Backend], pool: RouterPool | None = None
) -> list[Backend]:
    p = pool or RouterPool()
    return [b for b in backends if b.enabled and p.is_healthy(b)]


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
