"""Phase 0b — routing/failover contract scenarios (plan-f24-f26.md §2).

This module is the single source of truth for the routing-failover slice of
the contract-vector corpus. Every scenario is declared here as a JSON-ready
``ContractVector`` document (``vectors/__format__.md``); the recorded
``expected`` blocks live one-per-file under
``tests/contract/vectors/routing-failover/``.

Scenario groups (plan §2 "Scenario axis"):

- ``happy``      — one vector per routing strategy, incl. ``batch_shard``
                   with and without shard headers and ``auto`` both ways.
- ``chain``      — failover chains: hard-fail-then-ok, capacity-429-then-ok
                   (asserting no offline trip), capacity-body-marker, the
                   attempt cap with cloud extras present.
- ``exhaust``    — every candidate fails / no candidate matches, one vector
                   per surface, pinning the per-surface error outcome.
- ``header``     — ``x-netllm-strategy``, ``x-netllm-backend`` pin,
                   ``x-netllm-local-only`` and the ``x-netllm-hops``
                   backstop.
- ``peer``       — peer loop-guard headers: absent on non-peer rows.
- ``admission``  — per-source ``max_concurrency`` 429, incl. ``stream=true``.

Divergence annotations
======================

A vector carries ``"divergence": ["Dn"]`` only where the *cell it pins* is a
behavior-matrix divergence that is still open on this tree (D5 shard context
missing off the chat paths, D6/D8 the Messages fallback-tier + ``tried``
pre-seeding, D7 the attempt cap excluding ``cloud_extra``, D11 the keyless
401 vs 404 exhaustion split). Vectors for behavior the F-30..F-48
remediation already fixed — notably D3, streaming route-layer error mapping,
which is why the ``*-s`` exhaustion and admission vectors are real HTTP
statuses rather than 200 + aborted body — carry **no** annotation: they pin
the fixed behavior and must not move again.

Every vector also runs through ``anticollapse.assert_no_unintended_keyless_
401`` at record time and ``assert_corpus_has_no_unintended_keyless_401`` at
replay time: a keyless-401 recording (401 + zero upstream calls + a missing
``ANTHROPIC_API_KEY`` body) is only allowed where the vector's ``note`` says
so, because that outcome is also what a scenario whose request never reaches
the farm produces. The two ``exhaust-unknown-model-messages-*`` vectors are
the intentional ones here.

Running
=======

    uv run pytest tests/contract/scenarios_routing_failover.py
    NETLLM_VECTOR_RECORD=1 uv run pytest \\
        tests/contract/scenarios_routing_failover.py   # (re)record

Two harness gaps this module works around locally (see the Phase 0b report;
both want an additive fix in the harness, not here):

1. ``test_vectors.py::_vector_files`` globs ``vectors/*.json``
   non-recursively, so it does not see this sub-directory. The replay test
   below is a stand-in and should be deleted once that glob is recursive.
   WARNING while that stand-in exists: this file does not match pytest's
   ``python_files``, so it is collected ONLY when named directly on the
   command line — and even then pytest drops it if ``tests/contract`` is
   passed in the same invocation (the directory collection wins the
   dedupe). ``uv run pytest tests/contract tests/contract/\\
   scenarios_routing_failover.py`` silently runs 49 tests, not 96. Run it
   as its own invocation until the harness glob is fixed.
2. ``ContractVector`` has no pre-state / concurrency hook, so a per-source
   ``max_concurrency`` rejection is unreachable through a single sequential
   ``drive()``. The ``pre_state`` key handled by ``_run_vector`` below is a
   local extension of the vector format for exactly that.
"""

from __future__ import annotations

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
from drivers import contract_environment, scanned_backend_id
from netllm_core.models import Backend, BackendHealth

VECTOR_DIR = Path(__file__).resolve().parent / "vectors" / "routing-failover"
RECORD = os.environ.get("NETLLM_VECTOR_RECORD") == "1"

_VALID_DIVERGENCE_IDS = {f"D{i}" for i in range(1, 17)}

# --------------------------------------------------------------------------
# Fixtures of the world: farm backends, request bodies, shared config bits
# --------------------------------------------------------------------------

CHAT_MODEL = "farm-chat"
EMB_MODEL = "farm-embed"
MSG_MODEL = "claude-farm"

