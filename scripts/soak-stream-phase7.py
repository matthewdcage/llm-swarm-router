#!/usr/bin/env python3
"""Phase 7 pre-merge soak (plan-f24-f26.md §3 Phase 7 exit criteria).

A mixed-workload run against FakeFarm, long enough for a leak to show up as
a trend rather than as noise. The contract vectors prove *what a client
sees*; this proves what the process looks like afterwards.

Workload
--------
- all seven inference paths (C-NS, C-S, EMB, M-NS, M-S, RESP-NS, RESP-S),
  weighted toward the streaming ones because they are the phase's risk;
- concurrency deliberately **above** the per-source ``max_concurrency``
  caps, so a large fraction of requests are admission 429s and the
  admission ledger is under constant contention;
- ~10% injected upstream failures and ~5% mid-stream drops, drawn from a
  seeded RNG so a failure is reproducible;
- random client aborts: a slice of the streaming work is driven against the
  service generator and ``aclose()``d after one or two chunks, which is the
  only reachable analogue of Starlette dropping the client mid-body (httpx's
  ASGI transport buffers a streaming response to completion);
- one backend flapping 503 in blocks, so rows go sick and recover while
  traffic is in flight.

Assertions (all at quiescence, plus memory sampled throughout)
--------------------------------------------------------------
1. every ``Backend.in_flight`` and every ``netllm_backend_in_flight`` gauge
   is back to 0;
2. every ``_source_in_flight`` entry is back to 0;
3. ``pool.acquire`` and ``pool.release`` were called the same number of
   times — the pairing, not just the end state;
4. ``REQUESTS_TOTAL`` (ok + error) equals the harness-observed attempt
   count **exactly** at the boundary where that sentence is true — the
   router's own attempts, counted by ``pool.acquire``. Every acquired
   attempt reaches exactly one terminal accounting state, so
   ``REQUESTS_TOTAL == acquires`` except for the streams the client
   deliberately abandoned mid-flight, and the residual is bounded by the
   abort count: ``0 <= acquires - REQUESTS_TOTAL <= aborts``. Nothing may
   be lost outside an abort, and nothing may be counted twice.

   The *wire* count is reported alongside but is deliberately not the
   identity: the official SDKs retry transport-level errors internally
   (``max_retries``, independent of the ``x-should-retry: false`` the farm
   sets on scripted HTTP errors), so a dropped connection can produce two
   wire calls under one router attempt. That is below the router and not
   this phase's contract; it is asserted only as a bounded surplus.
5. memory is stable: traced allocations and live object count at the end
   are within a small factor of the post-warmup baseline.

Usage::

    uv run python scripts/soak-stream-phase7.py            # ~11 minutes
    SOAK_SECONDS=60 uv run python scripts/soak-stream-phase7.py
"""

from __future__ import annotations

import asyncio
import gc
import os
import random
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "contract"))

import httpx  # noqa: E402
from drivers import contract_environment  # noqa: E402

SEED = int(os.environ.get("SOAK_SEED", "20260801"))
DURATION_S = float(os.environ.get("SOAK_SECONDS", "660"))
WORKERS = int(os.environ.get("SOAK_WORKERS", "24"))
SOURCE_CAP = int(os.environ.get("SOAK_SOURCE_CAP", "4"))
WARMUP_S = 20.0

# Two backend families, because a `midstream_drop` is a *transport* error
# to a non-streaming caller and the official SDKs retry those internally
# (max_retries, independent of the `x-should-retry: false` the farm sets on
# scripted HTTP errors). One wire retry under one router attempt would make
# "upstream calls == accounted attempts" false for reasons that have nothing
# to do with this phase. Confining drops to models only the stream paths ask
# for keeps the identity exact and keeps the injection honest.
CHAT = "farm-chat"  # non-stream family
CHAT_S = "farm-chat-s"  # stream family
EMBED = "farm-embed"
CLAUDE = "claude-farm"
CLAUDE_S = "claude-farm-s"
USER = [{"role": "user", "content": "hi"}]

