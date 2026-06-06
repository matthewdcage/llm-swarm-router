"""Batch executor — parallel prompts with sharding and failed-index retry."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

from netllm_core.models import RoutingStrategy
from netllm_core.pool import BatchDedupLedger, RouterPool

T = TypeVar("T")


def run_batch_shard(
    pool: RouterPool,
    model: str,
    count: int,
    worker: Callable[[int, str], T | None],
    *,
    strategy: RoutingStrategy = "batch_shard",
    max_workers: int = 4,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[T | None]:
    """Run indexed work across healthy backends with per-index failover."""
    plan = pool.plan_batch_shard(model, count, strategy=strategy)
    if not plan.assignments:
        return [None] * count

    results: list[T | None] = [None] * count
    pending = set(range(count))
    ledger = BatchDedupLedger(assignments=dict(plan.assignments))

    by_url: dict[str, list[int]] = defaultdict(list)
    for idx, url in plan.assignments.items():
        by_url[url].append(idx)

    done_count = 0
    total = count

    def _run_indices(base_url: str, indices: list[int]) -> list[tuple[int, T | None]]:
        out: list[tuple[int, T | None]] = []
        workers = min(max_workers, len(indices)) if indices else 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(worker, i, base_url): i for i in indices if i in pending
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    out.append((idx, future.result()))
                except Exception:
                    out.append((idx, None))
        return out

    urls = plan.endpoints
    for base_url, indices in by_url.items():
        for idx, value in _run_indices(base_url, indices):
            if value is not None:
                results[idx] = value
                ledger.mark_done(idx)
                pending.discard(idx)
            done_count += 1
            if on_progress:
                on_progress(done_count, total, idx)

    backends = pool.backends
    for idx in list(pending):
        current = ledger.assignments.get(idx, urls[0] if urls else "")
        next_url = ledger.reassign_failed(idx, backends, current_url=current)
        if not next_url:
            continue
        try:
            value = worker(idx, next_url)
        except Exception:
            value = None
        if value is not None:
            results[idx] = value
            pending.discard(idx)

    return results