OK = {"behavior": "ok"}
FAIL_500 = {"behavior": "http", "status": 500}
CAPACITY_429 = {"behavior": "http", "status": 429}
CAPACITY_MARKER = {
    "behavior": "capacity_body_marker",
    "marker": "memory pressure",
    "status": 500,
}


def chat_backend(
    name: str,
    script: list[dict[str, Any]] | None = None,
    *,
    local: bool = True,
    scan_visible: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "api_format": "openai",
        "models": [CHAT_MODEL],
        "script": script or [OK],
        "local": local,
        "scan_visible": scan_visible,
    }


def emb_backend(
    name: str, script: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "api_format": "openai",
        "models": [EMB_MODEL],
        "script": script or [OK],
    }


def anthropic_backend(
    name: str, script: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "api_format": "anthropic",
        "models": [MSG_MODEL],
        "script": script or [OK],
    }


def chat_body(model: str = CHAT_MODEL) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


def emb_body(model: str = EMB_MODEL) -> dict[str, Any]:
    return {"model": model, "input": "hi"}


def msg_body(model: str = MSG_MODEL) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    }


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

# Shard headers: the explicit connector-style batch convention
# (netllm_agent.shard.extract_shard_context).
SHARD_HEADERS = {"x-netllm-batch-id": "batch-7", "x-netllm-shard-index": "1"}

# A source with a concurrency ceiling, addressed by x-netllm-source.
CAPPED_SOURCE = {
    "sources": [
        {
            "id": "cli",
            "enabled": True,
            "max_concurrency": 1,
        }
    ]
}

BETA_PIN = scanned_backend_id("http://beta.farm/v1")


def vector(
    vid: str,
    *,
    path: str,
    backends: list[dict[str, Any]],
    divergence: list[str] | None = None,
    routing: dict[str, Any] | None = None,
    cloud: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    pre_state: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    scenario: dict[str, Any] = {"backends": backends}
    if routing:
        scenario["routing"] = routing
    if cloud:
        scenario["cloud"] = cloud
    doc: dict[str, Any] = {
        "id": vid,
        "divergence": divergence or [],
        "scenario": scenario,
        "request": {
            "path": path,
            "body": body if body is not None else _BODY_FOR_PATH[path](),
            "headers": headers or {},
        },
    }
    if pre_state:
        doc["pre_state"] = pre_state
    if note:
        # Free-text; also the anti-collapse declaration channel
        # (anticollapse.declares_keyless_401).
        doc["note"] = note
    return doc


# --------------------------------------------------------------------------
# Group: happy — one vector per strategy
# --------------------------------------------------------------------------

_STRATEGIES = (
    "failover",
    "round_robin",
    "least_load",
    "latency_weighted",
    "local_spillover",
)

HAPPY: list[dict[str, Any]] = [
    vector(
        f"happy-{strategy.replace('_', '-')}",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": strategy},
    )
    for strategy in _STRATEGIES
]

HAPPY += [
    # auto with no shard context degrades to least_load ...
    vector(
        "happy-auto-no-shard",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "auto"},
    ),
    # ... and to batch_shard the moment shard context is present.
    vector(
        "happy-auto-with-shard",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "auto"},
        headers=SHARD_HEADERS,
    ),
    # batch_shard proper: ledger assignment for (batch, index).
    vector(
        "happy-batch-shard-with-headers",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "batch_shard"},
        headers=SHARD_HEADERS,
    ),
    # batch_shard without any shard context: degenerate, counted in
    # _shardless_fallbacks and served by round_robin.
    vector(
        "happy-batch-shard-no-headers",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "batch_shard"},
    ),
    # D5: EMB/M-NS pass shard=None into selection, so identical shard
    # headers degrade to the shardless fallback instead of placing.
    vector(
        "happy-batch-shard-emb-shard-ignored",
        path="emb",
        backends=[emb_backend("embed1"), emb_backend("embed2")],
        divergence=["D5"],
        routing={"default_strategy": "batch_shard"},
        headers=SHARD_HEADERS,
    ),
    vector(
        "happy-batch-shard-messages-shard-ignored",
        path="messages_ns",
        backends=[anthropic_backend("anthro1"), anthropic_backend("anthro2")],
        divergence=["D5"],
        routing={"default_strategy": "batch_shard"},
        headers=SHARD_HEADERS,
    ),
]

