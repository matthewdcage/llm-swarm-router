"""Phase 0b contract vectors: streaming lifecycle + error surfaces.

Scenario definitions for the vectors under
``tests/contract/vectors/streaming-errors/<group>/*.json``. Every document
is authored here in Python and materialized to JSON with the recorded
``expected`` block; the checked-in JSON is the contract, this module is how
it was produced.

Two groups (plan-f24-f26.md §2, Phase 0b "streaming" + the F-38 half of
"guards & policy"):

- ``streaming`` — mid-stream drop after the first byte (dialect-native
  error frame, no retry), pre-first-byte failover (invisible to the
  client), usage-chunk parse into telemetry (present and absent),
  client disconnect (release + in-flight balance under ``GeneratorExit``),
  and SSE frame-boundary splits that cut wire frames mid-line.
- ``errors`` — the per-surface error envelope post-F-38 (OpenAI shape on
  ``/v1/chat/completions`` + ``/v1/embeddings`` + ``/v1/responses``,
  Anthropic shape on ``/v1/messages``, FastAPI ``{"detail"}`` everywhere
  else), the pre-stream 429/502 that F-32 revived, and the capacity status
  set {409, 429, 503, 507} plus the body-marker arm.

Divergence annotations
----------------------
``divergence`` carries behavior-matrix.md IDs (D1–D16) for the cell a
vector pins. Used here:

- **D9** — M-S mid-stream error frame has no terminator event, and the
  translated M-S arm emits the rewritten model. Plan Phase 7 flips both.
- **D11** — Messages exhaustion is a keyless 401 where the OpenAI surfaces
  404, and an ``OpenAIUpstreamError`` from a translated backend is
  flattened to 502 instead of forwarding its status.
- **D6 / D8** — where the recorded candidate order is produced by the
  Messages anthropic fallback tier and its ``tried`` pre-seeding.
- **D16** — RESP-S records no success accounting: the Responses bridge
  breaks out of the chat stream at ``data: [DONE]``, so
  ``_stream_with_metrics``' post-loop ``mark_success`` / ok-REQUESTS_TOTAL
  / ``_request_count`` never run. Carried by the five ``*-responses-s``
  200-vectors, which is why they record ``routed_counts={}`` and
  ``request_count=0``.

Vectors for behavior the F-30..F-48 remediation already fixed carry no
annotation — they pin the fixed behavior and must not move again. That
covers most of this slice: F-32 (a pre-stream 429/502 is a real HTTP
status, not 200 + aborted body), F-33 (both stream wrappers record full
success accounting and parse the usage chunk), and F-38 (dialect-shaped
error envelopes). D2 is deliberately absent: post-remediation every C-S
upstream error passes through ``_stream_with_metrics`` and M-S increments
the error counter inline, so the "failure accounting missing on the stream
paths" divergence is no longer observable from the outside.

Findings with no D-number are carried in the non-schema ``note`` field
rather than mis-annotated. The sharpest one is
``stream-midstream-drop-responses-s``: the Responses translator swallows
the OpenAI error chunk and still emits ``response.completed``, so a
mid-stream upstream failure is invisible on that surface. (Its *accounting*
half now has a number — D16, above.)

Anti-collapse
-------------
Every vector here runs through ``anticollapse.assert_no_unintended_keyless_
401`` at record time and ``assert_corpus_has_no_unintended_keyless_401`` at
replay time. Three vectors in this module used to stand up an openai-format
farm serving ``CHAT_MODEL`` while asking for ``MSG_MODEL`` with no alias
between them, so they silently recorded a keyless 401 with zero upstream
calls instead of the D9/D11 behavior their notes claimed; ``_TRANSLATED_ARM``
supplies the missing alias and the guard makes the mistake unrepeatable.

Harness gaps worked around locally (additive, in this module only — see
the Phase 0b report):

1. ``TestClient`` buffers a streaming response to completion before the
   caller sees a byte, so a client disconnect cannot be expressed through
   ``drivers.drive``. The ``drive.mode = "service_stream_aclose"`` vectors
   drive the service's async generator directly and ``aclose()`` it after
   N chunks — the closest reachable analogue of Starlette dropping the
   client mid-body.
2. ``FakeFarm`` emits one whole SSE wire frame per byte-stream chunk, so
   frame-boundary splitting is unreachable from a behavior script. The
   ``wire.split_bytes`` key repacks the farm's frames for one environment
   by wrapping the *instance's* ``_stream_frames`` — no harness edit.
3. ``drivers.PATHS`` covers the seven inference paths only, so the
   ``/netllm/*`` half of the envelope contract needs the ``drive.mode =
   "raw"`` escape hatch.
4. ``ContractVector`` has no pre-state hook, so a per-source
   ``max_concurrency`` rejection is unreachable from one sequential
   ``drive()``; ``pre_state`` (same shape as
   ``scenarios_routing_failover``) seeds it.

Running
-------
``pytest`` does not auto-collect this module (its name does not match
``python_files``) and the Phase 0a runner globs ``vectors/*.json``
non-recursively, so pass the path explicitly and on its own::

    uv run pytest tests/contract/scenarios_streaming_errors.py
    NETLLM_VECTOR_RECORD=1 uv run pytest \\
        tests/contract/scenarios_streaming_errors.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from anticollapse import (
    assert_corpus_has_no_unintended_keyless_401,
    assert_no_unintended_keyless_401,
)
from canonical import canonicalize_result
from drivers import DriveResult, contract_environment

GROUP_DIR = Path(__file__).resolve().parent / "vectors" / "streaming-errors"
RECORD = os.environ.get("NETLLM_VECTOR_RECORD") == "1"

VALID_DIVERGENCE_IDS = {f"D{i}" for i in range(1, 17)}
GROUPS = ("streaming", "errors")
_ID_PREFIX = {"streaming": "stream-", "errors": "errors-"}

CHAT_MODEL = "farm-chat"
EMB_MODEL = "farm-embed"
MSG_MODEL = "claude-farm"

USER = [{"role": "user", "content": "hi"}]

OK = {"behavior": "ok"}
FAIL_500 = {"behavior": "http", "status": 500}

# Service async-generator entry points for the disconnect vectors.
_STREAM_METHOD = {
    "chat_s": "proxy_chat_completion_stream",
    "messages_s": "proxy_messages_stream",
    "responses_s": "proxy_responses_stream",
}

# A source with a concurrency ceiling of one, addressed by x-netllm-source.
CAPPED_SOURCE = {"sources": [{"id": "cli", "enabled": True, "max_concurrency": 1}]}


# --------------------------------------------------------------------------
# Scenario/request builders
# --------------------------------------------------------------------------


def oai(name: str, script: list[dict[str, Any]] | None = None, **kw: Any) -> dict:
    return {
        "name": name,
        "api_format": "openai",
        "models": [CHAT_MODEL],
        "script": script or [OK],
        **kw,
    }


def emb(name: str, script: list[dict[str, Any]] | None = None) -> dict:
    return {
        "name": name,
        "api_format": "openai",
        "models": [EMB_MODEL],
        "script": script or [OK],
    }


def ant(name: str, script: list[dict[str, Any]] | None = None) -> dict:
    return {
        "name": name,
        "api_format": "anthropic",
        "models": [MSG_MODEL],
        "script": script or [OK],
    }


def stream_ok(
    chunks: list[str] | None = None, *, usage_in_final: bool = True
) -> dict[str, Any]:
    out: dict[str, Any] = {"behavior": "ok_stream", "usage_in_final": usage_in_final}
    if chunks is not None:
        out["chunks"] = chunks
    return out


def drop(after_n: int) -> dict[str, Any]:
    return {"behavior": "midstream_drop", "after_n": after_n}


def chat_body(model: str = CHAT_MODEL) -> dict[str, Any]:
    return {"model": model, "messages": USER}


def emb_body(model: str = EMB_MODEL) -> dict[str, Any]:
    return {"model": model, "input": "hi"}


def msg_body(model: str = MSG_MODEL) -> dict[str, Any]:
    return {"model": model, "max_tokens": 16, "messages": USER}


def resp_body(model: str = CHAT_MODEL) -> dict[str, Any]:
    return {"model": model, "input": "hi"}


_BODY_FOR_PATH = {
    "chat_ns": chat_body,
    "chat_s": chat_body,
    "emb": emb_body,
    "messages_ns": msg_body,
    "messages_s": msg_body,
    "responses_ns": resp_body,
    "responses_s": resp_body,
}


def vector(
    group: str,
    vid: str,
    *,
    backends: list[dict[str, Any]],
    path: str = "",
    divergence: list[str] | None = None,
    note: str = "",
    routing: dict[str, Any] | None = None,
    cloud: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    pre_state: dict[str, Any] | None = None,
    wire: dict[str, Any] | None = None,
    drive: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    scenario: dict[str, Any] = {"backends": backends}
    if routing:
        scenario["routing"] = routing
    if cloud:
        scenario["cloud"] = cloud
    request: dict[str, Any] = {"path": path, "headers": headers or {}}
    if path:
        request["body"] = body if body is not None else _BODY_FOR_PATH[path]()
    elif body is not None:
        request["body"] = body
    doc: dict[str, Any] = {
        "id": vid,
        "divergence": divergence or [],
        "note": note,
        "scenario": scenario,
        "request": request,
    }
    if pre_state:
        doc["pre_state"] = pre_state
    if wire:
        doc["wire"] = wire
    if drive:
        doc["drive"] = drive
    return group, doc


# --------------------------------------------------------------------------
# Group: streaming
# --------------------------------------------------------------------------

_FAILOVER = {"default_strategy": "failover"}

# The Messages surfaces ask for MSG_MODEL; an openai-format farm host serves
# CHAT_MODEL. Without an alias bridging the two, no candidate matches and the
# request collapses into a keyless 401 with zero upstream calls (D11) instead
# of exercising the translated arm — see anticollapse.py. Every messages
# vector aimed at an openai-format row therefore routes through this.
_TRANSLATED_ARM = {**_FAILOVER, "model_aliases": {MSG_MODEL: [CHAT_MODEL]}}


def _streaming() -> list[tuple[str, dict[str, Any]]]:
    g = "streaming"
    out: list[tuple[str, dict[str, Any]]] = []

    # -- mid-stream drop: dialect error frame, and no retry after byte 1 --
    #
    # Every one of these has a healthy second row available; the recorded
    # upstream_calls list proving only ONE backend was ever contacted is
    # the `yielded_any` no-replay guard.
    out.append(
        vector(
            g,
            "stream-midstream-drop-chat-s",
            path="chat_s",
            backends=[oai("alpha", [drop(2)]), oai("beta")],
            routing=_FAILOVER,
            note=(
                "C-S after first byte: OpenAI-shaped data:{error} frame plus "
                "data:[DONE], HTTP 200 (headers long gone), exactly one "
                "upstream call, failure counted once via _stream_with_metrics"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-midstream-drop-after-usage-chunk-chat-s",
            path="chat_s",
            backends=[oai("alpha", [drop(4)]), oai("beta")],
            routing=_FAILOVER,
            note=(
                "drop between the usage-bearing final chunk and data:[DONE] — "
                "usage was parsed but the attempt still fails, so no token "
                "telemetry is recorded: success accounting is all-or-nothing"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-midstream-drop-messages-s-anthropic-arm",
            path="messages_s",
            backends=[ant("ant1", [drop(2)]), ant("ant2")],
            divergence=["D9"],
            routing=_FAILOVER,
            note=(
                "M-S native arm: Anthropic-shaped `event: error` frame with "
                "NO terminator event (plan Phase 7 [D9] adds one) and no "
                "data:[DONE] — the stream simply stops"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-midstream-drop-messages-s-translated-arm",
            path="messages_s",
            backends=[oai("alpha", [drop(2)])],
            divergence=["D9"],
            routing=_TRANSLATED_ARM,
            note=(
                "M-S openai-format arm: the OpenAI mid-stream failure is "
                "translated into the same terminator-less Anthropic error "
                "frame; message_start carries the per-backend model"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-midstream-drop-responses-s",
            path="responses_s",
            backends=[oai("alpha", [drop(2)]), oai("beta")],
            divergence=["D16"],
            routing=_FAILOVER,
            note=(
                "RESP-S: the Responses translator does not understand the "
                "OpenAI data:{error} chunk, drops it, and still emits "
                "response.completed — a mid-stream upstream failure is "
                "INVISIBLE to a Responses client even though the backend was "
                "marked failed. Real finding, no D-number; recorded as-is. "
                "[D16] the 200 carries no success accounting either "
                "(routed_counts empty, request_count 0) while the error "
                "counter from the failed attempt is present."
            ),
        )
    )

    # -- pre-first-byte failover is invisible to the client ---------------
    out.append(
        vector(
            g,
            "stream-prefirstbyte-drop-failover-chat-s",
            path="chat_s",
            backends=[oai("alpha", [drop(0)]), oai("beta")],
            routing=_FAILOVER,
            note=(
                "connection dies after response headers but before frame 0: "
                "two upstream calls, one clean stream, no error frame"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-prefirstbyte-failover-messages-s",
            path="messages_s",
            backends=[ant("ant1", [drop(0)]), ant("ant2")],
            divergence=["D6", "D8"],
            routing=_FAILOVER,
            note=(
                "the anthropic rows are pre-seeded into `tried` [D8] so the "
                "strategy loop yields nothing and both attempts come from the "
                "ordered fallback tier [D6]; the client still sees one stream"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-prefirstbyte-failover-responses-s",
            path="responses_s",
            backends=[oai("alpha", [FAIL_500]), oai("beta")],
            divergence=["D16"],
            routing=_FAILOVER,
            note=(
                "RESP-S inherits C-S failover; edge translation starts on "
                "beta. [D16] beta's success is not accounted — only alpha's "
                "error counter lands"
            ),
        )
    )

    # -- usage-chunk parse into telemetry (F-33) --------------------------
    out.append(
        vector(
            g,
            "stream-usage-chunk-parsed-chat-s",
            path="chat_s",
            backends=[oai("alpha", [stream_ok(usage_in_final=True)])],
            note=(
                "prompt/completion token counters and _request_count come "
                "from the SSE usage chunk"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-usage-absent-chat-s",
            path="chat_s",
            backends=[oai("alpha", [stream_ok(usage_in_final=False)])],
            note=(
                "no stream_options.include_usage: success is still fully "
                "accounted, token counters simply stay at zero (absent from "
                "the delta) — usage-absent tolerance"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-usage-chunk-parsed-messages-s",
            path="messages_s",
            backends=[ant("ant1", [stream_ok(usage_in_final=True)])],
            divergence=["D6", "D8"],
            note=(
                "M-S native arm records the same full accounting as C-S "
                "(F-33), reading input_tokens from message_start and "
                "output_tokens from message_delta"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-usage-absent-messages-s",
            path="messages_s",
            backends=[ant("ant1", [stream_ok(usage_in_final=False)])],
            divergence=["D6", "D8"],
            note="message_delta without usage: message_start's input_tokens still land",
        )
    )
    out.append(
        vector(
            g,
            "stream-usage-chunk-parsed-responses-s",
            path="responses_s",
            backends=[oai("alpha", [stream_ok(usage_in_final=True)])],
            divergence=["D16"],
            note=(
                "[D16] usage IS parsed below the Responses translation, but "
                "none of it is ever recorded: the bridge breaks out of the "
                "chat stream at data:[DONE], so _stream_with_metrics' "
                "post-loop success accounting never runs. RESP-S telemetry is "
                "therefore EMPTY where C-S records mark_success, ok-"
                "REQUESTS_TOTAL, latency, tokens and _request_count"
            ),
        )
    )

    # -- client disconnect: release + in-flight balance under GeneratorExit
    #
    # drive.mode = service_stream_aclose (harness gap 1). The recorded
    # sse_frames are what the client actually received before dropping.
    out.append(
        vector(
            g,
            "stream-client-disconnect-chat-s",
            path="chat_s",
            backends=[oai("alpha", [stream_ok(["a", "b", "c", "d"])])],
            drive={"mode": "service_stream_aclose", "consume": 2},
            note=(
                "GeneratorExit at chunk 2: pool in_flight and _source_in_flight "
                "both return to zero, BACKEND_IN_FLIGHT gauge is 0, and NO "
                "success accounting is recorded for the partial stream"
            ),
        )
    )
    out.append(
        vector(
            g,
            "stream-client-disconnect-first-chunk-chat-s",
            path="chat_s",
            backends=[oai("alpha", [stream_ok(["a", "b", "c", "d"])])],
            drive={"mode": "service_stream_aclose", "consume": 1},
            note="earliest observable disconnect — the same release balance holds",
        )
    )
    out.append(
        vector(
            g,
            "stream-client-disconnect-messages-s",
            path="messages_s",
            backends=[ant("ant1", [stream_ok(["a", "b", "c", "d"])])],
            divergence=["D6", "D8"],
            drive={"mode": "service_stream_aclose", "consume": 2},
            note="M-S release path under GeneratorExit (acquire is inside the loop)",
        )
    )
    out.append(
        vector(
            g,
            "stream-client-disconnect-responses-s",
            path="responses_s",
            backends=[oai("alpha", [stream_ok(["a", "b", "c", "d"])])],
            divergence=["D16"],
            drive={"mode": "service_stream_aclose", "consume": 2},
            note=(
                "GeneratorExit propagates through the Responses translation "
                "generator. [D16] no success accounting — indistinguishable "
                "from a completed RESP-S stream, which records none either"
            ),
        )
    )

    # -- SSE frame-boundary splits ----------------------------------------
    #
    # wire.split_bytes repacks the farm's byte stream so wire frames are cut
    # mid-line (and, at 1 byte, mid-token). The contract is that the router's
    # output is byte-identical to the unsplit recording of the same script.
    out.append(
        vector(
            g,
            "stream-frame-split-mid-line-chat-s",
            path="chat_s",
            backends=[oai("alpha", [stream_ok(["a", "b"])])],
            wire={"split_bytes": 7},
            note="7-byte wire chunks cut every `data:` line; SDK reassembly absorbs it",
        )
    )
    out.append(
        vector(
            g,
            "stream-frame-split-one-byte-chat-s",
            path="chat_s",
            backends=[oai("alpha", [stream_ok(["a", "b"])])],
            wire={"split_bytes": 1},
            note="pathological 1-byte wire chunks; identical client-visible frames",
        )
    )
    out.append(
        vector(
            g,
            "stream-frame-split-mid-line-messages-s",
            path="messages_s",
            backends=[ant("ant1", [stream_ok(["a", "b"])])],
            divergence=["D6", "D8"],
            wire={"split_bytes": 7},
            note="same for the Anthropic `event:`/`data:` frame pair",
        )
    )
    out.append(
        vector(
            g,
            "stream-frame-split-mid-line-responses-s",
            path="responses_s",
            backends=[oai("alpha", [stream_ok(["a", "b"])])],
            divergence=["D16"],
            wire={"split_bytes": 7},
            note=(
                "split boundaries do not perturb Responses sequence_number "
                "ordering. [D16] a clean 200 with zero success accounting"
            ),
        )
    )
    return out


# --------------------------------------------------------------------------
# Group: errors
# --------------------------------------------------------------------------


def _errors() -> list[tuple[str, dict[str, Any]]]:
    g = "errors"
    out: list[tuple[str, dict[str, Any]]] = []

    # -- per-surface envelope shape (F-38) --------------------------------
    out.append(
        vector(
            g,
            "errors-envelope-openai-404-chat-ns",
            path="chat_ns",
            backends=[oai("alpha")],
            body=chat_body("no-such-model"),
            routing=_FAILOVER,
            note=(
                "OpenAI envelope: {'error': {message, type, param, code}} with "
                "type=invalid_request_error, code=not_found"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-openai-404-emb",
            path="emb",
            backends=[emb("embed1")],
            body=emb_body("no-such-embed-model"),
            routing=_FAILOVER,
            note="same envelope on /v1/embeddings, capability='embedding' in the hint",
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-openai-404-responses-ns",
            path="responses_ns",
            backends=[oai("alpha")],
            body=resp_body("no-such-model"),
            routing=_FAILOVER,
            note=(
                "/v1/responses is an OpenAI surface: OpenAI envelope, not a "
                "Responses-shaped one"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-openai-405-wrong-method",
            backends=[oai("alpha")],
            drive={"mode": "raw", "method": "GET", "url": "/v1/chat/completions"},
            note=(
                "the envelope is installed as an exception handler, so even "
                "framework-generated 405s on /v1/* come out OpenAI-shaped"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-anthropic-401-keyless-messages-ns",
            path="messages_ns",
            backends=[oai("alpha")],
            divergence=["D11"],
            body=msg_body("no-such-model"),
            routing=_FAILOVER,
            note=(
                "Anthropic envelope {'type':'error','error':{type,message}}; "
                "[D11] the Messages surface answers 401 authentication_error "
                "where every OpenAI surface answers 404"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-anthropic-500-forwarded-messages-ns",
            path="messages_ns",
            backends=[ant("ant1", [FAIL_500])],
            divergence=["D6", "D8"],
            routing=_FAILOVER,
            note=(
                "AnthropicUpstreamError keeps its upstream status (500 -> "
                "api_error) instead of being flattened to 502"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-anthropic-502-openai-flattened-messages-ns",
            path="messages_ns",
            backends=[oai("alpha", [FAIL_500])],
            divergence=["D11"],
            routing=_TRANSLATED_ARM,
            note=(
                "[D11] an OpenAIUpstreamError from a translated backend is "
                "flattened to 502 on the Messages route (400/404 are not "
                "forwarded either) — body is still Anthropic-shaped"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-detail-netllm-404",
            backends=[oai("alpha")],
            drive={
                "mode": "raw",
                "method": "GET",
                "url": "/netllm/v1/cloud/providers/nope/models",
            },
            note=(
                "/netllm/* keeps FastAPI's {'detail': ...} — a control surface, "
                "not a dialect"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-envelope-detail-netllm-400",
            backends=[oai("alpha")],
            drive={
                "mode": "raw",
                "method": "POST",
                "url": "/netllm/v1/admin/config",
                "json": ["not-a-dict"],
            },
            note="a route-raised 400 on /netllm/* is {'detail'} too",
        )
    )

    # -- pre-stream 429 / 502 (F-32 revived the route-layer mapping) ------
    out.append(
        vector(
            g,
            "errors-prestream-429-source-cap-responses-s",
            path="responses_s",
            backends=[oai("alpha")],
            routing={**_FAILOVER, **CAPPED_SOURCE},
            headers={"x-netllm-source": "cli"},
            pre_state={"source_in_flight": {"cli": 1}},
            note=(
                "post-F-32 a source-cap rejection on stream=true is a real "
                "HTTP 429 with an OpenAI-shaped body, not 200 + aborted SSE"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-prestream-502-upstream-500-responses-s",
            path="responses_s",
            backends=[oai("alpha", [FAIL_500])],
            routing=_FAILOVER,
            note="pre-first-byte exhaustion on a stream path is a real 502",
        )
    )
    out.append(
        vector(
            g,
            "errors-prestream-502-openai-flattened-messages-s",
            path="messages_s",
            backends=[oai("alpha", [FAIL_500])],
            divergence=["D11"],
            routing=_TRANSLATED_ARM,
            note="[D11] flattening applies to the stream route too, post-F-32",
        )
    )
    out.append(
        vector(
            g,
            "errors-prestream-401-keyless-messages-s",
            path="messages_s",
            backends=[oai("alpha")],
            divergence=["D11"],
            body=msg_body("no-such-model"),
            routing=_FAILOVER,
            note="keyless M-S exhaustion: 401 before a single SSE byte is written",
        )
    )

    # -- capacity classification: status set + body marker ----------------
    #
    # One row, max_backend_failures=1: a capacity error is counted in
    # capacity_rejections and leaves health_status "online"; a hard failure
    # trips the row offline. Every one of them still exits as a 502 —
    # upstream capacity statuses are NOT forwarded to the client.
    for status in (409, 429, 503, 507):
        out.append(
            vector(
                g,
                f"errors-capacity-{status}-does-not-trip-offline",
                path="chat_ns",
                backends=[oai("alpha", [{"behavior": "http", "status": status}])],
                routing={**_FAILOVER, "max_backend_failures": 1},
                note=(
                    f"{status} is in the capacity status set: "
                    "capacity_rejections +1, health stays online, client 502"
                ),
            )
        )
    out.append(
        vector(
            g,
            "errors-capacity-body-marker-does-not-trip-offline",
            path="chat_ns",
            backends=[
                oai(
                    "alpha",
                    [
                        {
                            "behavior": "capacity_body_marker",
                            "marker": "memory pressure",
                            "status": 500,
                        }
                    ],
                )
            ],
            routing={**_FAILOVER, "max_backend_failures": 1},
            note=(
                "body-marker arm: a non-capacity status whose body says "
                "'memory pressure'"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-capacity-control-500-trips-offline",
            path="chat_ns",
            backends=[oai("alpha", [FAIL_500])],
            routing={**_FAILOVER, "max_backend_failures": 1},
            note=(
                "control for the six above: a plain 500 is NOT capacity — "
                "zero capacity_rejections and the row goes offline"
            ),
        )
    )
    out.append(
        vector(
            g,
            "errors-capacity-429-not-forwarded-chat-s",
            path="chat_s",
            backends=[oai("alpha", [{"behavior": "http", "status": 429}])],
            routing={**_FAILOVER, "max_backend_failures": 1},
            note=(
                "an UPSTREAM 429 pre-first-byte surfaces as 502, not 429: only "
                "the router's own source-cap rejection produces a client 429"
            ),
        )
    )
    return out


def all_vectors() -> list[tuple[str, dict[str, Any]]]:
    return [*_streaming(), *_errors()]


# --------------------------------------------------------------------------
# Runner (harness gaps 1–4 — see module docstring)
# --------------------------------------------------------------------------


def vector_path(group: str, vid: str) -> Path:
    return GROUP_DIR / group / f"{vid}.json"


def _apply_pre_state(env: Any, pre_state: dict[str, Any]) -> None:
    for source_id, count in pre_state.get("source_in_flight", {}).items():
        env.service._source_in_flight[source_id] = int(count)


def _apply_wire(env: Any, wire: dict[str, Any]) -> None:
    """Repack the farm's SSE byte stream into fixed-size chunks.

    Wraps the FakeFarm *instance's* bound ``_stream_frames`` so wire frames
    are cut at arbitrary byte offsets — mid-line, mid-JSON-token — without
    touching farm.py.
    """
    size = int(wire["split_bytes"])
    original = env.farm._stream_frames

    def repacked(*args: Any, **kwargs: Any) -> list[bytes]:
        blob = b"".join(original(*args, **kwargs))
        return [blob[i : i + size] for i in range(0, len(blob), size)]

    env.farm._stream_frames = repacked


def _frames_from_chunks(chunks: list[str]) -> list[str]:
    """SSE chunk strings -> drivers.drive's frame representation."""
    return [f for f in "".join(chunks).split("\n\n") if f.strip()]


