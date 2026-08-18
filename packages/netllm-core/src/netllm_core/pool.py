"""Endpoint pool: routing strategies, health cache, batch sharding."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from netllm_core.health import (
    is_online,
    probe_agent_health_sync,
    probe_anthropic_compat_sync,
    probe_openai_compat_sync,
)
from netllm_core.model_resolution import ModelResolver
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
    # Wall-clock sibling of last_check (UI-3). Never used for freshness
    # arithmetic — a wall clock can jump backwards (NTP, sleep/wake) and
    # that must never widen or collapse the health TTL. Published only.
    last_check_epoch_s: float = 0.0


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
    def resolver(self) -> ModelResolver:
        """The single alias/group/catalog matcher (F-25).

        Built per access: it only holds a reference to the live alias map
        plus a parsed snapshot of the groups, so construction is cheap and
        a config reload can never be observed through a stale cache.
        """
        return ModelResolver(
            model_aliases=self.model_aliases, model_pools=self.model_pools
        )

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
                    b.health.last_check_epoch_s = cached.last_check_epoch_s
                if existing is not None and cached is not None and not cached.online:
                    if existing.health.detail:
                        b.health.detail = existing.health.detail
                    if existing.health.http_status is not None:
                        b.health.http_status = existing.health.http_status
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
                # Config-sourced knobs must land on the live row too, or a
                # hot-applied edit silently waits for a restart while the
                # dashboard reports the save succeeded.
                existing.max_concurrency = b.max_concurrency
                existing.cloud_provider = b.cloud_provider
                continue
            by_url[b.base_url] = b
        self._backends = list(by_url.values())

    def prune_local_rows(self, keep_urls: set[str]) -> None:
        """Drop non-peer, non-cloud rows the current config no longer backs.

        ``keep_urls`` is the caller's authoritative set: the URLs the
        current provider scan returned *plus* the URLs of every enabled
        ``[[routing.backends]]`` override (the agent synthesises a row for
        each, reachable or not, so an override whose probe merely failed is
        never pruned here).

        This used to be gated on ``b.provider in discovery.providers``,
        which inverted the intent: a row could only be pruned while its
        provider was still enabled, so removing a `[[routing.backends]]`
        override whose provider sits outside the discovery roster (or
        removing the provider from ``discovery.providers`` at all) left the
        row routable until restart. Peer rows (``prune_peer_rows``) and
        cloud rows (``prune_cloud_provider_rows``) own their own lifecycle
        and are skipped here — the only other producers of pool rows.

        In-flight requests hold their own Backend reference, so dropping
        the row is safe.
        """
        self._backends = [
            b
            for b in self._backends
            if b.id.startswith("peer:")
            or bool(b.cloud_provider)
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

    def mark_failure(
        self,
        backend: Backend,
        *,
        capacity: bool = False,
        status_code: int | None = None,
    ) -> None:
        if capacity:
            # Backend is healthy but full (busy reload, rate limit,
            # memory guard): steer this request elsewhere via the
            # caller's exclude set, but never trip the backend offline —
            # it can take the very next request.
            self.capacity_rejections[backend.id] = (
                self.capacity_rejections.get(backend.id, 0) + 1
            )
            return
        # Agent-hop failures that reflect client/model mismatch on the
        # peer's local providers must not bench the whole peer row.
        if backend.id.startswith("peer:") and status_code in (400, 404):
            return
        key = backend.cache_key()
        entry = self._health_cache.setdefault(key, _HealthEntry())
        entry.failures += 1
        if entry.failures >= self.max_failures:
            entry.online = False
            # Stamp the trip time so the offline re-probe window
            # (offline_retry_s) counts from now, not the last probe.
            entry.last_check = time.monotonic()
            entry.last_check_epoch_s = time.time()
            backend.health.status = "offline"
            backend.health.last_check_epoch_s = entry.last_check_epoch_s

    def mark_success(self, backend: Backend, latency_ms: float | None = None) -> None:
        key = backend.cache_key()
        entry = self._health_cache.setdefault(key, _HealthEntry())
        entry.failures = 0
        entry.online = True
        entry.last_check = time.monotonic()
        entry.last_check_epoch_s = time.time()
        backend.health.status = "online"
        backend.health.last_check_epoch_s = entry.last_check_epoch_s
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
        if backend.id.startswith("peer:"):
            status = probe_agent_health_sync(backend.base_url)
        elif backend.api_format == "anthropic":
            # Prefer a model this backend is known to serve, so the Messages
            # fallback (only reached when the provider has no /v1/models) does
            # not depend on a hardcoded Anthropic model id being valid there.
            served = backend.health.models
            status = probe_anthropic_compat_sync(
                backend.base_url,
                api_key=probe_key,
                fallback_model=served[0] if served else None,
            )
        else:
            status = probe_openai_compat_sync(backend.base_url, api_key=probe_key)
        online = is_online(status)
        now_epoch = time.time()
        self._health_cache[key] = _HealthEntry(
            last_check=now, online=online, failures=0, last_check_epoch_s=now_epoch
        )
        backend.health.status = status.get("status", "unknown")
        backend.health.http_status = status.get("http_status")
        backend.health.detail = status.get("detail")
        probed_models = status.get("models") or []
        if backend.id.startswith("peer:"):
            # Catalog comes from heartbeats; reachability probe is /health only.
            if probed_models:
                backend.health.models = probed_models
                backend.health.model_count = status.get("model_count", 0)
        elif probed_models or online:
            # A failed probe keeps the last known catalog (heartbeat- or
            # probe-sourced) instead of wiping it to [] and breaking
            # model matching until the next heartbeat.
            backend.health.models = probed_models
            backend.health.model_count = status.get("model_count", 0)
        backend.health.last_check = now
        backend.health.last_check_epoch_s = now_epoch
        return online

    def refresh_peer_health(self, *, force: bool = False) -> None:
        """Re-probe peer-agent rows via GET /health (never /v1/models)."""
        for b in self._backends:
            if not b.enabled or not b.id.startswith("peer:"):
                continue
            if force:
                self.is_healthy(b, force_refresh=True)
                continue
            cached = self._health_cache.get(b.cache_key())
            now = time.monotonic()
            if cached is None or not cached.online:
                self.is_healthy(b)
            elif now - cached.last_check >= self._freshness_s(cached):
                self.is_healthy(b)

    def cached_online(self, backend: Backend) -> bool:
        """This pool's current belief about a backend, without probing.

        For observers (metrics, status display) that want the router's view
        rather than fresh truth. `is_healthy` blocks on a synchronous HTTP
        probe whenever the cache entry is stale, so calling it from an async
        request path stalls the whole event loop — the reason
        `_update_health_metrics` now reads through here for every row, not
        just peers (docs/architecture/07-findings-register.md F-03).

        An unprobed backend counts as online: the same optimistic default
        `is_healthy` starts from, so a fresh row is not reported down before
        anything has actually tried it.
        """
        cached = self._health_cache.get(backend.cache_key())
        if cached is None:
            return True
        return cached.online

    def cached_peer_online(self, backend: Backend) -> bool:
        """Deprecated alias for `cached_online` — kept for one release."""
        return self.cached_online(backend)

    # F-25: model_names_for / _backend_matches_host_ref /
    # pool_models_for_backend / resolve_via_pool / _serves_model all lived
    # here and were matcher A (plus half of matcher B). They are gone: the
    # single walk now lives in netllm_core.model_resolution.ModelResolver,
    # reachable as `self.resolver`.

    def known_models(
        self, *, limit: int = 25, capability: str | None = None
    ) -> list[str]:
        """Distinct model IDs across enabled backends (for 404 messages)."""
        return self.resolver.known_models(
            self._backends, limit=limit, capability=capability
        )

    def backends_for_model(
        self,
        model: str,
        *,
        local_only: bool = False,
        exact_model_only: bool = False,
        extra_candidates: list[Backend] | None = None,
    ) -> list[Backend]:
        """Candidate backends for `model`.

        `extra_candidates` are request-scoped rows that must be routable for
        this one request without ever entering the pool — used for cloud
        backends whose credential came from the calling request, so one
        caller's key can never serve another's (F-04). They participate in
        selection exactly like pooled rows; only their lifetime differs.

        When ``exact_model_only`` is True (agent-hop requests whose model was
        already resolved upstream), pool catch-all bypass is disabled so the
        terminating peer routes the forwarded model name literally instead of
        substituting another pool member model.
        """
        resolver = self.resolver
        searchable = (
            [*self._backends, *extra_candidates] if extra_candidates else self._backends
        )

        def collect(*, allow_pool_overflow: bool) -> list[Backend]:
            out: list[Backend] = []
            for b in searchable:
                if not b.enabled:
                    continue
                if not b.local and not self.allow_remote:
                    continue
                if local_only and not b.local:
                    continue
                models = b.health.models
                if not models and self.is_healthy(b):
                    # A stale row may hydrate its catalog on this probe;
                    # decide on the snapshot we end up holding, and hand
                    # that exact snapshot to the resolver.
                    models = b.health.models
                # Request-aware pools: phase 1 is alias-only candidacy;
                # phase 2 (allow_pool_overflow=True) adds group overflow.
                # Agent hops (exact_model_only) never pool-substitute.
                group_overflow = allow_pool_overflow and not exact_model_only
                if resolver.serves(
                    model,
                    b,
                    served=models,
                    allow_group_overflow=group_overflow,
                ):
                    out.append(b)
            return out

        candidates = collect(allow_pool_overflow=False)
        if not candidates:
            candidates = collect(allow_pool_overflow=True)
        if not candidates:
            # Catalogs may be stale (model pulled moments ago) — refresh
            # once and rematch instead of spraying every backend.
            for b in self._backends:
                if b.enabled and b.local:
                    self.is_healthy(b, force_refresh=True)
            candidates = collect(allow_pool_overflow=False)
            if not candidates:
                candidates = collect(allow_pool_overflow=True)
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
        exact_model_only: bool = False,
        prefer_provider: str | None = None,
        prefer_cloud: bool = False,
        exclude_ids: set[str] | None = None,
        cloud_provider_allowlist: frozenset[str] | None = None,
        extra_candidates: list[Backend] | None = None,
    ) -> Backend | None:
        if local_only:
            all_candidates = self.backends_for_model(
                model,
                local_only=True,
                exact_model_only=exact_model_only,
                extra_candidates=extra_candidates,
            )
        else:
            # One mesh-wide candidacy pass: pool overflow (phase 2) runs only
            # when no backend serves the requested name literally (D19). A
            # separate local_only pass used to re-run phase 2 on locals alone
            # and inject wrong pool substitutions (e.g. nemotron/bge answering
            # for gemma) that then won local_spillover over peers serving the
            # requested model.
            mesh_candidates = self.backends_for_model(
                model,
                exact_model_only=exact_model_only,
                extra_candidates=extra_candidates,
            )
            local = [b for b in mesh_candidates if b.local]
            remote = [b for b in mesh_candidates if not b.local]
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

        if cloud_provider_allowlist:
            # A source's cloud_providers allowlist only ever narrows
            # which cloud-tagged rows are reachable -- local/peer
            # (non-cloud) candidates are never affected.
            all_candidates = [
                b
                for b in all_candidates
                if not b.cloud_provider or b.cloud_provider in cloud_provider_allowlist
            ]
            if not all_candidates:
                return None

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
                idx = shard_index(shard_key, pool)
                return pool[idx]
            return pool[0]

        if strategy == "local_spillover":
            return self._select_local_spillover(all_candidates)

        if strategy == "batch_shard" and shard_key:
            idx = shard_index(shard_key, all_candidates)
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


def shard_index(shard_key: str, candidates: list[Backend]) -> int:
    """Map a shard key to an index into `candidates`.

    Numeric keys (plain batch indices, e.g. "0", "17") distribute evenly
    across the current candidate count via `index % N` — the shard count
    is inherently tied to the current worker count for a fixed-size batch,
    so there's no "stable across membership change" property to preserve.

    Non-numeric keys (session ids, connector keys) use rendezvous (HRW)
    hashing over each candidate's stable `.id`: the winner is whichever
    candidate scores highest for this key. Because each candidate's score
    depends only on `(shard_key, candidate.id)` and not on who else is in
    the list, adding or removing a candidate only changes the winner for
    keys that scored highest on the affected candidate — every other key's
    assignment is untouched. Plain `hash(key) % N` doesn't have this
    property: changing N remaps most keys to a different index even when
    the backend at that index had nothing to do with the change.
    """
    n = len(candidates)
    if n <= 1:
        return 0
    if shard_key.lstrip("-").isdigit():
        return int(shard_key) % n
    return max(range(n), key=lambda i: _hrw_score(shard_key, candidates[i].id))


def _hrw_score(shard_key: str, candidate_id: str) -> int:
    digest = hashlib.sha256(f"{shard_key}:{candidate_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