# --------------------------------------------------------------------------
# Group: chain — failover chains
# --------------------------------------------------------------------------

CHAIN: list[dict[str, Any]] = [
    # Two hard failures then success: three attempts, two error counters,
    # failure counts on the two dead rows.
    vector(
        "chain-hard-fail-then-ok",
        path="chat_ns",
        backends=[
            chat_backend("alpha", [FAIL_500]),
            chat_backend("beta", [FAIL_500]),
            chat_backend("gamma", [OK]),
        ],
        routing={"default_strategy": "failover"},
    ),
    # Capacity 429 is counted in capacity_rejections and must NOT trip the
    # backend offline (pool.py: capacity errors never count toward
    # max_backend_failures) — health_status stays "online".
    vector(
        "chain-capacity-429-then-ok",
        path="chat_ns",
        backends=[
            chat_backend("alpha", [CAPACITY_429]),
            chat_backend("beta", [OK]),
        ],
        routing={"default_strategy": "failover", "max_backend_failures": 1},
    ),
    # Same, via the body-marker arm of is_capacity_error (non-capacity
    # status, capacity-shaped body).
    vector(
        "chain-capacity-body-marker-then-ok",
        path="chat_ns",
        backends=[
            chat_backend("alpha", [CAPACITY_MARKER]),
            chat_backend("beta", [OK]),
        ],
        routing={"default_strategy": "failover", "max_backend_failures": 1},
    ),
    # Streaming failover before the first byte is invisible to the client:
    # the client sees one clean stream from beta.
    vector(
        "chain-stream-prefirstbyte-failover",
        path="chat_s",
        backends=[
            chat_backend("alpha", [FAIL_500]),
            chat_backend("beta", [OK]),
        ],
        routing={"default_strategy": "failover"},
    ),
    # Control for the D7 vector below: the legacy cloud row IS injected
    # from the caller's Authorization header and DOES participate in
    # selection like a pool row — here it is the only candidate for an
    # unknown name, and the attempt against api.openai.com (a host FakeFarm
    # refuses, exactly like a dead port) is what the client sees.
    vector(
        "chain-cloud-extra-selected-when-no-local-candidate",
        path="chat_ns",
        backends=[chat_backend("alpha")],
        body=chat_body("gpt-4o"),
        routing={"default_strategy": "failover"},
        headers={"authorization": "Bearer sk-farm-cloud"},
    ),
    # D7: max_attempts = max(len(pool.backends), 1) counts POOL rows only,
    # so with the cloud row injected exactly as above, the single pool row's
    # failure ends the loop and the cloud candidate is never reached.
    vector(
        "chain-attempt-cap-with-cloud-extra",
        path="chat_ns",
        backends=[chat_backend("alpha", [FAIL_500])],
        divergence=["D7"],
        routing={"default_strategy": "failover"},
        headers={"authorization": "Bearer sk-farm-cloud"},
    ),
]

# --------------------------------------------------------------------------
# Group: exhaust — every candidate fails / nothing matches, per surface
# --------------------------------------------------------------------------

_EXHAUST_FAIL = {
    "chat_ns": (
        [chat_backend("alpha", [FAIL_500]), chat_backend("beta", [FAIL_500])],
        [],
    ),
    "chat_s": (
        [chat_backend("alpha", [FAIL_500]), chat_backend("beta", [FAIL_500])],
        [],
    ),
    "emb": ([emb_backend("embed1", [FAIL_500]), emb_backend("embed2", [FAIL_500])], []),
    "messages_ns": (
        [
            anthropic_backend("anthro1", [FAIL_500]),
            anthropic_backend("anthro2", [FAIL_500]),
        ],
        ["D6", "D8"],
    ),
    "messages_s": (
        [
            anthropic_backend("anthro1", [FAIL_500]),
            anthropic_backend("anthro2", [FAIL_500]),
        ],
        ["D6", "D8"],
    ),
    "responses_ns": (
        [chat_backend("alpha", [FAIL_500]), chat_backend("beta", [FAIL_500])],
        [],
    ),
    "responses_s": (
        [chat_backend("alpha", [FAIL_500]), chat_backend("beta", [FAIL_500])],
        [],
    ),
}

