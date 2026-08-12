"""CLI status telemetry window table helpers."""

from __future__ import annotations

from netllm_cli.commands.observe import (
    _telemetry_span_label,
    _telemetry_traffic_row,
    _telemetry_window_span,
)


def test_telemetry_window_span_prefers_five_minutes() -> None:
    windows = {"spans_s": [60, 300, 86400]}
    assert _telemetry_window_span(windows) == 300


def test_telemetry_traffic_row_reads_nested_buckets() -> None:
    raw = {
        "requests": {"300": 4},
        "prompt_tokens": {"300": 120},
        "completion_tokens": {"300": 40},
        "avg_prefill_tps": {"300": 142.5},
        "avg_generation_tps": {"300": None},
    }
    row = _telemetry_traffic_row(raw, 300)
    assert row["requests"] == 4
    assert row["avg_prefill_tps"] == 142.5
    assert row["avg_generation_tps"] is None


def test_telemetry_span_label_minutes() -> None:
    assert _telemetry_span_label(300) == "5 min"
