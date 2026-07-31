"""ServiceProbe — before/after state snapshots as a normalized delta dict.

The agent's Prometheus metrics are module-level and registered on the
default ``prometheus_client.REGISTRY`` (netllm_agent/metrics.py), so a
fresh CollectorRegistry per test is impossible without re-importing the
production modules. Per the plan's fallback, the probe snapshots the
default registry before/after and reports counter *deltas* (accumulated
values from other tests cancel out) plus final gauge values, restricted to
backends actually present in the probed service's pool so state stamped by
other tests on other backends never leaks in.

The delta dict is raw (floats, ints, bools); tests/contract/canonical.py
applies the volatile-field schema (latency sums -> ">0", EMA -> "updated")
before anything is compared against a checked-in vector.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import REGISTRY

# Counter-style samples diffed before/after (order: family sample names as
# emitted by prometheus_client; *_created timestamp samples and histogram
# buckets are deliberately excluded — wall-clock / noise).
_COUNTER_SAMPLES = {
    "netllm_requests_total",
    "netllm_source_requests_total",
    "netllm_scenario_requests_total",
    "netllm_prompt_tokens_total",
    "netllm_completion_tokens_total",
    "netllm_request_latency_seconds_count",
    "netllm_request_latency_seconds_sum",
}
# Gauges reported as final absolute values (leak detectors).
_GAUGE_SAMPLES = {
    "netllm_backend_in_flight",
    "netllm_backend_healthy",
}


def _sample_key(name: str, labels: dict[str, str]) -> str:
    inner = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}{{{inner}}}"


def _collect(kinds: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name in kinds:
                out[_sample_key(sample.name, dict(sample.labels))] = sample.value
    return out


class ServiceProbe:
    """Snapshot pool state, telemetry counters, and admission counters."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self._before = self._snapshot()

    # -- snapshots ------------------------------------------------------

    def _pool_urls(self) -> set[str]:
        return {b.base_url for b in self.service.pool.backends}

    def _snapshot(self) -> dict[str, Any]:
        service = self.service
        pool = service.pool
        return {
            "counters": _collect(_COUNTER_SAMPLES),
            "gauges": _collect(_GAUGE_SAMPLES),
            "routed_counts": dict(pool.routed_counts),
            "capacity_rejections": dict(pool.capacity_rejections),
            "latency_ema": {b.base_url: b.latency_ema_ms for b in pool.backends},
            "source_counts": dict(service._source_counts),
            "scenario_counts": dict(service._scenario_counts),
            "request_count": service._request_count,
            "shardless_fallbacks": service._shardless_fallbacks,
        }

    # -- delta ----------------------------------------------------------

    def delta(self) -> dict[str, Any]:
        """Normalized delta since construction (or the last ``reset()``)."""
        before = self._before
        after = self._snapshot()
        pool = self.service.pool
        urls = self._pool_urls()

        def diff_map(key: str) -> dict[str, float]:
            b, a = before[key], after[key]
            out = {}
            for k in sorted(set(b) | set(a)):
                d = a.get(k, 0) - b.get(k, 0)
                if d:
                    out[k] = d
            return out

        def scoped_gauges() -> dict[str, float]:
            out = {}
            for key, value in sorted(after["gauges"].items()):
                if any(f"backend={url}" in key for url in urls):
                    out[key] = value
            return out

        metrics = {}
        for key in sorted(set(before["counters"]) | set(after["counters"])):
            d = after["counters"].get(key, 0.0) - before["counters"].get(key, 0.0)
            if d:
                metrics[key] = d

        return {
            "metrics": metrics,
            "gauges": scoped_gauges(),
            "pool": {
                "routed_counts": diff_map("routed_counts"),
                "capacity_rejections": diff_map("capacity_rejections"),
                "in_flight": {
                    b.base_url: b.in_flight
                    for b in sorted(pool.backends, key=lambda b: b.base_url)
                },
                "health_status": {
                    b.base_url: b.health.status
                    for b in sorted(pool.backends, key=lambda b: b.base_url)
                },
                "latency_ema_changed": {
                    url: after["latency_ema"].get(url, 0.0)
                    != before["latency_ema"].get(url, 0.0)
                    for url in sorted(after["latency_ema"])
                },
            },
            "admission": {
                "source_counts": diff_map("source_counts"),
                "scenario_counts": {
                    f"{sid}:{scenario}": d
                    for (sid, scenario), d in sorted(
                        (
                            k,
                            after["scenario_counts"].get(k, 0)
                            - before["scenario_counts"].get(k, 0),
                        )
                        for k in set(before["scenario_counts"])
                        | set(after["scenario_counts"])
                    )
                    if d
                },
                "source_in_flight": {
                    sid: n for sid, n in sorted(self.service._source_in_flight.items())
                },
                "request_count": after["request_count"] - before["request_count"],
                "shardless_fallbacks": after["shardless_fallbacks"]
                - before["shardless_fallbacks"],
            },
        }

    def reset(self) -> None:
        self._before = self._snapshot()