EXHAUST: list[dict[str, Any]] = [
    vector(
        f"exhaust-all-fail-{path.replace('_', '-')}",
        path=path,
        backends=backends,
        divergence=divergence,
        routing={"default_strategy": "failover"},
    )
    for path, (backends, divergence) in _EXHAUST_FAIL.items()
]

# No candidate ever matches: last_error stays None and each surface falls
# through to its own "nothing to route to" error. D11 is the split — the
# OpenAI surfaces 404 with a model-not-found hint, Messages 401s about a
# missing ANTHROPIC_API_KEY.
EXHAUST += [
    vector(
        "exhaust-unknown-model-chat-ns",
        path="chat_ns",
        backends=[chat_backend("alpha")],
        body=chat_body("no-such-model"),
        routing={"default_strategy": "failover"},
    ),
    vector(
        "exhaust-unknown-model-chat-s",
        path="chat_s",
        backends=[chat_backend("alpha")],
        body=chat_body("no-such-model"),
        routing={"default_strategy": "failover"},
    ),
    vector(
        "exhaust-unknown-model-emb",
        path="emb",
        backends=[emb_backend("embed1")],
        body=emb_body("no-such-embed-model"),
        routing={"default_strategy": "failover"},
    ),
    # Pool holds only an openai-format row, so the Messages anthropic
    # fallback tier is empty too: last_error is None and the surface answers
    # 401 "ANTHROPIC_API_KEY required" where the OpenAI surfaces answer 404.
    vector(
        "exhaust-unknown-model-messages-ns",
        path="messages_ns",
        backends=[chat_backend("alpha")],
        divergence=["D11"],
        body=msg_body("no-such-model"),
        routing={"default_strategy": "failover"},
        note=(
            "[D11] intentional keyless 401: no candidate matches, so M-NS "
            "answers 401 ANTHROPIC_API_KEY-required with zero upstream calls "
            "where the OpenAI surfaces answer 404"
        ),
    ),
    vector(
        "exhaust-unknown-model-messages-s",
        path="messages_s",
        backends=[chat_backend("alpha")],
        divergence=["D11"],
        body=msg_body("no-such-model"),
        routing={"default_strategy": "failover"},
        note="[D11] intentional keyless 401, same split on the M-S stream route",
    ),
    # D8: EMB pre-seeds every anthropic-format pool row into `tried`, so a
    # mixed pool only ever reaches the openai-format row.
    vector(
        "exhaust-emb-anthropic-rows-excluded",
        path="emb",
        backends=[emb_backend("embed1", [FAIL_500]), anthropic_backend("anthro1")],
        divergence=["D8"],
        routing={"default_strategy": "failover"},
    ),
]

# --------------------------------------------------------------------------
# Group: header — per-request routing overrides
# --------------------------------------------------------------------------

# A local row that serves only CHAT_MODEL, plus a genuinely non-local row
# the provider scan cannot see (so it keeps the override's local=false and
# stays a blind, catalog-less candidate). Requesting REMOTE_ONLY_MODEL makes
# the remote row the ONLY candidate, so local-only is observable as a
# routing decision rather than as a health accident.
REMOTE_ONLY_MODEL = "remote-only-model"

_LOCAL_AND_REMOTE = [
    chat_backend("alpha"),
    {
        "name": "beta",
        "api_format": "openai",
        "models": [],
        "script": [OK],
        "local": False,
        "scan_visible": False,
    },
]