# Weighted path mix; the stream paths carry the phase's risk.
PATHS = (
    ["chat_s"] * 4
    + ["messages_s"] * 4
    + ["responses_s"] * 3
    + ["chat_ns"] * 2
    + ["messages_ns"] * 2
    + ["responses_ns"] * 1
    + ["emb"] * 2
)
STREAM_PATHS = {"chat_s", "messages_s", "responses_s"}
_STREAM_METHOD = {
    "chat_s": "proxy_chat_completion_stream",
    "messages_s": "proxy_messages_stream",
    "responses_s": "proxy_responses_stream",
}
_ROUTE = {
    "chat_ns": "/v1/chat/completions",
    "chat_s": "/v1/chat/completions",
    "emb": "/v1/embeddings",
    "messages_ns": "/v1/messages",
    "messages_s": "/v1/messages",
    "responses_ns": "/v1/responses",
    "responses_s": "/v1/responses",
}
# Sources exist only to put the admission ledger under contention.
SOURCES = ("cli", "editor", "batch")


def _body(path: str, stream: bool) -> dict[str, Any]:
    chat_model = CHAT_S if stream else CHAT
    if path == "emb":
        return {"model": EMBED, "input": "hi"}
    if path.startswith("messages"):
        model = CLAUDE_S if stream else CLAUDE
        return {"model": model, "messages": USER, "max_tokens": 16, "stream": stream}
    if path.startswith("responses"):
        return {"model": chat_model, "input": "hi", "stream": stream}
    return {"model": chat_model, "messages": USER, "stream": stream}


def _script(rng: random.Random, n: int, *, streaming: bool) -> list[dict[str, Any]]:
    """~10% hard failures, ~5% mid-stream drops, the rest success."""
    out: list[dict[str, Any]] = []
    chunks = ["a", "b", "c", "d", "e", "f", "g", "h"] if streaming else None
    for _ in range(n):
        roll = rng.random()
        if roll < 0.10:
            out.append({"behavior": "http", "status": rng.choice([500, 502, 503, 429])})
        elif roll < 0.15 and streaming:
            out.append({"behavior": "midstream_drop", "after_n": rng.randint(0, 3)})
        elif chunks is not None:
            # "ok" (not "ok_stream") is the polymorphic behavior: the farm
            # streams it when the request asked for a stream and returns
            # JSON otherwise, which is what a mixed workload needs from one
            # script cursor. `chunks` still lengthens the stream so a client
            # abort at chunk 1-2 really lands mid-body.
            out.append(
                {
                    "behavior": "ok",
                    "chunks": chunks,
                    "usage_in_final": rng.random() < 0.5,
                }
            )
        else:
            out.append({"behavior": "ok"})
    return out


def _flapping_script(n: int) -> list[dict[str, Any]]:
    """One backend that goes sick in blocks and comes back."""
    out: list[dict[str, Any]] = []
    while len(out) < n:
        out.extend([{"behavior": "http", "status": 503}] * 40)
        out.extend([{"behavior": "ok"}] * 120)
    return out[:n]


def build_scenario(rng: random.Random, script_len: int) -> dict[str, Any]:
    def row(
        name: str, models: list[str], *, streaming: bool, fmt: str = "openai"
    ) -> dict[str, Any]:
        return {
            "name": name,
            "api_format": fmt,
            "models": models,
            "script": _script(rng, script_len, streaming=streaming),
        }

    def flapper(name: str, models: list[str], fmt: str = "openai") -> dict[str, Any]:
        return {
            "name": name,
            "api_format": fmt,
            "models": models,
            "script": _flapping_script(script_len),
        }

    return {
        "backends": [
            # stream family
            row("s-alpha", [CHAT_S], streaming=True),
            row("s-beta", [CHAT_S], streaming=True),
            flapper("s-flap", [CHAT_S]),
            row("s-ant1", [CLAUDE_S], streaming=True, fmt="anthropic"),
            row("s-ant2", [CLAUDE_S], streaming=True, fmt="anthropic"),
            # non-stream family
            row("n-alpha", [CHAT, EMBED], streaming=False),
            row("n-beta", [CHAT, EMBED], streaming=False),
            flapper("n-flap", [CHAT, EMBED]),
            row("n-ant1", [CLAUDE], streaming=False, fmt="anthropic"),
            row("n-ant2", [CLAUDE], streaming=False, fmt="anthropic"),
        ],
        "routing": {
            "default_strategy": "round_robin",
            "sources": [
                {"id": sid, "enabled": True, "max_concurrency": SOURCE_CAP}
                for sid in SOURCES
            ],
        },
    }


