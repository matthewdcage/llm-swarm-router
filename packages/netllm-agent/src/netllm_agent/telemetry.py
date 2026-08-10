"""Unified telemetry for dashboard and macOS menubar."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from array import array
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from netllm_core.models import default_config_path
from netllm_discovery.local import probe_omlx_telemetry

logger = logging.getLogger(__name__)

_HISTORY_LEN = 60
# All-time counters are flushed at most this often instead of once per
# request; close() flushes any pending write on clean shutdown.
_ALLTIME_SAVE_INTERVAL_S = 10.0
_STATS_FILE = default_config_path().parent / "stats.json"


@dataclass
class _RouterCounters:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_prefill_duration: float = 0.0
    total_generation_duration: float = 0.0
    # Tokens belonging to the requests that actually contributed a measured
    # duration. Throughput must divide these, not the display totals: a
    # non-streaming request adds tokens with no observable prefill time, so
    # dividing every token by the measured seconds would inflate the rate by
    # however much traffic was unmeasurable.
    measured_prompt_tokens: int = 0
    measured_completion_tokens: int = 0
    started_at: float = field(default_factory=time.time)

    def avg_prefill_tps(self) -> float | None:
        """Prompt tokens per second of *measured* prefill time, or ``None``.

        ``None`` means no request has yet contributed a measured prefill
        duration — which is every non-streaming request, since a response that
        arrives in one piece has no observable time-to-first-token. Until
        UI-2 this returned ``prompt_tokens / (0.3 × total_latency)``: a
        hardcoded rescaling of total latency, shipped to the dashboard and the
        macOS menubar as a measurement. A missing number is recoverable; a
        wrong one is not.
        """
        if self.total_prefill_duration <= 0:
            return None
        return self.measured_prompt_tokens / self.total_prefill_duration

    def avg_generation_tps(self) -> float | None:
        """Completion tokens per second of measured generation time, or
        ``None``. Same rule as :meth:`avg_prefill_tps`."""
        if self.total_generation_duration <= 0:
            return None
        return self.measured_completion_tokens / self.total_generation_duration

    def to_dict(self) -> dict[str, Any]:
        prefill = self.avg_prefill_tps()
        generation = self.avg_generation_tps()
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "avg_prefill_tps": None if prefill is None else round(prefill, 2),
            "avg_generation_tps": None if generation is None else round(generation, 2),
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _RouterCounters:
        counter = cls()
        counter.requests = int(data.get("requests") or 0)
        counter.prompt_tokens = int(data.get("prompt_tokens") or 0)
        counter.completion_tokens = int(data.get("completion_tokens") or 0)
        counter.started_at = float(data.get("started_at") or time.time())

        # One-time migration. Before UI-2 the duration accumulators were fed
        # `latency * 0.3` and `latency * 0.7`, so a stats.json written by an
        # older agent holds a fabricated split, not measurements — recognisable
        # in the wild by the ratio sitting on 0.31/0.69. Those two totals are
        # dropped rather than carried, because an average over them is exactly
        # the wrong number this release removed, and there is no way to
        # reconstruct the real durations after the fact.
        #
        # `requests`, `prompt_tokens` and `completion_tokens` are genuine
        # counts and are kept: the history is real, only its derivation into a
        # rate was invented. Throughput restarts from the first measured
        # stream and reads `None` until then.
        if bool(data.get("durations_measured")):
            counter.total_prefill_duration = float(
                data.get("total_prefill_duration") or 0.0
            )
            counter.total_generation_duration = float(
                data.get("total_generation_duration") or 0.0
            )
            counter.measured_prompt_tokens = int(
                data.get("measured_prompt_tokens") or 0
            )
            counter.measured_completion_tokens = int(
                data.get("measured_completion_tokens") or 0
            )
        return counter

    def persist_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_prefill_duration": self.total_prefill_duration,
            "total_generation_duration": self.total_generation_duration,
            "measured_prompt_tokens": self.measured_prompt_tokens,
            "measured_completion_tokens": self.measured_completion_tokens,
            # Marks this file as holding measured durations. Its absence is
            # what identifies a pre-UI-2 file on load; never write False.
            "durations_measured": True,
            "started_at": self.started_at,
        }


class _RingBuffer:
    def __init__(self, maxlen: int = _HISTORY_LEN) -> None:
        self._values: deque[float] = deque(maxlen=maxlen)

    def append(self, value: float) -> None:
        self._values.append(value)

    def as_list(self) -> list[float]:
        return list(self._values)


# --------------------------------------------------------------------------- #
# UI-1 — the windowed, dimensioned request ledger
#
# Every other request counter in the agent (``pool.routed_counts``,
# ``_source_counts``, ``_scenario_counts``) is cumulative since process start
# and carries no start timestamp, which is why nothing on the dashboard could
# honestly say "last 5 minutes" or "share of traffic". This ledger is the
# windowed half; the cumulative dicts stay exactly as they are, because clients
# read them today and they answer a different question.
#
# Hot-path constraints (docs/ui-redesign-feature-spec.md UI-1, "Risk"):
#
# 1. Preallocated integer buckets. Recording is a modulo index, a staleness
#    compare and an ``+=`` into a fixed ``array``; there is no timestamp list,
#    no append, no prune scan, and no container allocated per request.
# 2. No lock. The counters this sits beside are lock-free plain dicts on the
#    event loop; an ``asyncio.Lock`` around per-request accounting would
#    serialise completions. Every mutation below is a single bytecode-level
#    step on one event loop, so concurrent coroutines interleave safely
#    without one.
# 3. Bounded cardinality. ``by_model`` is keyed on the *client's* requested
#    model string, and a LAN peer's ``health.models`` is republished through
#    ``/v1/models``, so the key space is attacker-controlled and unbounded
#    growth here is a memory-exhaustion vector. Each dimension caps at
#    ``LEDGER_MAX_KEYS`` distinct keys and folds everything after that into a
#    single ``__other__`` row — accounted, never silently dropped.
# --------------------------------------------------------------------------- #

#: Server-declared spans, in seconds. Clients read the span keys present in the
#: payload; they never sum buckets themselves and never assume this tuple.
LEDGER_SPANS_S: tuple[int, ...] = (60, 300, 86400)

#: ``(offset, buckets, width_s)`` per span, laid out back to back in one flat
#: array per key. Widths are 1 s / 5 s / 900 s, so 216 slots cover all three
#: spans — 60 s at second resolution for the live sparkline, a day at
#: quarter-hour resolution for the "today" figures.
_LEDGER_PLAN: tuple[tuple[int, int, int], ...] = (
    (0, 60, 1),
    (60, 60, 5),
    (120, 96, 900),
)
_LEDGER_SLOTS = 216

#: Totals (request rate, token rate, live tok/s) only ever need the finest
#: span, so they use a one-span plan rather than paying for all three.
_LIVE_PLAN: tuple[tuple[int, int, int], ...] = ((0, 60, 1),)
_LIVE_SLOTS = 60

#: The live block's window, in seconds. Must be <= the finest span above.
LIVE_WINDOW_S = 10

#: Hard per-dimension key cap (constraint 3 above).
LEDGER_MAX_KEYS = 256
#: Where keys past the cap are accounted. Never a real backend/model/source id.
LEDGER_OVERFLOW_KEY = "__other__"
#: Secondary caps for the one 2-D view the spec allows (``by_source`` breakdowns).
_SOURCE_MODEL_CAP = 16
_SOURCE_SURFACE_CAP = 8
_SOURCE_TOP_MODELS = 5
#: Per-backend latency histograms are ~7 KB each; backend ids are bounded in
#: practice but peer/cloud churn is not, so this is capped too.
_BACKEND_HISTOGRAM_CAP = 64


class _SpanRing:
    """Fixed-width buckets for one key, one flat preallocated array per span.

    ``_ticks[i]`` is the bucket index the slot currently holds. A slot whose
    tick is stale is *the* zeroing mechanism: nothing sweeps, nothing prunes,
    and an idle key decays to zero by arithmetic rather than by a timer. That
    is what makes ``history.router_rps`` decay to zero on an idle router
    instead of holding its last value (spec §1, third fact).
    """

    __slots__ = ("_counts", "_plan", "_ticks")

    def __init__(
        self,
        plan: tuple[tuple[int, int, int], ...] = _LEDGER_PLAN,
        slots: int = _LEDGER_SLOTS,
    ) -> None:
        self._plan = plan
        # array(), not list(): a preallocated C buffer of doubles, so a slot
        # write stores a machine value instead of rebinding a Python object.
        self._counts = array("d", bytes(8 * slots))
        self._ticks = array("q", bytes(8 * slots))

    def add(self, now: float, amount: float = 1.0) -> None:
        """Record ``amount`` at wall-clock ``now``. The hot path."""
        counts = self._counts
        ticks = self._ticks
        for offset, nbuckets, width in self._plan:
            tick = int(now // width)
            idx = offset + tick % nbuckets
            if ticks[idx] != tick:
                ticks[idx] = tick
                counts[idx] = 0.0
            counts[idx] += amount

    def window(self, now: float, index: int = 0) -> float:
        """Total over span ``index``, ignoring buckets that have aged out."""
        offset, nbuckets, width = self._plan[index]
        oldest = int(now // width) - nbuckets + 1
        counts = self._counts
        ticks = self._ticks
        total = 0.0
        for i in range(offset, offset + nbuckets):
            if ticks[i] >= oldest:
                total += counts[i]
        return total

    def recent(self, now: float, seconds: int, index: int = 0) -> float:
        """Total over the last ``seconds`` (must fit inside span ``index``)."""
        offset, nbuckets, width = self._plan[index]
        tick = int(now // width)
        span_buckets = min(nbuckets, max(1, seconds // width))
        oldest = tick - span_buckets + 1
        counts = self._counts
        ticks = self._ticks
        total = 0.0
        for i in range(offset, offset + nbuckets):
            if ticks[i] >= oldest:
                total += counts[i]
        return total

    def series(self, now: float, index: int = 0) -> list[float]:
        """Per-bucket values oldest → newest; a stale slot reads 0.0, not its
        last value. This is what a sparkline needs."""
        offset, nbuckets, width = self._plan[index]
        tick = int(now // width)
        counts = self._counts
        ticks = self._ticks
        out: list[float] = []
        for age in range(nbuckets - 1, -1, -1):
            wanted = tick - age
            idx = offset + wanted % nbuckets
            out.append(counts[idx] if ticks[idx] == wanted else 0.0)
        return out


class _Dimension:
    """One dimension of the ledger — a capped ``{key: _SpanRing}`` map."""

    __slots__ = ("_cap", "keys", "truncated")

    def __init__(self, cap: int = LEDGER_MAX_KEYS) -> None:
        self._cap = cap
        self.keys: dict[str, _SpanRing] = {}
        #: Requests folded into ``__other__`` because the dimension was full.
        #: Non-zero means any top-N list built from this dimension is partial.
        self.truncated = 0

    def ring_for(self, key: str) -> _SpanRing | None:
        if not key:
            # An unattributed request still counts in the totals; it must not
            # mint an empty-string dimension row for the UI to render.
            return None
        ring = self.keys.get(key)
        if ring is not None:
            return ring
        if len(self.keys) >= self._cap:
            self.truncated += 1
            ring = self.keys.get(LEDGER_OVERFLOW_KEY)
            if ring is not None:
                return ring
            key = LEDGER_OVERFLOW_KEY
        ring = _SpanRing()
        self.keys[key] = ring
        return ring

    def payload(self, now: float) -> dict[str, dict[str, int]]:
        return {
            key: {
                str(span): int(ring.window(now, i))
                for i, span in enumerate(LEDGER_SPANS_S)
            }
            for key, ring in self.keys.items()
        }


class _SourceEntry:
    """The one 2-D view the spec allows: per source, a bounded top-N of models
    and a bounded surface tally. Deliberately not ``source × model × surface``."""

    __slots__ = ("last_seen_at", "models", "requests", "surfaces")

    def __init__(self) -> None:
        self.requests = _SpanRing()
        self.surfaces: dict[str, int] = {}
        self.models: dict[str, int] = {}
        self.last_seen_at = 0.0


# Log-spaced latency ladder, in milliseconds. Same shape as the Prometheus
# REQUEST_LATENCY histogram, re-scaled: that one starts at 100 ms, which puts
# every realistic TTFT in the first bucket and makes p50 meaningless.
_LATENCY_EDGES_MS: tuple[float, ...] = (
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    200.0,
    400.0,
    800.0,
    1500.0,
    3000.0,
    6000.0,
    12000.0,
    30000.0,
    60000.0,
    120000.0,
)
_HIST_BUCKETS = len(_LATENCY_EDGES_MS) + 1  # + the open-ended top bucket
_HIST_SLOTS = 60
_HIST_WIDTH_S = 5
#: Rolling window the percentiles describe, in seconds.
LATENCY_WINDOW_S = _HIST_SLOTS * _HIST_WIDTH_S
_HIST_ZERO_ROW = array("d", bytes(8 * _HIST_BUCKETS))


class _LatencyHistogram:
    """Windowed latency histogram: O(1) record, no reservoir, no sort.

    A reservoir would need a per-sample allocation and a sort at read time; a
    fixed bucket ladder crossed with a time ring is a constant-size structure
    whose record path is one ``bisect_right`` (C) and one ``+=``.
    """

    __slots__ = ("_counts", "_ticks")

    def __init__(self) -> None:
        self._counts = array("d", bytes(8 * _HIST_SLOTS * _HIST_BUCKETS))
        self._ticks = array("q", bytes(8 * _HIST_SLOTS))

    def observe(self, now: float, value_ms: float) -> None:
        tick = int(now // _HIST_WIDTH_S)
        slot = tick % _HIST_SLOTS
        base = slot * _HIST_BUCKETS
        if self._ticks[slot] != tick:
            self._ticks[slot] = tick
            # One C-level memcpy from a shared preallocated row.
            self._counts[base : base + _HIST_BUCKETS] = _HIST_ZERO_ROW
        self._counts[base + bisect_right(_LATENCY_EDGES_MS, value_ms)] += 1.0

    def snapshot(self, now: float) -> list[float]:
        oldest = int(now // _HIST_WIDTH_S) - _HIST_SLOTS + 1
        acc = [0.0] * _HIST_BUCKETS
        counts = self._counts
        ticks = self._ticks
        for slot in range(_HIST_SLOTS):
            if ticks[slot] < oldest:
                continue
            base = slot * _HIST_BUCKETS
            for j in range(_HIST_BUCKETS):
                acc[j] += counts[base + j]
        return acc


def _histogram_quantile(buckets: list[float], q: float) -> float | None:
    """Interpolated quantile over the fixed ladder, or ``None`` for no data.

    ``None`` — never ``0.0`` — is the whole point: a dashboard that renders a
    fabricated zero for "nothing measured yet" is the defect this feature
    exists to remove.
    """
    total = 0.0
    for count in buckets:
        total += count
    if total <= 0.0:
        return None
    target = total * q
    cumulative = 0.0
    lower = 0.0
    for i, count in enumerate(buckets):
        upper = _LATENCY_EDGES_MS[i] if i < len(_LATENCY_EDGES_MS) else None
        if count > 0.0 and cumulative + count >= target:
            if upper is None:
                # Open-ended top bucket: report its floor rather than invent
                # an upper bound the data does not support.
                return lower
            return lower + (upper - lower) * ((target - cumulative) / count)
        cumulative += count
        if upper is not None:
            lower = upper
    return lower


def _round_or_none(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


class RequestLedger:
    """Windowed, dimensioned request counters plus latency histograms.

    Written from exactly one place — ``AttemptRecorder.success`` — so every
    dimension counts the same population and "share of traffic" is a real
    ratio rather than two counters that happen to be near each other.
    """

    __slots__ = (
        "_backend_latency",
        "_by_backend",
        "_by_model",
        "_by_policy",
        "_by_source",
        "_gen_seconds",
        "_gen_tokens",
        "_prefill_seconds",
        "_prefill_tokens",
        "_requests",
        "_source_truncated",
        "_started_at",
        "_tokens",
        "_ttft",
    )

    def __init__(self, *, started_at: float | None = None) -> None:
        self._started_at = time.time() if started_at is None else started_at
        self._by_backend = _Dimension()
        self._by_model = _Dimension()
        self._by_policy = _Dimension()
        self._by_source: dict[str, _SourceEntry] = {}
        self._source_truncated = 0
        self._requests = _SpanRing(_LIVE_PLAN, _LIVE_SLOTS)
        self._tokens = _SpanRing(_LIVE_PLAN, _LIVE_SLOTS)
        self._prefill_tokens = _SpanRing(_LIVE_PLAN, _LIVE_SLOTS)
        self._prefill_seconds = _SpanRing(_LIVE_PLAN, _LIVE_SLOTS)
        self._gen_tokens = _SpanRing(_LIVE_PLAN, _LIVE_SLOTS)
        self._gen_seconds = _SpanRing(_LIVE_PLAN, _LIVE_SLOTS)
        self._ttft = _LatencyHistogram()
        self._backend_latency: dict[str, _LatencyHistogram] = {}

    # -- record ------------------------------------------------------------ #

    def record(
        self,
        *,
        backend_id: str = "",
        model: str = "",
        source_id: str = "",
        policy_key: str = "",
        surface: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_s: float = 0.0,
        ttft_s: float | None = None,
        now: float | None = None,
    ) -> None:
        """One completed request. Runs on every completion; keep it flat.

        ``ttft_s`` is ``None`` for anything that did not stream — that request
        contributes to the request and latency counts but not to the TTFT
        population, because folding a non-streaming total latency into a TTFT
        percentile makes the percentile describe nothing.
        """
        now = time.time() if now is None else now
        self._requests.add(now)
        tokens = prompt_tokens + completion_tokens
        if tokens > 0:
            self._tokens.add(now, float(tokens))
        if ttft_s is not None and ttft_s > 0.0:
            self._ttft.observe(now, ttft_s * 1000.0)
            if prompt_tokens > 0:
                self._prefill_tokens.add(now, float(prompt_tokens))
                self._prefill_seconds.add(now, ttft_s)
            generation_s = latency_s - ttft_s
            if generation_s > 0.0 and completion_tokens > 0:
                self._gen_tokens.add(now, float(completion_tokens))
                self._gen_seconds.add(now, generation_s)
        if backend_id:
            ring = self._by_backend.ring_for(backend_id)
            if ring is not None:
                ring.add(now)
            if latency_s > 0.0:
                histogram = self._backend_histogram(backend_id)
                if histogram is not None:
                    histogram.observe(now, latency_s * 1000.0)
        if model:
            ring = self._by_model.ring_for(model)
            if ring is not None:
                ring.add(now)
        if policy_key:
            ring = self._by_policy.ring_for(policy_key)
            if ring is not None:
                ring.add(now)
        if source_id:
            self._record_source(now, source_id, surface, model)

    def _backend_histogram(self, backend_id: str) -> _LatencyHistogram | None:
        histogram = self._backend_latency.get(backend_id)
        if histogram is not None:
            return histogram
        if len(self._backend_latency) >= _BACKEND_HISTOGRAM_CAP:
            # Deliberately not folded into __other__: mixing several backends'
            # latencies into one percentile would produce a number that reads
            # like a measurement of a backend and is not one.
            return None
        histogram = _LatencyHistogram()
        self._backend_latency[backend_id] = histogram
        return histogram

    def _record_source(
        self, now: float, source_id: str, surface: str, model: str
    ) -> None:
        entry = self._by_source.get(source_id)
        if entry is None:
            if len(self._by_source) >= LEDGER_MAX_KEYS:
                self._source_truncated += 1
                source_id = LEDGER_OVERFLOW_KEY
                entry = self._by_source.get(source_id)
            if entry is None:
                entry = _SourceEntry()
                self._by_source[source_id] = entry
        entry.requests.add(now)
        entry.last_seen_at = now
        if surface:
            surfaces = entry.surfaces
            if surface in surfaces or len(surfaces) < _SOURCE_SURFACE_CAP:
                surfaces[surface] = surfaces.get(surface, 0) + 1
        if model:
            models = entry.models
            if model in models or len(models) < _SOURCE_MODEL_CAP:
                models[model] = models.get(model, 0) + 1

    # -- read -------------------------------------------------------------- #

    def windows_payload(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        return {
            "counters_since": round(self._started_at, 3),
            "spans_s": list(LEDGER_SPANS_S),
            "by_backend": self._by_backend.payload(now),
            "by_model": self._by_model.payload(now),
            "by_policy": self._by_policy.payload(now),
            "by_source": {
                key: self._source_payload(entry, now)
                for key, entry in self._by_source.items()
            },
            "truncated": {
                "by_backend": self._by_backend.truncated,
                "by_model": self._by_model.truncated,
                "by_policy": self._by_policy.truncated,
                "by_source": self._source_truncated,
            },
        }

    @staticmethod
    def _source_payload(entry: _SourceEntry, now: float) -> dict[str, Any]:
        top = sorted(entry.models.items(), key=lambda kv: -kv[1])[:_SOURCE_TOP_MODELS]
        return {
            "requests": {
                str(span): int(entry.requests.window(now, i))
                for i, span in enumerate(LEDGER_SPANS_S)
            },
            "surfaces": dict(entry.surfaces),
            "top_models": [{"model": m, "count": c} for m, c in top],
            "last_seen_at": round(entry.last_seen_at, 3),
        }

    def latency_payload(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        buckets = self._ttft.snapshot(now)
        samples = 0.0
        for count in buckets:
            samples += count
        return {
            "ttft_p50_ms": _round_or_none(_histogram_quantile(buckets, 0.5)),
            "ttft_p95_ms": _round_or_none(_histogram_quantile(buckets, 0.95)),
            "ttft_samples": int(samples),
            "window_s": LATENCY_WINDOW_S,
        }

    def backend_latency_payload(
        self, backend_id: str, now: float | None = None
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        histogram = self._backend_latency.get(backend_id)
        buckets = histogram.snapshot(now) if histogram is not None else []
        samples = 0.0
        for count in buckets:
            samples += count
        return {
            "p50_ms": _round_or_none(_histogram_quantile(buckets, 0.5)),
            "p95_ms": _round_or_none(_histogram_quantile(buckets, 0.95)),
            "samples": int(samples),
            "window_s": LATENCY_WINDOW_S,
        }

    def live_payload(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        prefill_tokens = self._prefill_tokens.recent(now, LIVE_WINDOW_S)
        prefill_seconds = self._prefill_seconds.recent(now, LIVE_WINDOW_S)
        gen_tokens = self._gen_tokens.recent(now, LIVE_WINDOW_S)
        gen_seconds = self._gen_seconds.recent(now, LIVE_WINDOW_S)
        requests = self._requests.recent(now, LIVE_WINDOW_S)
        return {
            "prefill_tps": (
                round(prefill_tokens / prefill_seconds, 2)
                if prefill_seconds > 0.0
                else None
            ),
            "generation_tps": (
                round(gen_tokens / gen_seconds, 2) if gen_seconds > 0.0 else None
            ),
            "requests_per_s": round(requests / LIVE_WINDOW_S, 3),
            "window_s": LIVE_WINDOW_S,
        }

    def rps_series(self, now: float | None = None) -> list[float]:
        return self._requests.series(time.time() if now is None else now)

    def tps_series(self, now: float | None = None) -> list[float]:
        return self._tokens.series(time.time() if now is None else now)


class TelemetryService:
    """Router + oMLX telemetry with lazy oMLX admin probing."""

    def __init__(self, *, stats_path: Path | None = None) -> None:
        self._stats_path = stats_path or _STATS_FILE
        self._lock = asyncio.Lock()
        self._subscribers = 0
        self._session = _RouterCounters()
        self._alltime = _RouterCounters()
        self._load_alltime()
        self._last_omlx_probe: dict[str, Any] | None = None
        self._last_omlx_probe_at = 0.0
        self._omlx_probe_interval_s = 1.0
        # history.router_rps used to be a request-triggered ring: it was only
        # appended to from inside record_usage, so an idle router never
        # sampled, the sparkline held its last values instead of decaying, and
        # the 60 entries spanned an unknown wall-clock duration (spec §1). The
        # ledger's second-resolution ring replaces it — same 60 entries, but
        # each one is a real second, and idle seconds read zero. No timer task
        # is needed, so nothing wakes a sleeping laptop's event loop.
        self._ledger = RequestLedger()
        self._history_omlx_pp = _RingBuffer()
        self._history_omlx_tg = _RingBuffer()
        self._http_client: Any | None = None
        self._alltime_dirty = False
        self._last_alltime_save = 0.0

    def _load_alltime(self) -> None:
        if not self._stats_path.is_file():
            return
        try:
            data = json.loads(self._stats_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._alltime = _RouterCounters.from_dict(data)
        except json.JSONDecodeError as exc:
            # A partial file means the process died mid-write (or the file
            # was hand-edited); starting fresh is fine, doing so silently
            # is not (docs/architecture/09-follow-up-audit-2026-07-31.md
            # F-48).
            logger.warning(
                "corrupt stats file %s (%s); all-time counters reset",
                self._stats_path,
                exc,
            )
            return
        except (OSError, TypeError, ValueError):
            return

    def _save_alltime(self) -> None:
        # Write-then-rename: os.replace is atomic on POSIX and Windows, so
        # a crash mid-write can never leave a truncated stats.json (F-48).
        # The tmp file lives in the same directory to stay on the same
        # filesystem.
        tmp_path = self._stats_path.with_name(self._stats_path.name + ".tmp")
        try:
            self._stats_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(self._alltime.persist_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp_path, self._stats_path)
        except OSError:
            return
        self._alltime_dirty = False
        self._last_alltime_save = time.monotonic()

    def _save_alltime_debounced(self) -> None:
        """Persist all-time counters at most every _ALLTIME_SAVE_INTERVAL_S.

        This used to run on every recorded request: a mkdir + json.dumps +
        write_text on the event loop per proxied request
        (docs/architecture/07-findings-register.md F-09). The counters are a
        convenience total, not an audit log — losing at most one interval's
        worth on a hard kill is an acceptable trade for keeping the request
        path off the disk. A clean shutdown flushes via close().
        """
        self._alltime_dirty = True
        if time.monotonic() - self._last_alltime_save < _ALLTIME_SAVE_INTERVAL_S:
            return
        self._save_alltime()

    def subscribe(self) -> None:
        self._subscribers += 1

    def unsubscribe(self) -> None:
        self._subscribers = max(0, self._subscribers - 1)

    @property
    def has_subscribers(self) -> bool:
        return self._subscribers > 0

    async def close(self) -> None:
        # Flush whatever the debounce is still holding so a clean shutdown
        # never loses counters.
        if self._alltime_dirty:
            self._save_alltime()
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def ledger(self) -> RequestLedger:
        return self._ledger

    def record_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        prefill_duration: float = 0.0,
        generation_duration: float = 0.0,
        latency_s: float = 0.0,
        ttft_s: float | None = None,
        backend_id: str = "",
        model: str = "",
        source_id: str = "",
        policy_key: str = "",
        surface: str = "",
    ) -> None:
        """One completed request: cumulative counters plus the windowed ledger.

        ``prefill_duration`` / ``generation_duration`` are *measured* seconds
        or ``0.0`` — never a fraction of total latency. A zero here does not
        move the duration accumulators, which is what keeps
        ``avg_prefill_tps`` ``None`` rather than a function of a constant.
        """
        for counter in (self._session, self._alltime):
            counter.requests += 1
            counter.prompt_tokens += max(0, prompt_tokens)
            counter.completion_tokens += max(0, completion_tokens)
            counter.total_prefill_duration += max(0.0, prefill_duration)
            counter.total_generation_duration += max(0.0, generation_duration)
            # Only tokens from a request that produced a real duration count
            # towards the rate — see `measured_prompt_tokens`.
            if prefill_duration > 0:
                counter.measured_prompt_tokens += max(0, prompt_tokens)
            if generation_duration > 0:
                counter.measured_completion_tokens += max(0, completion_tokens)
        self._save_alltime_debounced()
        self._ledger.record(
            backend_id=backend_id,
            model=model,
            source_id=source_id,
            policy_key=policy_key,
            surface=surface,
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
            latency_s=latency_s,
            ttft_s=ttft_s,
        )

    def record_request(self) -> None:
        """Count a routed request without token usage metadata."""
        self.record_usage()

    async def _get_client(self) -> Any:
        if self._http_client is None:
            import httpx

            self._http_client = httpx.AsyncClient(timeout=2.0)
        return self._http_client

    async def _probe_omlx(self, backends: list[Any]) -> dict[str, Any] | None:
        if not self.has_subscribers:
            return self._last_omlx_probe
        now = time.monotonic()
        if (
            self._last_omlx_probe is not None
            and now - self._last_omlx_probe_at < self._omlx_probe_interval_s
        ):
            return self._last_omlx_probe
        client = await self._get_client()
        stats = await probe_omlx_telemetry(backends, client)
        self._last_omlx_probe = stats
        self._last_omlx_probe_at = now
        if stats and stats.get("available"):
            live = stats.get("live") or {}
            self._history_omlx_pp.append(float(live.get("prefill_tps") or 0.0))
            self._history_omlx_tg.append(float(live.get("generation_tps") or 0.0))
        return stats

    def _router_block(self, pool: Any) -> dict[str, Any]:
        in_flight = sum(b.in_flight for b in pool.backends if b.enabled)
        now = time.time()
        ledger = self._ledger
        return {
            "session": self._session.to_dict(),
            "alltime": self._alltime.to_dict(),
            "routed_requests": dict(pool.routed_counts),
            "capacity_rejections": dict(pool.capacity_rejections),
            "shardless_fallbacks": getattr(pool, "shardless_fallbacks", 0),
            "in_flight_total": in_flight,
            "windows": ledger.windows_payload(now),
            "latency": ledger.latency_payload(now),
            "live": ledger.live_payload(now),
            "backends": [
                {
                    "id": b.id,
                    "provider": b.provider,
                    "base_url": b.base_url,
                    "health": b.health.status,
                    "in_flight": b.in_flight,
                    **ledger.backend_latency_payload(b.id, now),
                }
                for b in pool.backends
                if b.enabled
            ],
        }

    async def build_payload(
        self,
        service: Any,
        *,
        scopes: set[str] | None = None,
        include_history: bool = True,
    ) -> dict[str, Any]:
        active_scopes = scopes or {"router", "omlx"}
        payload: dict[str, Any] = {
            "schema_version": 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if "router" in active_scopes:
            shardless = getattr(service, "_shardless_fallbacks", 0)
            router = self._router_block(service.pool)
            router["shardless_fallbacks"] = shardless
            payload["router"] = router
        if "omlx" in active_scopes:
            omlx = await self._probe_omlx(service.pool.backends)
            payload["omlx"] = omlx or {"available": False}
        payload["host"] = self._host_block()
        if include_history:
            now = time.time()
            payload["history"] = {
                "router_rps": self._ledger.rps_series(now),
                "router_tps": self._ledger.tps_series(now),
                "omlx_pp_tps": self._history_omlx_pp.as_list(),
                "omlx_tg_tps": self._history_omlx_tg.as_list(),
            }
        return payload

    @staticmethod
    def _host_block() -> dict[str, Any] | None:
        try:
            import psutil
        except ImportError:
            return None
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_used_gb": round(vm.used / (1024**3), 2),
            "memory_total_gb": round(vm.total / (1024**3), 2),
            "memory_percent": vm.percent,
        }