HEADER: list[dict[str, Any]] = [
    # x-netllm-strategy beats routing.default_strategy: batch_shard would
    # have counted a shardless fallback, failover does not.
    vector(
        "header-strategy-override",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "batch_shard"},
        headers={"x-netllm-strategy": "failover"},
    ),
    # An unknown strategy name is ignored, not an error.
    vector(
        "header-strategy-invalid-ignored",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "failover"},
        headers={"x-netllm-strategy": "not-a-strategy"},
    ),
    # x-netllm-backend pins attempt 1 to a specific pool row.
    vector(
        "header-backend-pin",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "failover"},
        headers={"x-netllm-backend": BETA_PIN},
    ),
    # An unresolvable pin degrades to the resolved strategy.
    vector(
        "header-backend-pin-unknown",
        path="chat_ns",
        backends=[chat_backend("alpha"), chat_backend("beta")],
        routing={"default_strategy": "failover"},
        headers={"x-netllm-backend": "no-such-backend"},
    ),
    # Baseline for the three below: without local-only the remote row is
    # the only candidate for this name and serves the request.
    vector(
        "header-local-only-absent-uses-remote",
        path="chat_ns",
        backends=_LOCAL_AND_REMOTE,
        body=chat_body(REMOTE_ONLY_MODEL),
        routing={"default_strategy": "failover"},
    ),
    # x-netllm-local-only is an absolute ceiling: the remote row drops out
    # of candidacy, leaving nothing to route to (404, zero upstream calls).
    vector(
        "header-local-only-excludes-remote",
        path="chat_ns",
        backends=_LOCAL_AND_REMOTE,
        body=chat_body(REMOTE_ONLY_MODEL),
        routing={"default_strategy": "failover"},
        headers={"x-netllm-local-only": "1"},
    ),
    # Hop backstop: hops >= MAX_FORWARD_HOPS (2) implies local-only even
    # with the local-only header stripped.
    vector(
        "header-hops-backstop-forces-local-only",
        path="chat_ns",
        backends=_LOCAL_AND_REMOTE,
        body=chat_body(REMOTE_ONLY_MODEL),
        routing={"default_strategy": "failover"},
        headers={"x-netllm-hops": "2"},
    ),
    # One hop below the backstop still routes remotely.
    vector(
        "header-hops-below-backstop",
        path="chat_ns",
        backends=_LOCAL_AND_REMOTE,
        body=chat_body(REMOTE_ONLY_MODEL),
        routing={"default_strategy": "failover"},
        headers={"x-netllm-hops": "1"},
    ),
]

# --------------------------------------------------------------------------
# Group: peer — loop-guard headers
# --------------------------------------------------------------------------

PEER: list[dict[str, Any]] = [
    # Non-peer rows never receive the loop guard: the recorded upstream
    # headers carry neither x-netllm-local-only nor x-netllm-hops, even
    # though this request arrived carrying a hop count and a source id.
    vector(
        "peer-loopguard-absent-on-non-peer-row",
        path="chat_ns",
        backends=[chat_backend("alpha")],
        routing={"default_strategy": "failover", **CAPPED_SOURCE},
        headers={"x-netllm-hops": "1", "x-netllm-source": "cli"},
    ),
]

# --------------------------------------------------------------------------
# Group: admission — per-source max_concurrency
# --------------------------------------------------------------------------

ADMISSION: list[dict[str, Any]] = [
    vector(
        f"admission-source-cap-429-{path.replace('_', '-')}",
        path=path,
        backends=[chat_backend("alpha")]
        if path != "messages_s"
        else [anthropic_backend("anthro1")],
        routing={"default_strategy": "failover", **CAPPED_SOURCE},
        headers={"x-netllm-source": "cli"},
        pre_state={"source_in_flight": {"cli": 1}},
    )
    for path in ("chat_ns", "chat_s", "messages_s")
]

VECTORS: list[dict[str, Any]] = [*HAPPY, *CHAIN, *EXHAUST, *HEADER, *PEER, *ADMISSION]

GROUPS: dict[str, list[dict[str, Any]]] = {
    "happy": HAPPY,
    "chain": CHAIN,
    "exhaust": EXHAUST,
    "header": HEADER,
    "peer": PEER,
    "admission": ADMISSION,
}


# --------------------------------------------------------------------------
# Runner (harness gap #1/#2 — see module docstring)
# --------------------------------------------------------------------------


def vector_path(doc: dict[str, Any]) -> Path:
    return VECTOR_DIR / f"{doc['id']}.json"


def _apply_pre_state(env: Any, pre_state: dict[str, Any]) -> None:
    """Seed service state a single sequential drive() cannot reach."""
    for source_id, count in pre_state.get("source_in_flight", {}).items():
        env.service._source_in_flight[source_id] = int(count)