def _drive_service_stream_aclose(env: Any, doc: dict[str, Any]) -> DriveResult:
    """Consume N chunks from the service generator, then aclose() it."""
    request = doc["request"]
    method = _STREAM_METHOD[request["path"]]
    payload = {**request["body"], "stream": True}
    headers = dict(request.get("headers") or {})
    consume = int(doc["drive"].get("consume", 1))

    async def _run() -> list[str]:
        gen = getattr(env.service, method)(payload, headers=headers)
        received: list[str] = []
        async for chunk in gen:
            received.append(chunk)
            if len(received) >= consume:
                break
        await gen.aclose()
        return received

    chunks = asyncio.run(_run())
    return DriveResult(status=200, body=None, frames=_frames_from_chunks(chunks))


def _drive_raw(env: Any, doc: dict[str, Any]) -> DriveResult:
    spec = doc["drive"]
    kwargs: dict[str, Any] = {}
    if "json" in spec:
        kwargs["json"] = spec["json"]
    resp = env.client.request(spec["method"], spec["url"], **kwargs)
    try:
        body: Any = resp.json()
    except ValueError:  # pragma: no cover - defensive
        body = resp.text
    return DriveResult(status=resp.status_code, body=body, frames=None)


def run_vector(doc: dict[str, Any]) -> dict[str, Any]:
    """test_vectors.run_vector plus the pre_state / wire / drive extensions."""
    with contract_environment(doc["scenario"]) as env:
        if doc.get("pre_state"):
            _apply_pre_state(env, doc["pre_state"])
        if doc.get("wire"):
            _apply_wire(env, doc["wire"])
        mode = (doc.get("drive") or {}).get("mode", "http")
        if mode == "http":
            result = env.drive(doc["request"])
        elif mode == "service_stream_aclose":
            result = _drive_service_stream_aclose(env, doc)
        elif mode == "raw":
            result = _drive_raw(env, doc)
        else:  # pragma: no cover - guarded by test_vector_docs_are_well_formed
            raise AssertionError(f"unknown drive mode {mode!r}")
        delta = env.probe.delta()
        calls = env.farm.inference_calls()
    return _normalize_local_volatiles(canonicalize_result(result, delta, calls))


