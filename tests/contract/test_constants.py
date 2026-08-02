"""Characterization tests — one per behavior-matrix "constant across all
paths" item (behavior-matrix.md, list after the divergence table), so no
later refactor phase can silently drop one:

1. attribution + scenario counted once per request (not per attempt)
2. admit-before-first-await + finally-release (per-source cap)
3. capacity classification: statuses {409, 429, 503, 507} + body markers
4. _offload_if_probing selection (thread offload only when probes possible)
5. yielded_any no-replay guard on both stream dialects
6. pin / strategy / local-only header plumbing
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest import mock

import pytest
from drivers import PATHS, scanned_backend_id
from farm import http, midstream_drop, ok
from netllm_core.pool import is_capacity_error

# [D4] /v1/embeddings gained a capability guard in Phase 4c, so the emb path
# can no longer share the chat paths' model name: every farm backend serves
# both and each surface asks for the one it is allowed to.
_MODELS = ["farm-chat", "farm-embed"]

_BODY: dict[str, dict[str, Any]] = {
    "chat_ns": {"model": "farm-chat", "messages": [{"role": "user", "content": "hi"}]},
    "chat_s": {"model": "farm-chat", "messages": [{"role": "user", "content": "hi"}]},
    "emb": {"model": "farm-embed", "input": "hi"},
    "messages_ns": {
        "model": "farm-chat",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    },
    "messages_s": {
        "model": "farm-chat",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    },
    "responses_ns": {"model": "farm-chat", "input": "hi"},
    "responses_s": {"model": "farm-chat", "input": "hi"},
}


def _two_backend_scenario(
    alpha_script: list[dict], beta_script: list[dict]
) -> dict[str, Any]:
    return {
        "backends": [
            {"name": "alpha", "models": _MODELS, "script": alpha_script},
            {"name": "beta", "models": _MODELS, "script": beta_script},
        ],
        "routing": {"default_strategy": "failover"},
    }


# 1 -- attribution/scenario counted once per request ------------------------


@pytest.mark.parametrize("path", PATHS)
def test_attribution_and_scenario_counted_once_even_with_retries(
    contract_env, path: str
) -> None:
    env = contract_env(_two_backend_scenario([http(500)], [ok()]))
    result = env.drive({"path": path, "body": _BODY[path]})
    assert result.status == 200
    # Two attempts reached the farm (alpha failed, beta served) ...
    assert [c["backend"] for c in env.farm.inference_calls()] == ["alpha", "beta"]
    delta = env.probe.delta()
    # ... but source and scenario were each counted exactly once.
    assert delta["admission"]["source_counts"] == {"default": 1}
    assert delta["admission"]["scenario_counts"] == {"default:default": 1}


# 2 -- admit-before-first-await + finally-release ---------------------------


@pytest.mark.parametrize("path", PATHS)
def test_source_admission_reserved_then_released_in_finally(
    contract_env, path: str
) -> None:
    scenario = _two_backend_scenario([ok()], [ok()])
    scenario["routing"]["sources"] = [{"id": "cli", "max_concurrency": 1}]
    env = contract_env(scenario)
    headers = {"x-netllm-source": "cli"}

    # Saturated source rejects before any await / upstream traffic ...
    env.service._source_in_flight["cli"] = 1
    rejected = env.drive({"path": path, "body": _BODY[path], "headers": headers})
    assert rejected.status == 429
    assert env.farm.inference_calls() == []
    env.service._source_in_flight["cli"] = 0

    # ... a successful request releases in finally ...
    okr = env.drive({"path": path, "body": _BODY[path], "headers": headers})
    assert okr.status == 200
    assert env.service._source_in_flight.get("cli", 0) == 0

    # ... and so does an exhausted-failure request.
    env2 = contract_env(
        {
            "backends": [{"name": "gamma", "models": _MODELS, "script": [http(500)]}],
            "routing": {
                "default_strategy": "failover",
                "sources": [{"id": "cli", "max_concurrency": 1}],
            },
        }
    )
    failed = env2.drive({"path": path, "body": _BODY[path], "headers": headers})
    assert failed.status >= 400
    assert env2.service._source_in_flight.get("cli", 0) == 0


# 3 -- capacity classification ----------------------------------------------


def test_capacity_status_set_and_markers_frozen() -> None:
    for status in (409, 429, 503, 507):
        assert is_capacity_error(status, "")
    for status in (400, 401, 404, 500, 502):
        assert not is_capacity_error(status, "irrelevant")
    for marker in (
        "prefill_memory_exceeded",
        "memory pressure",
        "is busy",
        "rate limit",
    ):
        assert is_capacity_error(502, f"upstream said: {marker}")
    assert not is_capacity_error(None, "some ordinary failure")


def test_capacity_429_steers_away_without_offline_trip(contract_env) -> None:
    env = contract_env(_two_backend_scenario([http(429)], [ok()]))
    result = env.drive({"path": "chat_ns", "body": _BODY["chat_ns"]})
    assert result.status == 200
    delta = env.probe.delta()
    alpha_url = "http://alpha.farm/v1"
    # Scan-discovered rows carry uuid5(base_url) ids (netllm_discovery).
    assert delta["pool"]["capacity_rejections"] == {scanned_backend_id(alpha_url): 1}
    # Capacity rejections never trip the backend offline.
    assert delta["pool"]["health_status"][alpha_url] == "online"
    assert [c["backend"] for c in env.farm.inference_calls()] == ["alpha", "beta"]


# 4 -- _offload_if_probing --------------------------------------------------


def test_offload_if_probing_threads_only_when_health_stale(contract_env) -> None:
    env = contract_env(_two_backend_scenario([ok()], [ok()]))
    service = env.service
    main = threading.current_thread()

    def where() -> threading.Thread:
        return threading.current_thread()

    with mock.patch.object(service.pool, "any_health_stale", return_value=True):
        stale_thread = asyncio.run(service._offload_if_probing(where))
    with mock.patch.object(service.pool, "any_health_stale", return_value=False):
        fresh_thread = asyncio.run(service._offload_if_probing(where))
    assert stale_thread is not main  # probe possible -> worker thread
    assert fresh_thread is main  # fresh caches -> stays on the loop


# 5 -- yielded_any no-replay guard ------------------------------------------


def test_chat_stream_midstream_drop_emits_error_frame_and_never_retries(
    contract_env,
) -> None:
    env = contract_env(_two_backend_scenario([midstream_drop(3)], [ok()]))
    result = env.drive({"path": "chat_s", "body": _BODY["chat_s"]})
    assert result.status == 200  # bytes were already on the wire
    frames = result.frames or []
    assert any('"error"' in f for f in frames)
    assert frames[-1] == "data: [DONE]"
    # No second backend ever received bytes after the first yielded byte.
    assert [c["backend"] for c in env.farm.inference_calls()] == ["alpha"]


def test_messages_stream_midstream_drop_emits_error_event_and_never_retries(
    contract_env,
) -> None:
    env = contract_env(
        {
            "backends": [
                {
                    "name": "anthro1",
                    "api_format": "anthropic",
                    "models": ["claude-farm"],
                    "script": [midstream_drop(3)],
                },
                {
                    "name": "anthro2",
                    "api_format": "anthropic",
                    "models": ["claude-farm"],
                    "script": [ok()],
                },
            ]
        }
    )
    body = dict(_BODY["messages_s"], model="claude-farm")
    result = env.drive({"path": "messages_s", "body": body})
    assert result.status == 200
    frames = result.frames or []
    assert any(f.startswith("event: error") for f in frames)
    assert [c["backend"] for c in env.farm.inference_calls()] == ["anthro1"]


# 6 -- pin / strategy / local-only header plumbing --------------------------


@pytest.mark.parametrize("path", PATHS)
def test_backend_pin_header_routes_to_pinned_backend(contract_env, path: str) -> None:
    env = contract_env(_two_backend_scenario([ok()], [ok()]))
    result = env.drive(
        {
            "path": path,
            "body": _BODY[path],
            "headers": {"x-netllm-backend": "http://beta.farm/v1"},
        }
    )
    assert result.status == 200
    assert [c["backend"] for c in env.farm.inference_calls()] == ["beta"]


@pytest.mark.parametrize("path", PATHS)
def test_local_only_header_excludes_remote_backends(contract_env, path: str) -> None:
    # scan_visible=False: a scan-discovered row is always local=True and
    # wins the pool merge, so the only way a routing.backends override's
    # `local: false` flag reaches the pool is when the scan cannot see the
    # host (the row then enters via the override append branch, catalog
    # blind — characterized HEAD behavior).
    env = contract_env(
        {
            "backends": [
                {
                    "name": "remote1",
                    "models": _MODELS,
                    "script": [ok()],
                    "local": False,
                    "scan_visible": False,
                }
            ],
            "routing": {"default_strategy": "failover"},
        }
    )
    blocked = env.drive(
        {
            "path": path,
            "body": _BODY[path],
            "headers": {"x-netllm-local-only": "1"},
        }
    )
    assert blocked.status >= 400
    assert env.farm.inference_calls() == []
    served = env.drive({"path": path, "body": _BODY[path]})
    assert served.status == 200
    assert [c["backend"] for c in env.farm.inference_calls()] == ["remote1"]


def test_strategy_header_is_honored(contract_env) -> None:
    env = contract_env(_two_backend_scenario([ok()], [ok()]))
    for _ in range(2):
        result = env.drive(
            {
                "path": "chat_ns",
                "body": _BODY["chat_ns"],
                "headers": {"x-netllm-strategy": "round_robin"},
            }
        )
        assert result.status == 200
    # round_robin alternates; failover (the configured default) would not.
    assert [c["backend"] for c in env.farm.inference_calls()] == ["alpha", "beta"]
