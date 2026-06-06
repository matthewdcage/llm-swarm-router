"""Tests for batch executor."""

from __future__ import annotations

from netllm_core.batch import run_batch_shard
from netllm_core.models import Backend, BackendHealth
from netllm_core.pool import RouterPool


def test_batch_executor_shards_and_retries() -> None:
    pool = RouterPool()
    pool.set_backends(
        [
            Backend(
                id="a",
                base_url="http://a/v1",
                health=BackendHealth(models=["m"], status="online"),
            ),
            Backend(
                id="b",
                base_url="http://b/v1",
                health=BackendHealth(models=["m"], status="online"),
            ),
        ]
    )
    calls: list[tuple[int, str]] = []

    def worker(idx: int, base_url: str) -> str | None:
        calls.append((idx, base_url))
        if idx == 1 and base_url == "http://a/v1":
            return None
        return f"ok-{idx}"

    results = run_batch_shard(pool, "m", 3, worker, max_workers=2)
    assert results[0] == "ok-0"
    assert results[2] == "ok-2"
    assert any(c[0] == 1 for c in calls)