class Counters:
    def __init__(self) -> None:
        self.issued = 0
        self.aborted = 0
        self.admission_429 = 0
        self.statuses: dict[int, int] = {}
        self.errors: list[str] = []
        # Drained from the farm periodically rather than read at the end:
        # an 11-minute run records tens of thousands of calls, and letting
        # that list grow would put harness bookkeeping inside the memory
        # trend the soak is supposed to be measuring.
        self.upstream_calls = 0
        self.probe_calls = 0
        # Attempts the AttemptRecorder actually settled. Counted through a
        # wrapper rather than read off Prometheus so the two can be checked
        # against each other: if they ever disagree, something other than
        # the recorder is writing REQUESTS_TOTAL.
        self.accounted = 0

    def status(self, code: int) -> None:
        self.statuses[code] = self.statuses.get(code, 0) + 1
        if code == 429:
            self.admission_429 += 1


async def _one_request(
    client: httpx.AsyncClient,
    env: Any,
    rng: random.Random,
    counters: Counters,
) -> None:
    path = rng.choice(PATHS)
    source = rng.choice(SOURCES)
    headers = {"x-netllm-source": source, "x-api-key": "caller-key"}
    counters.issued += 1

    # ~1 stream in 6 is abandoned by the "client" after a chunk or two.
    if path in STREAM_PATHS and rng.random() < 0.17:
        gen = getattr(env.service, _STREAM_METHOD[path])(
            _body(path, True), headers=headers
        )
        stop_after = rng.randint(1, 2)
        seen = 0
        try:
            async for _chunk in gen:
                seen += 1
                if seen >= stop_after:
                    break
        except Exception:
            # An exhausted/keyless/capacity failure raised on first anext is
            # a normal outcome here; nothing was aborted.
            return
        finally:
            await gen.aclose()
        if seen >= stop_after:
            counters.aborted += 1
        return

    stream = path in STREAM_PATHS
    try:
        resp = await client.post(
            _ROUTE[path], json=_body(path, stream), headers=headers
        )
        counters.status(resp.status_code)
    except Exception as exc:  # pragma: no cover - soak diagnostics
        counters.errors.append(f"{path}: {type(exc).__name__}: {exc}")


async def _worker(
    client: httpx.AsyncClient,
    env: Any,
    rng: random.Random,
    counters: Counters,
    deadline: float,
) -> None:
    while time.monotonic() < deadline:
        await _one_request(client, env, rng, counters)
        await asyncio.sleep(0)


def _drain_farm(env: Any, counters: Counters) -> None:
    """Fold the farm's recorded calls into counters and drop the records."""
    recorded, env.farm.requests = env.farm.requests, []
    for call in recorded:
        if _is_diagnose_probe(call.body):
            counters.probe_calls += 1
        else:
            counters.upstream_calls += 1


def _is_diagnose_probe(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and body.get("max_tokens") == 1
        and body.get("stream") is False
        and isinstance(body.get("messages"), list)
    )


def _requests_total(env: Any) -> float:
    from prometheus_client import REGISTRY

    total = 0.0
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "netllm_requests_total":
                total += sample.value
    return total


def _in_flight_gauges(env: Any) -> dict[str, float]:
    from prometheus_client import REGISTRY

    urls = {b.base_url for b in env.service.pool.backends}
    out: dict[str, float] = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "netllm_backend_in_flight":
                url = sample.labels.get("backend", "")
                if url in urls:
                    out[url] = sample.value
    return out