def _run_vector(doc: dict[str, Any]) -> dict[str, Any]:
    """test_vectors.run_vector plus the local ``pre_state`` extension."""
    with contract_environment(doc["scenario"]) as env:
        if doc.get("pre_state"):
            _apply_pre_state(env, doc["pre_state"])
        result = env.drive(doc["request"])
        delta = env.probe.delta()
        calls = env.farm.inference_calls()
    return canonicalize_result(result, delta, calls)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_vector_ids_are_unique_and_grouped() -> None:
    ids = [doc["id"] for doc in VECTORS]
    assert len(ids) == len(set(ids)), "duplicate routing-failover vector ids"
    prefixes = {doc["id"].split("-", 1)[0] for doc in VECTORS}
    assert prefixes == set(GROUPS), f"ungrouped vector prefixes: {prefixes}"


def test_divergence_annotations_are_valid_ids() -> None:
    for doc in VECTORS:
        unknown = set(doc["divergence"]) - _VALID_DIVERGENCE_IDS
        assert not unknown, f"{doc['id']}: unknown divergence IDs {unknown}"


def test_no_unintended_keyless_401_recorded() -> None:
    """Replay-time half of the anti-collapse guard (anticollapse.py)."""
    paths = sorted(VECTOR_DIR.glob("*.json")) if VECTOR_DIR.exists() else []
    assert_corpus_has_no_unintended_keyless_401(paths)


def test_no_orphan_vector_files() -> None:
    on_disk = (
        {p.stem for p in VECTOR_DIR.glob("*.json")} if VECTOR_DIR.exists() else set()
    )
    declared = {doc["id"] for doc in VECTORS}
    assert not on_disk - declared, (
        f"vector files with no scenario definition: {sorted(on_disk - declared)}"
    )


@pytest.mark.parametrize("doc", VECTORS, ids=lambda d: d["id"])
def test_routing_failover_vector(doc: dict[str, Any]) -> None:
    path = vector_path(doc)
    actual = _run_vector(doc)
    # Anti-collapse: fails at record time, before a false vector can land.
    assert_no_unintended_keyless_401(doc, actual)
    if RECORD:
        VECTOR_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({**doc, "expected": actual}, indent=2, sort_keys=True) + "\n"
        )
    assert path.exists(), f"{path.name} not recorded — run with NETLLM_VECTOR_RECORD=1"
    stored = json.loads(path.read_text())
    # Drift guard: the checked-in document must still describe the scenario
    # this module declares, so a vector can never be "fixed" by editing the
    # world instead of the expectation.
    for key in ("id", "divergence", "scenario", "request", "pre_state"):
        assert stored.get(key) == doc.get(key), f"{path.name}: {key!r} drifted"
    assert stored.get("expected") is not None, f"{path.name} has no expected block"
    assert actual == stored["expected"]


# --------------------------------------------------------------------------
# Peer loop-guard: the `peer:` half, at unit level (harness gap #3)
#
# FakeFarm/drivers can only create pool rows from routing.backends
# overrides, whose ids are base URLs or scan uuid5s — never "peer:<agent>".
# Peer rows enter the pool exclusively through SwarmRegistry, which needs a
# live /netllm/v1/status peer. Until the harness can mint one, these two
# characterization tests pin the header contract the vectors above can only
# cover negatively.
# --------------------------------------------------------------------------


def _peer_row() -> Backend:
    return Backend(
        id="peer:agent-9",
        base_url="http://10.0.0.9:11400/v1",
        provider="custom",
        local=False,
        agent_id="agent-9",
        health=BackendHealth(models=[CHAT_MODEL], model_count=1),
    )


def test_peer_forward_headers_present_on_peer_rows() -> None:
    scenario = {
        "backends": [chat_backend("alpha")],
        "routing": {"default_strategy": "failover", **CAPPED_SOURCE},
    }
    with contract_environment(scenario) as env:
        fwd = env.service._peer_forward_headers(
            _peer_row(), {"x-netllm-hops": "1", "x-netllm-source": "cli"}
        )
    assert fwd == {
        "x-netllm-local-only": "1",
        "x-netllm-hops": "2",
        "x-netllm-source": "cli",
    }


def test_peer_forward_headers_absent_on_non_peer_rows() -> None:
    scenario = {"backends": [chat_backend("alpha")]}
    with contract_environment(scenario) as env:
        row = env.service.pool.backends[0]
        assert not row.id.startswith("peer:")
        assert env.service._peer_forward_headers(row, {"x-netllm-hops": "1"}) is None
