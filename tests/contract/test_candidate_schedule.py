"""Phase 5 characterization tests — CandidateSchedule + selection.

Covers the four Phase 5 divergences the vector corpus cannot pin on its own:

- **D7** the attempt budget. The property test below is the plan's exit
  criterion in executable form: *attempts <= max_attempts, every candidate
  tried at most once, tier order respected* — asserted against the schedule
  the request actually built, over a matrix of surfaces and topologies.
- **D7 / capacity storm.** Many concurrent capacity rejections must stay
  inside the budget and must never trip a backend offline (pool.py: a
  capacity rejection is counted, not a failure).
- **D13** peer loop-guard headers on the anthropic-format arm. The farm can
  only mint pool rows from ``routing.backends`` overrides, whose ids are
  base URLs or scan uuid5s — never ``peer:<agent>`` — so, exactly like the
  existing peer tests in ``scenarios_routing_failover``, the peer row is
  constructed here and driven through the real invoker. The assertion is
  wire-level: the farm records what the Anthropic SDK actually sent.
- **D5** shard context reaching selection on EMB / M-NS / M-S, including the
  batch-ledger arm's newfound obligation to honour ``exclude_ids``.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from drivers import contract_environment
from farm import FARM_API_KEY, http, ok
from netllm_agent.candidates import CandidateSchedule, excluded_api_formats
from netllm_agent.taxonomy import Surface
from netllm_core.models import OPENAI_CLOUD_BASE_URL, Backend, BackendHealth

CHAT = "farm-chat"
EMBED = "bge-m3"
CLAUDE = "claude-farm"
USER = [{"role": "user", "content": "hi"}]


def _spy_schedules(service: Any) -> list[CandidateSchedule]:
    """Record every CandidateSchedule the service builds for a request."""
    seen: list[CandidateSchedule] = []
    original = service.build_candidates

    def spy(plan: Any, cloud_extra: Any) -> CandidateSchedule:
        schedule = original(plan, cloud_extra)
        seen.append(schedule)
        return schedule

    service.build_candidates = spy  # type: ignore[method-assign]
    return seen


# ---------------------------------------------------------------------------
# D7 — the property test named in plan §3 Phase 5
# ---------------------------------------------------------------------------

# (id, scenario, request) triples spanning every schedule shape: no tier /
# tier only / both phases, with and without a request-scoped cloud extra,
# on every surface that has its own loop.
_BUDGET_CASES: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    (
        "chat-ns-all-fail",
        {
            "backends": [
                {"name": "alpha", "models": [CHAT], "script": [http(500)]},
                {"name": "beta", "models": [CHAT], "script": [http(500)]},
                {"name": "gamma", "models": [CHAT], "script": [http(500)]},
            ],
            "routing": {"default_strategy": "failover"},
        },
        {"path": "chat_ns", "body": {"model": CHAT, "messages": USER}},
    ),
    (
        "chat-s-all-fail",
        {
            "backends": [
                {"name": "alpha", "models": [CHAT], "script": [http(503)]},
                {"name": "beta", "models": [CHAT], "script": [http(503)]},
            ],
            "routing": {"default_strategy": "round_robin"},
        },
        {"path": "chat_s", "body": {"model": CHAT, "messages": USER}},
    ),
    (
        "chat-ns-with-legacy-cloud-row",
        {
            "backends": [
                {"name": "alpha", "models": [CHAT], "script": [http(500)]},
                {
                    "name": "oaicloud",
                    "models": [CHAT],
                    "script": [http(500)],
                    "url": OPENAI_CLOUD_BASE_URL,
                    "configured": False,
                    "local": False,
                },
            ],
            "routing": {"default_strategy": "failover"},
            "cloud": {"enabled": True, "fallback": "cloud"},
        },
        {
            "path": "chat_ns",
            "headers": {"authorization": "Bearer sk-caller-key"},
            "body": {"model": CHAT, "messages": USER},
        },
    ),
    (
        "emb-mixed-dialect-pool",
        {
            "backends": [
                {"name": "ant", "api_format": "anthropic", "models": [EMBED]},
                {"name": "embed1", "models": [EMBED], "script": [http(500)]},
                {"name": "embed2", "models": [EMBED], "script": [http(500)]},
            ],
            "routing": {"default_strategy": "failover"},
        },
        {"path": "emb", "body": {"model": EMBED, "input": "hi"}},
    ),
    (
        "messages-ns-both-phases",
        {
            "backends": [
                {"name": "alpha", "models": [CLAUDE], "script": [http(503)]},
                {
                    "name": "anthro1",
                    "api_format": "anthropic",
                    "models": [CLAUDE],
                    "script": [http(503)],
                },
                {
                    "name": "anthro2",
                    "api_format": "anthropic",
                    "models": [CLAUDE],
                    "script": [http(503)],
                },
            ],
            "routing": {"default_strategy": "failover"},
        },
        {
            "path": "messages_ns",
            "headers": {"x-api-key": "caller"},
            "body": {"model": CLAUDE, "max_tokens": 16, "messages": USER},
        },
    ),
    (
        "messages-s-both-phases",
        {
            "backends": [
                {"name": "alpha", "models": [CLAUDE], "script": [http(503)]},
                {
                    "name": "anthro1",
                    "api_format": "anthropic",
                    "models": [CLAUDE],
                    "script": [http(503)],
                },
                {
                    "name": "anthro2",
                    "api_format": "anthropic",
                    "models": [CLAUDE],
                    "script": [http(503)],
                },
            ],
            "routing": {"default_strategy": "least_load"},
        },
        {
            "path": "messages_s",
            "headers": {"x-api-key": "caller"},
            "body": {"model": CLAUDE, "max_tokens": 16, "messages": USER},
        },
    ),
]


@pytest.mark.parametrize(
    ("scenario", "request_"),
    [(s, r) for _, s, r in _BUDGET_CASES],
    ids=[case_id for case_id, _, _ in _BUDGET_CASES],
)
def test_attempts_stay_inside_the_schedule(
    scenario: dict[str, Any], request_: dict[str, Any]
) -> None:
    """[D7] attempts <= max_attempts, each candidate once, tier order kept.

    Every candidate in these scenarios fails, so the loop spends the whole
    budget: the number of upstream calls the farm saw *is* the number of
    attempts the loop made.
    """
    with contract_environment(scenario) as env:
        schedules = _spy_schedules(env.service)
        env.drive(request_)
        calls = env.farm.inference_calls()
        url_by_name = {b.name: b.base_url for b in env.farm.backends}

    assert len(schedules) == 1, "one request must build exactly one schedule"
    schedule = schedules[0]
    attempted = [call["backend"] for call in calls]

    # 1. bounded
    assert len(attempted) <= schedule.max_attempts, (
        f"{len(attempted)} attempts against a budget of "
        f"{schedule.max_attempts} "
        f"(strategy {schedule.strategy_attempts} + fallback "
        f"{schedule.fallback_attempts})"
    )
    # 2. no candidate tried twice
    assert len(attempted) == len(set(attempted)), f"repeated attempt in {attempted}"
    # 3. tier order — once the fallback phase starts it never goes back
    tier_urls = {b.base_url for b in schedule.fallback_backends()}
    in_tier = [url_by_name.get(name, "") in tier_urls for name in attempted]
    assert in_tier == sorted(in_tier), (
        f"fallback tier interleaved with the strategy phase: {attempted}"
    )
    # 4. the tier itself keeps its configured order
    tier_order = [b.base_url for b in schedule.fallback_backends()]
    seen_tier = [
        url_by_name[name]
        for name in attempted
        if url_by_name.get(name, "") in tier_urls
    ]
    assert seen_tier == [u for u in tier_order if u in seen_tier]


def test_max_attempts_is_the_sum_over_the_schedule() -> None:
    """[D7] The budget is arithmetic on the schedule, not a pool measurement."""
    schedule = CandidateSchedule(
        strategy_attempts=3,
        fallback_tiers=(("a", "b"), ("c",)),  # type: ignore[arg-type]
    )
    assert schedule.fallback_attempts == 3
    assert schedule.max_attempts == 6


def test_legacy_cloud_row_earns_the_attempt_it_can_use() -> None:
    """[D7] The pre-Phase-5 cap, ``max(len(pool.backends), 1)``, counted only
    pool rows — so an injected request-scoped cloud row was selectable but
    unaffordable. Same scenario as the
    ``cloud-legacy-openai-row-earns-its-attempt`` vector, asserted on the
    schedule rather than on the recorded response."""
    scenario = {
        "backends": [
            {"name": "alpha", "models": [CHAT], "script": [http(500)]},
            {
                "name": "oaicloud",
                "models": [CHAT],
                "url": OPENAI_CLOUD_BASE_URL,
                "configured": False,
                "local": False,
            },
        ],
        "routing": {"default_strategy": "failover"},
        "cloud": {"enabled": True, "fallback": "cloud"},
    }
    request_ = {
        "path": "chat_ns",
        "headers": {"authorization": "Bearer sk-caller-key"},
        "body": {"model": CHAT, "messages": USER},
    }
    with contract_environment(scenario) as env:
        schedules = _spy_schedules(env.service)
        result = env.drive(request_)
        pool_rows = len(env.service.pool.backends)
        calls = env.farm.inference_calls()

    schedule = schedules[0]
    assert [b.id for b in schedule.extra_candidates] == ["openai-cloud"]
    assert pool_rows == 1, "the cloud stand-in must not be a configured row"
    assert max(pool_rows, 1) == 1, "the old cap would have allowed one attempt"
    assert schedule.strategy_attempts == 2
    assert schedule.max_attempts == 2
    assert [c["backend"] for c in calls] == ["alpha", "oaicloud"]
    assert result.status == 200
    # The caller's own key on the wire proves this is the request-scoped
    # legacy row (F-04), not a pooled one.
    assert calls[1]["headers"]["authorization"] == "Bearer sk-caller-key"


def test_messages_fallback_tier_is_inside_the_budget() -> None:
    """[D7] The Messages tier used to be an unbounded ``for`` that ran past
    a cap it was never part of."""
    scenario = {
        "backends": [
            {"name": "alpha", "models": [CLAUDE], "script": [http(503)]},
            {
                "name": "anthro1",
                "api_format": "anthropic",
                "models": [CLAUDE],
                "script": [http(503)],
            },
            {
                "name": "anthro2",
                "api_format": "anthropic",
                "models": [CLAUDE],
                "script": [http(503)],
            },
        ],
        "routing": {"default_strategy": "failover"},
    }
    request_ = {
        "path": "messages_ns",
        "headers": {"x-api-key": "caller"},
        "body": {"model": CLAUDE, "max_tokens": 16, "messages": USER},
    }
    with contract_environment(scenario) as env:
        schedules = _spy_schedules(env.service)
        env.drive(request_)
        calls = [c["backend"] for c in env.farm.inference_calls()]

    schedule = schedules[0]
    assert schedule.strategy_attempts == 1, "one openai-format row is eligible"
    assert schedule.fallback_attempts == 2, "both anthropic rows are the tier"
    assert schedule.max_attempts == 3
    assert calls == ["alpha", "anthro1", "anthro2"]


# ---------------------------------------------------------------------------
# D8 — eligibility is schedule data, `tried` is failures only
# ---------------------------------------------------------------------------


def test_excluded_api_formats_matches_the_three_pre_seedings() -> None:
    """[D8] Chat filtered nothing; EMB and Messages filtered anthropic."""
    assert excluded_api_formats(Surface.CHAT) == frozenset()
    assert excluded_api_formats(Surface.EMBEDDINGS) == frozenset({"anthropic"})
    assert excluded_api_formats(Surface.MESSAGES) == frozenset({"anthropic"})


def test_ineligible_rows_are_not_the_tried_set() -> None:
    """[D8] The dialect filter lives in ``ineligible_ids``; ``tried`` starts
    empty on every surface, and the two only meet in ``exclusions()``."""
    scenario = {
        "backends": [
            {"name": "ant", "api_format": "anthropic", "models": [EMBED]},
            {"name": "embed1", "models": [EMBED]},
        ],
        "routing": {"default_strategy": "failover"},
    }
    with contract_environment(scenario) as env:
        schedules = _spy_schedules(env.service)
        result = env.drive({"path": "emb", "body": {"model": EMBED, "input": "hi"}})
        anthropic_ids = {
            b.id for b in env.service.pool.backends if b.api_format == "anthropic"
        }

    schedule = schedules[0]
    assert result.status == 200
    assert schedule.ineligible_ids == frozenset(anthropic_ids)
    assert schedule.exclusions(set()) == set(anthropic_ids)
    assert schedule.exclusions({"x"}) == {*anthropic_ids, "x"}


# ---------------------------------------------------------------------------
# D5 — shard context reaches selection everywhere
# ---------------------------------------------------------------------------


_SHARD_HEADERS = {"x-netllm-batch-id": "batch-7", "x-netllm-shard-index": "1"}

# Two rows per case, both eligible for the surface's strategy phase, so the
# batch ledger has a real choice to make. The Messages case uses
# openai-format rows on purpose: anthropic rows are the fallback tier, and
# the tier is ordered, not shard-placed.
_SHARD_CASES: list[tuple[str, dict[str, Any], dict[str, str], str]] = [
    ("emb", {"model": EMBED, "input": "hi"}, {}, "openai"),
    (
        "messages_ns",
        {"model": CLAUDE, "max_tokens": 16, "messages": USER},
        {"x-api-key": "caller"},
        "openai",
    ),
    (
        "messages_s",
        {"model": CLAUDE, "max_tokens": 16, "messages": USER},
        {"x-api-key": "caller"},
        "openai",
    ),
]


@pytest.mark.parametrize(
    ("path", "body", "headers", "api_format"),
    _SHARD_CASES,
    ids=[case[0] for case in _SHARD_CASES],
)
def test_shard_context_reaches_selection(
    path: str, body: dict[str, Any], headers: dict[str, str], api_format: str
) -> None:
    """[D5] A shard-bearing request no longer degrades to the shardless
    round_robin fallback on EMB / M-NS / M-S.

    Two independent signals: ``_shardless_fallbacks`` does not tick (the
    request was recognised as sharded at all), and the batch ledger both
    placed the shard and closed it out (``mark_done``) — the half that keeps
    a completed shard from being re-dispatched later in the same batch.
    """
    model = body["model"]
    scenario = {
        "backends": [
            {"name": "one", "api_format": api_format, "models": [model]},
            {"name": "two", "api_format": api_format, "models": [model]},
        ],
        "routing": {"default_strategy": "batch_shard"},
    }
    request_ = {"path": path, "headers": {**headers, **_SHARD_HEADERS}, "body": body}
    with contract_environment(scenario) as env:
        before = env.service._shardless_fallbacks
        result = env.drive(request_)
        ledger = env.service._batch_ledger
        assert result.status == 200
        assert env.service._shardless_fallbacks == before
        assert ledger.assignments.get(("batch-7", 1))
        assert ("batch-7", 1) in ledger.completed


def test_batch_ledger_never_assigns_an_ineligible_backend() -> None:
    """[D5/D8] The batch-ledger arm of ``_select_backend_for_request`` was the
    one selection route that ignored ``exclude_ids``. Harmless while only the
    chat paths carried a shard; with D5 it could hand an anthropic row to
    /v1/embeddings, which is exactly what the dialect filter forbids."""
    scenario = {
        "backends": [
            {"name": "ant", "api_format": "anthropic", "models": [EMBED]},
            {"name": "embed1", "models": [EMBED]},
        ],
        "routing": {"default_strategy": "batch_shard"},
    }
    calls: list[str] = []
    # Every shard index in turn: the ledger hashes the index onto the
    # candidate list, so a single index could pass by luck.
    with contract_environment(scenario) as env:
        for index in range(6):
            result = env.drive(
                {
                    "path": "emb",
                    "headers": {
                        "x-netllm-batch-id": f"batch-{index}",
                        "x-netllm-shard-index": str(index),
                    },
                    "body": {"model": EMBED, "input": "hi"},
                }
            )
            assert result.status == 200
        calls = [c["backend"] for c in env.farm.inference_calls()]
    assert set(calls) == {"embed1"}, f"anthropic row served embeddings: {calls}"


# ---------------------------------------------------------------------------
# D13 — peer loop-guard headers on the anthropic-format arm
# ---------------------------------------------------------------------------


def _anthropic_peer_row(base_url: str) -> Backend:
    """A ``peer:`` row that happens to speak the Anthropic dialect.

    Nothing constructs one today (peers are discovered as openai-format
    agents), which is why D13 was latent: the anthropic invoker simply never
    attached the loop guard.
    """
    return Backend(
        id="peer:agent-9",
        base_url=base_url,
        provider="custom",
        api_format="anthropic",
        api_key=FARM_API_KEY,
        local=False,
        agent_id="agent-9",
        health=BackendHealth(models=[CLAUDE], model_count=1, status="online"),
    )


_PEER_INCOMING = {"x-netllm-hops": "1", "x-api-key": "caller"}


def test_anthropic_peer_arm_sends_loop_guard_headers() -> None:
    """[D13] Non-stream anthropic arm — asserted on the wire, not on a dict."""
    scenario = {
        "backends": [{"name": "ant", "api_format": "anthropic", "models": [CLAUDE]}]
    }
    with contract_environment(scenario) as env:
        peer = _anthropic_peer_row("http://ant.farm")
        result = asyncio.run(
            env.service._messages_on_backend(
                peer,
                {"model": CLAUDE, "max_tokens": 16, "messages": USER},
                CLAUDE,
                _PEER_INCOMING,
                "caller",
            )
        )
        assert result["model"] == CLAUDE
        sent = env.farm.requests[-1].headers

    assert sent["x-netllm-local-only"] == "1"
    assert sent["x-netllm-hops"] == "2", "hop counter must advance past the peer"


def test_anthropic_peer_stream_arm_sends_loop_guard_headers() -> None:
    """[D13] Stream anthropic arm — the same guard, same assertion."""
    scenario = {
        "backends": [{"name": "ant", "api_format": "anthropic", "models": [CLAUDE]}]
    }

    with contract_environment(scenario) as env:
        peer = _anthropic_peer_row("http://ant.farm")

        async def _drain() -> list[str]:
            payload = {
                "model": CLAUDE,
                "max_tokens": 16,
                "messages": USER,
                "stream": True,
            }
            return [
                chunk
                async for chunk in env.service._messages_stream_on_backend(
                    peer, payload, CLAUDE, _PEER_INCOMING, "caller"
                )
            ]

        frames = asyncio.run(_drain())
        assert frames, "the peer must have streamed something"
        sent = env.farm.requests[-1].headers

    assert sent["x-netllm-local-only"] == "1"
    assert sent["x-netllm-hops"] == "2"


def test_non_peer_anthropic_row_gets_no_loop_guard_headers() -> None:
    """[D13] The guard is peer-scoped: a plain anthropic row is untouched,
    and the caller's ``anthropic-version`` pass-through still works."""
    scenario = {
        "backends": [{"name": "ant", "api_format": "anthropic", "models": [CLAUDE]}]
    }
    with contract_environment(scenario) as env:
        row = env.service.pool.backends[0]
        assert not row.id.startswith("peer:")
        headers = env.service._anthropic_upstream_headers(
            row, {"anthropic-version": "2023-06-01", "x-netllm-hops": "1"}
        )
    assert headers == {"anthropic-version": "2023-06-01"}


