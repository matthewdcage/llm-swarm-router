"""Counter-parity guard (plan-f24-f26.md §4.3) + the D2 flip's pins.

The F-24 defect generator is "the fix lands on one loop only": five proxy
paths each inlined their own success block and their own failure block, so
a change to accounting reached whichever loop the author happened to be
reading. Phase 2 routes every path through a single ``AttemptRecorder``.
This module is the standing refutation attempt against a relapse: one
identical scripted scenario is driven through every applicable path and the
pool / metrics / admission deltas must come out field-identical. Only the
wire dialect may differ.

Where a path legitimately cannot produce an identical delta the difference
is enumerated here with its reason, so a *new* asymmetry can never hide
behind a vague allowance:

* ``emb`` records no completion tokens — an embeddings response body has no
  completion side (``usage`` is ``{prompt_tokens, total_tokens}``).
* ``messages_s`` records no prompt tokens — the OpenAI->Anthropic stream
  bridge does not carry ``input_tokens`` into the synthesized
  ``message_start`` frame. A wire-content gap, not an accounting gap.
* ``responses_s`` records no success accounting at all — behavior-matrix
  D16, pinned separately below and owed a fix by a later phase.

The failure side has no exceptions at all: every one of the seven paths
must produce byte-identical accounting when the whole pool fails.
"""

from __future__ import annotations

import re
from typing import Any
from unittest import mock

import pytest
from drivers import PATHS
from farm import http, ok
from netllm_sdk_openai.client import OpenAIUpstreamError

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

_ALPHA = "http://alpha.farm/v1"
_BETA = "http://beta.farm/v1"

# Accounting-shape metrics: what the recorder writes, independent of how
# many tokens the wire happened to carry.
_ACCOUNTING_PREFIXES = (
    "netllm_requests_total",
    "netllm_request_latency_seconds_count",
    "netllm_scenario_requests_total",
    "netllm_source_requests_total",
)
_TOKEN_PREFIXES = ("netllm_prompt_tokens_total", "netllm_completion_tokens_total")

# [D16] RESOLVED in Phase 7. The Responses bridge still breaks out of the
# chat stream at data:[DONE] — that is the bridge's business — but the
# accounting no longer sits behind that chunk: engine.StreamSession settles
# the attempt one chunk ahead, so abandoning the generator cannot strand it.
# RESP-S therefore joins the success-parity comparison; the set is empty and
# kept only so a future exclusion has to be spelled out rather than smuggled
# into the loop.
_D16_PATHS: frozenset[str] = frozenset()


def _scenario(alpha: list[dict], beta: list[dict]) -> dict[str, Any]:
    return {
        "backends": [
            {"name": "alpha", "models": ["farm-chat", "farm-embed"], "script": alpha},
            {"name": "beta", "models": ["farm-chat", "farm-embed"], "script": beta},
        ],
        "routing": {"default_strategy": "failover"},
    }


def _subset(metrics: dict[str, float], prefixes: tuple[str, ...]) -> dict[str, float]:
    return {k: v for k, v in metrics.items() if k.startswith(prefixes)}


# [D4] Since the embeddings surface got its capability guard, no single model
# name can drive all seven paths: /v1/embeddings now demands an
# embedding-classified name and the chat/messages guards demand a
# chat-classified one. The `model=` label is a *content* property, not an
# accounting-shape property, so cross-path comparisons blank it out. Anything
# else in the key — backend, status, metric family — still has to match
# exactly.
_MODEL_LABEL = re.compile(r"model=[^,}]*")


def _blank_model_label(metrics: dict[str, float]) -> dict[str, float]:
    return {_MODEL_LABEL.sub("model=*", k): v for k, v in metrics.items()}


def _model_for(path: str) -> str:
    return _BODY[path]["model"]


def _drive_all(contract_env, alpha: list[dict], beta: list[dict]) -> dict[str, Any]:
    """One identical scripted scenario through every path; deltas by path."""
    out: dict[str, Any] = {}
    for path in PATHS:
        env = contract_env(_scenario(alpha, beta))
        result = env.drive({"path": path, "body": _BODY[path]})
        out[path] = {
            "status": result.status,
            "calls": [c["backend"] for c in env.farm.inference_calls()],
            "delta": env.probe.delta(),
        }
    return out


# -- success side: fail alpha once, succeed on beta -------------------------


