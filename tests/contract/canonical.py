"""Canonicalizer with an explicit volatile-field schema.

Everything checked into a contract vector must be byte-for-byte stable
across runs and machines. The schema below is the complete list of fields
allowed to vary at runtime and how each is normalized — anything NOT in the
schema is compared verbatim, so a new source of nondeterminism fails loudly
instead of being silently blessed.

Volatile-field schema
=====================

Response bodies / SSE data payloads (recursive, by key):
- ``id``               -> "<id>"        (upstream/request identifiers)
- ``created``          -> "<ts>"        (unix timestamps)
- ``created_at``       -> "<ts>"
- ``system_fingerprint`` -> "<fp>"

Metrics delta:
- ``netllm_request_latency_seconds_sum{...}`` -> ">0" when positive
  (wall-clock latency; the paired ``_count`` stays exact)

Pool delta:
- ``latency_ema_changed`` booleans -> "updated" / "unchanged"
  (the EMA's numeric value is wall-clock; whether it moved is contract)
"""

from __future__ import annotations

from typing import Any

VOLATILE_BODY_FIELDS: dict[str, str] = {
    "id": "<id>",
    "created": "<ts>",
    "created_at": "<ts>",
    "system_fingerprint": "<fp>",
}

_LATENCY_SUM = "netllm_request_latency_seconds_sum"


def canonicalize_json(obj: Any) -> Any:
    """Normalize volatile fields recursively; sort dict keys."""
    if isinstance(obj, dict):
        out = {}
        for key in sorted(obj):
            if key in VOLATILE_BODY_FIELDS and isinstance(obj[key], (str, int, float)):
                out[key] = VOLATILE_BODY_FIELDS[key]
            else:
                out[key] = canonicalize_json(obj[key])
        return out
    if isinstance(obj, list):
        return [canonicalize_json(item) for item in obj]
    return obj


def canonicalize_sse_frames(frames: list[str] | None) -> list[list[str]] | None:
    """SSE frames -> list of line lists with canonicalized data payloads."""
    import json

    if frames is None:
        return None
    out: list[list[str]] = []
    for frame in frames:
        lines: list[str] = []
        for line in frame.split("\n"):
            if line.startswith("data: "):
                data = line[len("data: ") :]
                if data.strip() == "[DONE]":
                    lines.append(line)
                    continue
                try:
                    parsed = json.loads(data)
                except ValueError:
                    lines.append(line)
                    continue
                lines.append(
                    "data: " + json.dumps(canonicalize_json(parsed), sort_keys=True)
                )
            else:
                lines.append(line)
        out.append(lines)
    return out


def canonicalize_metrics(metrics: dict[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(metrics):
        value = metrics[key]
        if key.startswith(_LATENCY_SUM):
            out[key] = ">0" if value > 0 else 0
        else:
            out[key] = value
    return out


def canonicalize_pool_delta(pool: dict[str, Any]) -> dict[str, Any]:
    out = dict(pool)
    out["latency_ema_changed"] = {
        url: "updated" if changed else "unchanged"
        for url, changed in sorted(pool.get("latency_ema_changed", {}).items())
    }
    return canonicalize_json(out)


def canonicalize_upstream_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order-preserving; headers/models are deterministic farm inputs and
    are kept verbatim (they ARE the contract: per-backend model + peer
    loop-guard headers)."""
    return [canonicalize_json(call) for call in calls]


def canonicalize_result(result: Any, delta: dict[str, Any], calls: list) -> dict:
    """Full vector "expected" payload from a DriveResult + probe delta."""
    return {
        "status": result.status,
        "body_shape": canonicalize_json(result.body),
        "sse_frames": canonicalize_sse_frames(result.frames),
        "upstream_calls": canonicalize_upstream_calls(calls),
        "pool_delta": canonicalize_pool_delta(delta["pool"]),
        "metrics_delta": {
            "counters": canonicalize_metrics(delta["metrics"]),
            "gauges": canonicalize_json(delta["gauges"]),
        },
        "admission_delta": canonicalize_json(delta["admission"]),
    }