async def _run(env: Any, counters: Counters) -> list[tuple[float, int, int]]:
    rng_pool = [random.Random(SEED + i) for i in range(WORKERS)]
    transport = httpx.ASGITransport(app=env.client.app)
    samples: list[tuple[float, int, int]] = []
    started = time.monotonic()
    deadline = started + DURATION_S

    async with httpx.AsyncClient(
        transport=transport, base_url="http://soak", timeout=30.0
    ) as client:
        workers = [
            asyncio.create_task(_worker(client, env, rng_pool[i], counters, deadline))
            for i in range(WORKERS)
        ]

        async def sampler() -> None:
            baseline_taken = False
            while time.monotonic() < deadline:
                await asyncio.sleep(10.0)
                elapsed = time.monotonic() - started
                if not baseline_taken and elapsed >= WARMUP_S:
                    baseline_taken = True
                _drain_farm(env, counters)
                gc.collect()
                current, _peak = tracemalloc.get_traced_memory()
                samples.append((elapsed, current, len(gc.get_objects())))
                print(
                    f"  t={elapsed:6.0f}s  issued={counters.issued:<7d} "
                    f"traced={current / 1e6:7.2f} MB  objects={samples[-1][2]}",
                    flush=True,
                )

        sample_task = asyncio.create_task(sampler())
        await asyncio.gather(*workers)
        sample_task.cancel()
        try:
            await sample_task
        except asyncio.CancelledError:
            pass
    return samples