def test_success_accounting_is_field_identical_across_paths(contract_env) -> None:
    runs = _drive_all(contract_env, [http(500)], [ok()])
    reference = runs["chat_ns"]

    for path, run in runs.items():
        if path in _D16_PATHS:
            continue
        assert run["status"] == 200, path
        assert run["calls"] == ["alpha", "beta"], path
        for section in ("pool", "gauges", "admission"):
            assert run["delta"][section] == reference["delta"][section], (
                f"{path}: {section} delta diverges from chat_ns — accounting "
                "must not depend on which proxy loop served the request"
            )
        assert _blank_model_label(
            _subset(run["delta"]["metrics"], _ACCOUNTING_PREFIXES)
        ) == _blank_model_label(
            _subset(reference["delta"]["metrics"], _ACCOUNTING_PREFIXES)
        ), f"{path}: accounting metrics diverge from chat_ns"


def test_token_counter_differences_are_wire_content_only(contract_env) -> None:
    """Token deltas may differ only where the wire body genuinely lacks the
    number — enumerated, never open-ended."""
    runs = _drive_all(contract_env, [http(500)], [ok()])
    prompt_key = f"netllm_prompt_tokens_total{{backend={_BETA},model=farm-chat}}"
    completion_key = (
        f"netllm_completion_tokens_total{{backend={_BETA},model=farm-chat}}"
    )
    emb_prompt_key = (
        f"netllm_prompt_tokens_total{{backend={_BETA},model={_model_for('emb')}}}"
    )
    expected = {
        "chat_ns": {prompt_key: 3.0, completion_key: 5.0},
        "chat_s": {prompt_key: 3.0, completion_key: 5.0},
        "responses_ns": {prompt_key: 3.0, completion_key: 5.0},
        "messages_ns": {prompt_key: 3.0, completion_key: 5.0},
        # An embeddings body has no completion side at all. [D4] It also
        # carries its own model label now — see _blank_model_label.
        "emb": {emb_prompt_key: 3.0},
        # The OpenAI->Anthropic stream bridge drops input_tokens.
        "messages_s": {completion_key: 5.0},
        # [D16] Phase 7: RESP-S records exactly what C-S records.
        "responses_s": {prompt_key: 3.0, completion_key: 5.0},
    }
    for path, run in runs.items():
        assert _subset(run["delta"]["metrics"], _TOKEN_PREFIXES) == expected[path], (
            f"{path}: token counters changed — if this is a real wire-content "
            "change update the enumeration; if it is an accounting change it "
            "is a parity regression"
        )


def test_responses_stream_success_accounting_matches_chat_stream(
    contract_env,
) -> None:
    """[D16] The former hole, inverted into a pin on the fix.

    RESP-S is C-S plus an edge translation, so its accounting must be C-S's
    exactly — same routed_counts shape, same request_count, same tokens,
    same ok-counter. This used to be the one path where all of that was
    empty on a successful request."""
    runs = _drive_all(contract_env, [http(500)], [ok()])
    resp, chat = runs["responses_s"]["delta"], runs["chat_s"]["delta"]
    assert runs["responses_s"]["status"] == 200
    assert resp["pool"]["routed_counts"] == chat["pool"]["routed_counts"] != {}
    assert resp["admission"]["request_count"] == chat["admission"]["request_count"] == 1
    assert _subset(resp["metrics"], _TOKEN_PREFIXES) == _subset(
        chat["metrics"], _TOKEN_PREFIXES
    )
    assert [k for k in resp["metrics"] if "status=ok" in k] == [
        k for k in chat["metrics"] if "status=ok" in k
    ]
    # The failure half was never the broken one, and still is not.
    assert (
        resp["metrics"][
            f"netllm_requests_total{{backend={_ALPHA},model=farm-chat,status=error}}"
        ]
        == 1.0
    )


# -- failure side: whole pool fails; no path may differ at all --------------


def test_failure_accounting_is_field_identical_across_all_paths(
    contract_env,
) -> None:
    runs = _drive_all(contract_env, [http(500)], [http(500)])
    reference = runs["chat_ns"]
    for path, run in runs.items():
        assert run["calls"] == ["alpha", "beta"], path
        run_delta = {**run["delta"]}
        ref_delta = {**reference["delta"]}
        run_delta["metrics"] = _blank_model_label(run_delta["metrics"])
        ref_delta["metrics"] = _blank_model_label(ref_delta["metrics"])
        assert run_delta == ref_delta, (
            f"{path}: failure accounting diverges from chat_ns — every path "
            "must count exactly one error per failed attempt"
        )