# ---------------------------------------------------------------------------
# Capacity storm — bounded attempts, no offline trips
# ---------------------------------------------------------------------------


def _drive_many(env: Any, request_: dict[str, Any], n: int) -> list[int]:
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(env.drive, request_) for _ in range(n)]
        return [f.result().status for f in futures]


def test_capacity_storm_stays_bounded_and_keeps_backends_online() -> None:
    """A storm of capacity rejections must spend the budget and nothing more,
    and must never bench a backend.

    ``pool.mark_backend_failure(capacity=True)`` counts the rejection and
    returns *before* the failure ledger — a full backend can take the very
    next request. With three backends all answering 429 and a
    ``max_backend_failures`` of 1, a single mis-classified rejection would
    trip all three offline immediately, which is why the threshold is
    lowered here rather than left at the default.
    """
    concurrency = 12
    scenario = {
        "backends": [
            {"name": "alpha", "models": [CHAT], "script": [http(429)]},
            {"name": "beta", "models": [CHAT], "script": [http(429)]},
            {"name": "gamma", "models": [CHAT], "script": [http(429)]},
        ],
        "routing": {"default_strategy": "least_load", "max_backend_failures": 1},
    }
    request_ = {"path": "chat_ns", "body": {"model": CHAT, "messages": USER}}

    with contract_environment(scenario) as env:
        statuses = _drive_many(env, request_, concurrency)
        pool = env.service.pool
        calls = env.farm.inference_calls()
        rejections = dict(pool.capacity_rejections)
        health = {b.base_url: b.health.status for b in pool.backends}
        in_flight = {b.base_url: b.in_flight for b in pool.backends}
        source_in_flight = dict(env.service._source_in_flight)

    # Every request exhausted its schedule: 3 pool rows, no extras, no tier.
    # The route maps a non-400/404 OpenAIUpstreamError to 502; the 429 is the
    # upstream's, and it is the *classification* that matters here.
    assert statuses == [502] * concurrency, statuses
    assert len(calls) == concurrency * 3, (
        f"{len(calls)} upstream calls for {concurrency} requests with a "
        "3-attempt budget"
    )
    # Capacity, not failure: counted per backend, nothing benched.
    assert sum(rejections.values()) == concurrency * 3
    assert all(status != "offline" for status in health.values()), health
    # No leaked reservations on either ledger.
    assert all(v == 0 for v in in_flight.values()), in_flight
    assert all(v == 0 for v in source_in_flight.values()), source_in_flight


def test_capacity_storm_leaves_the_pool_usable() -> None:
    """The end-to-end form of "never trips offline": after the storm, the
    same backends serve normally with no cooldown."""
    scenario = {
        "backends": [
            {"name": "alpha", "models": [CHAT], "script": [http(429)]},
            {"name": "beta", "models": [CHAT], "script": [http(429)]},
        ],
        "routing": {"default_strategy": "least_load", "max_backend_failures": 1},
    }
    request_ = {"path": "chat_ns", "body": {"model": CHAT, "messages": USER}}
    with contract_environment(scenario) as env:
        _drive_many(env, request_, 8)
        for backend in env.farm.backends:
            backend.script = [ok()]
            backend._cursor = 0
        after = env.drive(request_)
    assert after.status == 200, json.dumps(after.body)[:300]