def _normalize_local_volatiles(obj: Any) -> Any:
    """Local extension of canonical.VOLATILE_BODY_FIELDS (harness gap 5).

    The Responses bridge mints a fresh ``msg_<hex>`` item id per request and
    echoes it as ``item_id`` on every ``response.*`` event. The shared
    canonicalizer normalizes ``id`` but not ``item_id``, so every RESP-S
    vector that reaches a content-part event is nondeterministic without
    this. Deliberately narrow: one key, applied after canonicalization, so
    any OTHER new source of volatility still fails loudly.
    """
    if isinstance(obj, dict):
        return {
            k: ("<id>" if k == "item_id" else _normalize_local_volatiles(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_normalize_local_volatiles(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("data: ") and '"item_id"' in obj:
        # canonicalize_sse_frames re-serializes each data payload to a
        # string before we see it, so reach back into the JSON.
        payload = _normalize_local_volatiles(json.loads(obj[len("data: ") :]))
        return "data: " + json.dumps(payload, sort_keys=True)
    return obj


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def checked_in_vectors() -> list[Path]:
    return sorted(GROUP_DIR.glob("*/*.json"))


# --------------------------------------------------------------------------
# Tests (pytest does not auto-collect this module — pass the path explicitly)
# --------------------------------------------------------------------------


def test_vector_ids_are_unique_and_grouped() -> None:
    ids = [doc["id"] for _, doc in all_vectors()]
    assert len(ids) == len(set(ids)), "duplicate streaming-errors vector ids"
    for group, doc in all_vectors():
        assert group in GROUPS, f"{doc['id']}: unknown group {group!r}"
        assert doc["id"].startswith(_ID_PREFIX[group]), (
            f"{doc['id']}: id does not carry its group prefix"
        )


def test_divergence_annotations_are_valid_ids() -> None:
    for _, doc in all_vectors():
        unknown = set(doc["divergence"]) - VALID_DIVERGENCE_IDS
        assert not unknown, f"{doc['id']}: unknown divergence IDs {unknown}"


def test_no_unintended_keyless_401_recorded() -> None:
    """Replay-time half of the anti-collapse guard (anticollapse.py)."""
    assert_corpus_has_no_unintended_keyless_401(checked_in_vectors())


def test_no_orphan_vector_files() -> None:
    declared = {vector_path(g, d["id"]) for g, d in all_vectors()}
    orphans = sorted(str(p) for p in set(checked_in_vectors()) - declared)
    assert not orphans, f"vector files with no scenario definition: {orphans}"


@pytest.mark.parametrize(
    ("group", "doc"), all_vectors(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_streaming_errors_vector(group: str, doc: dict[str, Any]) -> None:
    path = vector_path(group, doc["id"])
    actual = run_vector(doc)
    # Anti-collapse: fails at record time, before a false vector can land.
    assert_no_unintended_keyless_401(doc, actual)
    if RECORD:
        _write(path, {**doc, "expected": actual})
    assert path.exists(), f"{path.name} not recorded — run with NETLLM_VECTOR_RECORD=1"
    stored = json.loads(path.read_text())
    # Drift guard: the checked-in document must still describe the scenario
    # this module declares, so a vector can never be "fixed" by editing the
    # world instead of the expectation.
    drift_keys = (
        "id",
        "divergence",
        "scenario",
        "request",
        "pre_state",
        "wire",
        "drive",
    )
    for key in drift_keys:
        assert stored.get(key) == doc.get(key), f"{path.name}: {key!r} drifted"
    assert stored.get("expected") is not None, f"{path.name} has no expected block"
    assert actual == stored["expected"]