# -- D2: chat-stream pre-first-byte failure, counted exactly once -----------


def _error_counts(delta: dict[str, Any]) -> dict[str, float]:
    return {k: v for k, v in delta["metrics"].items() if "status=error" in k}


def test_chat_stream_pre_first_byte_failure_counted_exactly_once(
    contract_env,
) -> None:
    """The stream wrapper records the failure and re-raises; the failover
    loop now records too. The recorder's dedup guard must keep the total at
    one — not two."""
    env = contract_env(_scenario([http(500)], [ok()]))
    result = env.drive({"path": "chat_s", "body": _BODY["chat_s"]})
    assert result.status == 200
    delta = env.probe.delta()
    assert _error_counts(delta) == {
        f"netllm_requests_total{{backend={_ALPHA},model=farm-chat,status=error}}": 1.0
    }
    assert _alpha_failures(env) == 1


def _alpha(env: Any) -> Any:
    for backend in env.service.pool.backends:
        if backend.base_url == _ALPHA:
            return backend
    raise AssertionError("alpha not in pool")


def _alpha_failures(env: Any) -> int:
    pool = env.service.pool
    entry = pool._health_cache.get(_alpha(env).cache_key())
    return entry.failures if entry is not None else 0


def _fail_alpha_before_the_wrapper(env: Any, exc: Exception):
    """Make the *upstream client construction* fail for alpha.

    That call sits inside the failover loop's try but outside
    ``_stream_with_metrics``' own try, which is exactly the region whose
    failures used to be counted nowhere (D2).
    """
    real = env.service._openai_upstream

    def fake(backend: Any, headers: Any = None, **kw: Any) -> Any:
        if backend.base_url == _ALPHA:
            raise exc
        return real(backend, headers, **kw)

    return mock.patch.object(env.service, "_openai_upstream", side_effect=fake)


def test_chat_stream_failure_outside_the_wrapper_is_now_counted(
    contract_env,
) -> None:
    """[D2] Pre-first-byte failures raised before the stream wrapper runs
    used to record neither a pool failure nor an error counter."""
    env = contract_env(_scenario([ok()], [ok()]))
    exc = OpenAIUpstreamError("cannot build upstream client", status_code=500)
    with _fail_alpha_before_the_wrapper(env, exc):
        result = env.drive({"path": "chat_s", "body": _BODY["chat_s"]})
    assert result.status == 200  # beta served it
    delta = env.probe.delta()
    assert _error_counts(delta) == {
        f"netllm_requests_total{{backend={_ALPHA},model=farm-chat,status=error}}": 1.0
    }
    assert _alpha_failures(env) == 1


def test_chat_stream_capacity_failure_outside_the_wrapper_is_classified(
    contract_env,
) -> None:
    """[D2] ...and it goes through capacity classification, so a 429 raised
    there steers away for this request without tripping the backend
    offline — the same treatment every non-stream path already gave it."""
    env = contract_env(_scenario([ok()], [ok()]))
    exc = OpenAIUpstreamError("busy", status_code=429)
    with _fail_alpha_before_the_wrapper(env, exc):
        result = env.drive({"path": "chat_s", "body": _BODY["chat_s"]})
    assert result.status == 200
    delta = env.probe.delta()
    assert delta["pool"]["capacity_rejections"] == {_alpha(env).id: 1}
    assert delta["pool"]["health_status"][_ALPHA] == "online"
    assert _alpha_failures(env) == 0


@pytest.mark.parametrize("path", sorted(set(PATHS)))
def test_no_path_double_counts_a_failed_attempt(contract_env, path: str) -> None:
    """One failed attempt, one error counter, one pool failure — on every
    path. The dedup guard must not turn into over- or under-counting."""
    env = contract_env(_scenario([http(500)], [ok()]))
    env.drive({"path": path, "body": _BODY[path]})
    delta = env.probe.delta()
    assert _error_counts(delta) == {
        f"netllm_requests_total{{backend={_ALPHA},"
        f"model={_model_for(path)},status=error}}": 1.0
    }
    assert _alpha_failures(env) == 1
