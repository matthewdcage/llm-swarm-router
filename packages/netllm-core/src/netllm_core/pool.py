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
from netllm_core.models import Backend, ModelPool, RoutingStrategy

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


# Capacity rejections: the backend is healthy but full right now (busy
# model reload, rate limit, memory guard). These must steer the request
# to another backend without counting toward the offline trip — tripping
# a loaded-but-working backend offline blackholes it for offline_retry_s
# while its work piles onto the survivors.
_CAPACITY_STATUS = {409, 429, 503, 507}
# Peer agents wrap upstream refusals in a 502, so the original status is
# only visible in the message body — match known capacity markers too.
_CAPACITY_MARKERS = (
    "prefill_memory_exceeded",
    "memory pressure",
    "is busy",
    "rate limit",
)


def is_capacity_error(status_code: int | None, message: str | None) -> bool:
    """True when an upstream failure means "full now", not "broken"."""
    if status_code in _CAPACITY_STATUS:
        return True
    msg = (message or "").lower()
    return any(marker in msg for marker in _CAPACITY_MARKERS)


class RouterPool:
    """Manages backends with health cache and routing selection."""

    def __init__(
        self,
        *,
        allow_remote: bool = True,
        spillover_max_local_in_flight: int = 2,
        model_aliases: dict[str, list[str]] | None = None,
        model_pools: dict[str, ModelPool] | None = None,
        health_ttl_s: float = HEALTH_TTL_S,
        offline_retry_s: float = OFFLINE_RETRY_S,
        max_failures: int = MAX_FAILURES,
        max_in_flight_per_backend: int = 0,
    ) -> None:
        self._backends: list[Backend] = []
        self._health_cache: dict[str, _HealthEntry] = {}
        self._round_robin_idx = 0
        self.allow_remote = allow_remote
        self.spillover_max_local_in_flight = max(1, spillover_max_local_in_flight)
        self.model_aliases = model_aliases or {}
        self.model_pools = model_pools or {}
        self.health_ttl_s = health_ttl_s
        self.offline_retry_s = min(offline_retry_s, health_ttl_s)
        self.max_failures = max(1, max_failures)
        # 0 disables the cap. When set, selection prefers backends with
        # fewer than this many requests in flight (all strategies).
        self.max_in_flight_per_backend = max(0, max_in_flight_per_backend)
        # Our own active forwards per peer agent URL. Peer rows are
        # rebuilt from heartbeats on every refresh, so this ledger keeps
        # in-flight hop counts from being wiped between heartbeats.
        self._own_peer_hops: dict[str, int] = {}
        # Successful requests served per backend id — surfaces "peer is
        # discovered but idle" directly in status/dashboards.
        self.routed_counts: dict[str, int] = {}
        # Capacity rejections per backend id (backend full, not broken).
        self.capacity_rejections: dict[str, int] = {}

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
                # The rebuilt row's health defaults to "unknown", but the
                # gating truth lives in _health_cache (which survives the
                # merge). Hydrate the display fields so /status reports
                # what routing actually believes about the peer.
                cached = self._health_cache.get(b.cache_key())
                if cached is not None and cached.last_check > 0:
                    b.health.status = "online" if cached.online else "offline"
                    b.health.last_check = cached.last_check
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

    def prune_local_provider_rows(
        self, keep_urls: set[str], providers: set[str]
    ) -> None:
        """Drop local rows for discovery providers no longer scanned.

        Removing a provider from discovery (or a backend disappearing
        from the scan) must remove its pool row — otherwise a stale row
        (e.g. an auth-gated LM Studio) keeps attracting selection until
        restart. Rows for providers outside the discovery set (cloud
        injects, config overrides) are untouched. In-flight requests
        hold their own Backend reference, so dropping the row is safe.
        """
        self._backends = [
            b
            for b in self._backends
            if b.id.startswith("peer:")
            or not b.local
            or b.provider not in providers
            or b.base_url in keep_urls
        ]

    def prune_cloud_provider_rows(self, keep_ids: set[str]) -> None:
        """Drop materialized [cloud.providers.*] rows no longer configured.

        Disabling a provider (or the cloud master switch) must remove its
        pool row immediately — otherwise a stale keyed row keeps
        attracting selection until restart. Legacy env-triggered injects
        (ids "anthropic-cloud" / "openai-cloud") are tagged with
        cloud_provider too, so they prune the same way when
        cloud.enabled=false.
        """
        self._backends = [
            b for b in self._backends if not b.cloud_provider or b.id in keep_ids
        ]

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

    def mark_failure(self, backend: Backend, *, capacity: bool = False) -> None:
        if capacity:
            # Backend is healthy but full (busy reload, rate limit,
            # memory guard): steer this request elsewhere via the
            # caller's exclude set, but never trip the backend offline —
            # it can take the very next request.
            self.capacity_rejections[backend.id] = (
                self.capacity_rejections.get(backend.id, 0) + 1
            )
            return
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
    def _backend_matches_host_ref(backend: Backend, ref: str) -> bool:
        """Same ref forms as backend_by_id: id, "peer:<agent-id>", bare
        agent_id, or base_url — so a pool's `hosts` list can name a
        machine the same way the x-netllm-backend pin header does."""
        target = ref.strip()
        if not target:
            return False
        return (
            backend.id == target
            or backend.id == f"peer:{target}"
            or (backend.agent_id != "" and backend.agent_id == target)
            or backend.base_url.rstrip("/") == target.rstrip("/")
        )

    def pool_models_for_backend(self, backend: Backend) -> list[str]:
        """Union of allowed models from every enabled pool this backend
        belongs to (routing.model_pools). Empty when the backend is not a
        member of any enabled pool."""
        names: list[str] = []
        for pool in self.model_pools.values():
            if not pool.enabled:
                continue
            if not any(
                self._backend_matches_host_ref(backend, ref) for ref in pool.hosts
            ):
                continue
            for m in pool.models:
                if m not in names:
                    names.append(m)
        return names

    def resolve_via_pool(self, backend: Backend, requested_model: str) -> str | None:
        """Pick the model to actually invoke on a pool-member backend,
        ignoring the requested name entirely (model_aliases already
        failed to match by the time callers reach this).

        Returns the first pool-allowed model this backend actually
        serves, or None if the backend is not a pool member / serves
        none of its pool's allowed models.
        """
        pool_models = self.pool_models_for_backend(backend)
        if not pool_models:
            return None
        served = backend.health.models
        for m in pool_models:
            if m in served:
                return m
        folded = {m.casefold() for m in pool_models}
        for served_id in served:
            if served_id.casefold() in folded:
                return served_id
        return None

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
                    if b.local and b.health.http_status in (401, 403):
                        # Auth-gated local provider (e.g. LM Studio with
                        # API auth): probes "online" (reachable) but every
                        # inference call will 401. As a blind candidate it
                        # shows in_flight=0 and wins every least_load pick,
                        # starving real backends. Doctor flags the missing
                        # key; skip until a valid key unlocks /models.
                        # (Cloud injects stay blind candidates — their key
                        # arrives per request, so a 401 probe means
                        # nothing about the next call.)
                        continue
                    # Unknown catalog (unprobed, cloud inject): keep as
                    # a candidate rather than guessing wrong.
                    out.append(b)
                    continue
                if self._serves_model(models, names):
                    out.append(b)
                    continue
                # model_pools bypass: a pool-member backend is a candidate
                # for ANY requested name, as long as it serves one of its
                # pool's allowed models — independent of model_aliases.
                pool_models = self.pool_models_for_backend(b)
                if pool_models and self._serves_model(models, pool_models):
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
        prefer_cloud: bool = False,
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

        if prefer_cloud:
            # cloud.fallback = "local" (cloud-primary): steer every
            # strategy toward materialized cloud backends first. Once all
            # cloud candidates land in exclude_ids (tried/failed), this
            # narrows to nothing and falls through to the full set —
            # the same empty-preferred-list fallback prefer_provider uses
            # above — so the local mesh becomes the retry fallback tier.
            cloud_candidates = [b for b in all_candidates if b.cloud_provider]
            if cloud_candidates:
                all_candidates = cloud_candidates

        if self.max_in_flight_per_backend > 0 or any(
            b.max_concurrency > 0 for b in all_candidates
        ):
            # Back-pressure guardrail for every strategy: don't stack
            # more work on a saturated backend while an alternative has
            # headroom. When all candidates are at the cap, fall through
            # to normal selection rather than failing the request.
            #
            # Per-backend b.max_concurrency (self-declared by a peer via
            # agent.max_concurrency in its heartbeat, or a manual
            # BackendOverride) wins over the pool-wide
            # max_in_flight_per_backend when set — a machine's own
            # declared ceiling is authoritative for its own row.
            def _under_cap(b: Backend) -> bool:
                cap = b.max_concurrency or self.max_in_flight_per_backend
                return cap <= 0 or b.in_flight < cap

            under_cap = [b for b in all_candidates if _under_cap(b)]
            if under_cap:
                all_candidates = under_cap

        if strategy == "auto":
            # Shard-context requests are mapped to batch_shard by the
            # agent before reaching the pool; everything else balances
            # by live load.
            strategy = "least_load"

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
            # min() breaks ties by returning the first element, and
            # all_candidates is local-then-remote — so every exact tie
            # (very common at small in-flight counts, e.g. both at 0 or
            # both at 1) silently favored local forever, starving peers
            # of anything but strictly-lower-load selections. Rotate
            # fairly among tied candidates instead; unchanged when
            # there's a single clear minimum (the common case).
            lowest = min(b.in_flight for b in all_candidates)
            tied = [b for b in all_candidates if b.in_flight == lowest]
            if len(tied) == 1:
                return tied[0]
            b = tied[self._round_robin_idx % len(tied)]
            self._round_robin_idx += 1
            return b

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