def main() -> int:
    rng = random.Random(SEED)
    script_len = 400_000
    scenario = build_scenario(rng, script_len)
    counters = Counters()

    tracemalloc.start()
    print(
        f"soak: {DURATION_S:.0f}s, {WORKERS} workers, source cap {SOURCE_CAP}, "
        f"seed {SEED}",
        flush=True,
    )

    with contract_environment(scenario) as env:
        pool = env.service.pool
        acquire_calls = release_calls = 0
        real_acquire, real_release = pool.acquire, pool.release

        def counting_acquire(backend: Any) -> Any:
            nonlocal acquire_calls
            acquire_calls += 1
            return real_acquire(backend)

        def counting_release(backend: Any) -> Any:
            nonlocal release_calls
            release_calls += 1
            return real_release(backend)

        pool.acquire = counting_acquire  # type: ignore[method-assign]
        pool.release = counting_release  # type: ignore[method-assign]

        from netllm_agent.service import AttemptRecorder

        real_success = AttemptRecorder.success
        real_failure = AttemptRecorder.failure

        def counting_success(self: Any, **kw: Any) -> Any:
            counters.accounted += 1
            return real_success(self, **kw)

        def counting_failure(
            self: Any, *, backend: Any, model: str, exc: Exception
        ) -> Any:
            # Mirror the recorder's own dedup: a re-recorded exception does
            # not increment REQUESTS_TOTAL, so it must not increment here.
            if not self.already_recorded(exc):
                counters.accounted += 1
            return real_failure(self, backend=backend, model=model, exc=exc)

        AttemptRecorder.success = counting_success  # type: ignore[method-assign]
        AttemptRecorder.failure = counting_failure  # type: ignore[method-assign]

        before_requests_total = _requests_total(env)
        samples = asyncio.run(_run(env, counters))

        # Let any generator finalization the loop scheduled actually run.
        gc.collect()
        time.sleep(0.5)
        gc.collect()

        # The farm records the local-provider scan's 1-token *diagnose*
        # probes (netllm_core.health.diagnose_backend) on the same inference
        # path as real traffic. They are discovery, not routed requests, so
        # they were never meant to reach REQUESTS_TOTAL; excluded by their
        # unmistakable body rather than by a harness change.
        _drain_farm(env, counters)
        probe_calls = counters.probe_calls
        upstream_calls = counters.upstream_calls
        requests_total = _requests_total(env) - before_requests_total
        AttemptRecorder.success = real_success  # type: ignore[method-assign]
        AttemptRecorder.failure = real_failure  # type: ignore[method-assign]
        gauges = _in_flight_gauges(env)
        backend_in_flight = {b.base_url: b.in_flight for b in pool.backends}
        source_in_flight = dict(env.service._source_in_flight)

    failures: list[str] = []

    def check(ok: bool, label: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}", flush=True)
        if not ok:
            failures.append(label)

    print("\n--- workload ---", flush=True)
    print(f"  issued           : {counters.issued}", flush=True)
    print(f"  statuses         : {dict(sorted(counters.statuses.items()))}", flush=True)
    print(f"  admission 429s   : {counters.admission_429}", flush=True)
    print(f"  client aborts    : {counters.aborted}", flush=True)
    print(f"  upstream calls   : {upstream_calls}", flush=True)
    print(f"  discovery probes : {probe_calls}", flush=True)
    print(f"  REQUESTS_TOTAL   : {requests_total:.0f}", flush=True)
    print(f"  acquire/release  : {acquire_calls}/{release_calls}", flush=True)
    print(f"  accounted        : {counters.accounted}", flush=True)
    if counters.errors:
        print(f"  transport errors : {counters.errors[:5]}", flush=True)

    print("\n--- assertions ---", flush=True)
    check(not counters.errors, f"no transport-level errors ({len(counters.errors)})")
    check(counters.issued > 1000, f"workload was substantial ({counters.issued})")
    check(
        counters.admission_429 > 0,
        f"concurrency exceeded the source caps ({counters.admission_429} 429s)",
    )
    check(counters.aborted > 0, f"client aborts were exercised ({counters.aborted})")
    check(
        all(v == 0 for v in backend_in_flight.values()),
        f"pool in_flight back to zero ({backend_in_flight})",
    )
    check(
        all(v == 0 for v in gauges.values()),
        f"BACKEND_IN_FLIGHT gauges back to zero ({gauges})",
    )
    check(
        all(v == 0 for v in source_in_flight.values()),
        f"_source_in_flight back to zero ({source_in_flight})",
    )
    check(
        acquire_calls == release_calls,
        f"acquire/release balanced ({acquire_calls} vs {release_calls})",
    )
    unaccounted = acquire_calls - int(requests_total)
    wire_surplus = upstream_calls - acquire_calls
    check(
        counters.accounted == int(requests_total),
        f"REQUESTS_TOTAL is exactly what the recorder wrote "
        f"({counters.accounted} vs {requests_total:.0f})",
    )
    check(
        unaccounted >= 0,
        f"no attempt was accounted twice (acquires {acquire_calls} >= "
        f"REQUESTS_TOTAL {requests_total:.0f})",
    )
    check(
        unaccounted <= counters.aborted,
        f"every unaccounted attempt is a deliberate client abort "
        f"({unaccounted} unaccounted <= {counters.aborted} aborts)",
    )
    check(
        wire_surplus >= 0,
        f"no upstream call happened without an attempt "
        f"(wire {upstream_calls} >= acquires {acquire_calls})",
    )
    check(
        wire_surplus <= 0.05 * max(upstream_calls, 1),
        f"below-router SDK transport retries stayed marginal "
        f"({wire_surplus}, {100 * wire_surplus / max(upstream_calls, 1):.2f}%)",
    )

    if len(samples) >= 3:
        baseline = samples[len(samples) // 3]
        final = samples[-1]
        mem_growth = final[1] / max(baseline[1], 1)
        obj_growth = final[2] / max(baseline[2], 1)
        print(
            f"\n  memory baseline t={baseline[0]:.0f}s "
            f"{baseline[1] / 1e6:.2f} MB / {baseline[2]} objects",
            flush=True,
        )
        print(
            f"  memory final    t={final[0]:.0f}s "
            f"{final[1] / 1e6:.2f} MB / {final[2]} objects",
            flush=True,
        )
        check(mem_growth < 1.5, f"traced memory stable (x{mem_growth:.2f})")
        check(obj_growth < 1.5, f"live object count stable (x{obj_growth:.2f})")
    else:
        print("  (too few memory samples to trend — raise SOAK_SECONDS)", flush=True)

    tracemalloc.stop()
    if failures:
        print(f"\nSOAK FAILED: {failures}", flush=True)
        return 1
    print("\nSOAK PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
