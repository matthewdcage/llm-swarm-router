"""Prometheus metrics for netllm agent."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

REQUESTS_TOTAL = Counter(
    "netllm_requests_total",
    "Total proxied LLM requests",
    ["backend", "model", "status"],
)

REQUEST_LATENCY = Histogram(
    "netllm_request_latency_seconds",
    "Request latency in seconds",
    ["backend"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

BACKEND_HEALTH = Gauge(
    "netllm_backend_healthy",
    "1 if backend is healthy",
    ["backend", "provider"],
)

BACKEND_IN_FLIGHT = Gauge(
    "netllm_backend_in_flight",
    "In-flight requests per backend",
    ["backend"],
)


def metrics_bytes() -> bytes:
    return generate_latest()
