"""Phase 6 lifecycle assertions — the engine's acquire/release contract.

The plan's exit criteria for Phase 6 are byte-identical vectors *plus* two
things a vector cannot see, because both are about what happens when the
request does not finish normally:

- **acquire/release pairing under injected mid-invoke exceptions.** The
  vector corpus drives real upstream failures (HTTP statuses, dropped
  connections), all of which arrive as the surface's classified error type.
  What it never produces is a failure *inside* the attempt that the adapter
  does not classify — an SDK bug, a ``TypeError`` from a signature drift
  (behavior-matrix D15 is exactly that class of defect), a ``KeyError`` in
  payload adaptation. Those propagate straight out of ``_run_attempt``, and
  the pool ledger must be balanced anyway.
- **admission released in ``finally``.** ``build_request_plan`` takes the
  per-source reservation before the caller's first ``await``; the matching
  release is ``run_with_failover``'s outermost ``finally``. Every exit path
  — success, exhaustion, unclassified exception, cancellation — has to go
  through it, or the source's ``max_concurrency`` leaks a slot per failure
  and the source is 429ing itself within a few requests.

Both are asserted per surface, because "it holds for chat" was the shape of
the original defect.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from drivers import contract_environment
from farm import ok
from netllm_agent.service.surfaces import (
    ChatAdapter,
    EmbeddingsAdapter,
    MessagesAdapter,
)
from netllm_sdk_openai.client import OpenAIUpstreamError

CHAT = "farm-chat"
EMBED = "bge-m3"
CLAUDE = "claude-farm"
USER = [{"role": "user", "content": "hi"}]


class Boom(RuntimeError):
    """Deliberately not any adapter's classified error type."""


def _three_row_scenario(model: str, api_format: str = "openai") -> dict[str, Any]:
    return {
        "backends": [
            {
                "name": name,
                "api_format": api_format,
                "models": [model],
                "script": [ok()] * 4,
            }
            for name in ("alpha", "beta", "gamma")
        ],
        "routing": {"default_strategy": "round_robin"},
    }


# (id, adapter class, scenario, coroutine factory)
SURFACES = [
    (
        "chat-ns",
        ChatAdapter,
        _three_row_scenario(CHAT),
        lambda svc: svc.proxy_chat_completion({"model": CHAT, "messages": USER}),
    ),
    (
        "embeddings",
        EmbeddingsAdapter,
        _three_row_scenario(EMBED),
        lambda svc: svc.proxy_embeddings({"model": EMBED, "input": "hi"}),
    ),
    (
        "messages-ns",
        MessagesAdapter,
        _three_row_scenario(CLAUDE, api_format="anthropic"),
        lambda svc: svc.proxy_messages(
            {"model": CLAUDE, "messages": USER, "max_tokens": 16}
        ),
    ),
]

SURFACE_IDS = [case[0] for case in SURFACES]


def _ledgers(service: Any) -> tuple[dict[str, int], dict[str, int]]:
    return (
        {b.base_url: b.in_flight for b in service.pool.backends},
        dict(service._source_in_flight),
    )


def _assert_balanced(service: Any, label: str) -> None:
    backend_in_flight, source_in_flight = _ledgers(service)
    assert all(v == 0 for v in backend_in_flight.values()), (
        f"{label}: leaked pool reservation {backend_in_flight}"
    )
    assert all(v == 0 for v in source_in_flight.values()), (
        f"{label}: leaked admission slot {source_in_flight}"
    )


@pytest.mark.parametrize(
    ("label", "adapter_cls", "scenario", "call"), SURFACES, ids=SURFACE_IDS
)
def test_unclassified_mid_invoke_exception_still_releases_everything(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    adapter_cls: type,
    scenario: dict[str, Any],
    call: Any,
) -> None:
    """An exception the adapter does not classify propagates — balanced.

    Not retried, either: an unclassified error is a bug in *us*, not a sick
    backend, so exactly one backend may have been touched.
    """

    async def boom(self: Any, plan: Any, invocation: Any) -> Any:
        raise Boom("injected mid-invoke")

    monkeypatch.setattr(adapter_cls, "invoke", boom)

    with contract_environment(scenario) as env:
        with pytest.raises(Boom):
            asyncio.run(call(env.service))
        _assert_balanced(env.service, label)
        assert env.farm.inference_calls() == [], (
            f"{label}: an unclassified failure must not be retried"
        )


@pytest.mark.parametrize(
    ("label", "adapter_cls", "scenario", "call"), SURFACES, ids=SURFACE_IDS
)
def test_exception_while_building_the_invocation_releases_everything(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    adapter_cls: type,
    scenario: dict[str, Any],
    call: Any,
) -> None:
    """The failure point that used to sit *outside* anybody's accounting.

    ``build_invocation`` runs after ``pool.acquire`` (upstream-client
    construction and per-backend model resolution are part of the attempt,
    D2). A raise there is the narrowest window in the loop and the one most
    likely to be missed.
    """

    def boom(self: Any, plan: Any, backend: Any) -> Any:
        raise Boom("injected during build_invocation")

    monkeypatch.setattr(adapter_cls, "build_invocation", boom)

    with contract_environment(scenario) as env:
        with pytest.raises(Boom):
            asyncio.run(call(env.service))
        _assert_balanced(env.service, label)


@pytest.mark.parametrize(
    ("label", "adapter_cls", "scenario", "call"), SURFACES, ids=SURFACE_IDS
)
def test_classified_failure_on_every_candidate_releases_everything(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    adapter_cls: type,
    scenario: dict[str, Any],
    call: Any,
) -> None:
    """The retry path: one acquire and one release per attempt, budget spent."""
    attempts = 0

    async def boom(self: Any, plan: Any, invocation: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise OpenAIUpstreamError("injected", status_code=500)

    monkeypatch.setattr(adapter_cls, "invoke", boom)

    with contract_environment(scenario) as env:
        with pytest.raises(Exception) as caught:
            asyncio.run(call(env.service))
        assert not isinstance(caught.value, Boom)
        _assert_balanced(env.service, label)
        assert attempts == 3, f"{label}: expected the 3-row budget, got {attempts}"


@pytest.mark.parametrize(
    ("label", "adapter_cls", "scenario", "call"), SURFACES, ids=SURFACE_IDS
)
def test_admission_release_survives_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    adapter_cls: type,
    scenario: dict[str, Any],
    call: Any,
) -> None:
    """A leaked slot is invisible once and fatal by the fourth request.

    ``max_concurrency`` is set to 1 for the ``default`` source, so a single
    unreleased reservation makes the *next* request raise
    ``SourceCapacityExceeded`` instead of routing. Driving the same failing
    request four times proves the release is in a ``finally``, not on the
    success path.
    """
    from netllm_agent.service import SourceCapacityExceeded

    async def boom(self: Any, plan: Any, invocation: Any) -> Any:
        raise Boom("injected mid-invoke")

    monkeypatch.setattr(adapter_cls, "invoke", boom)

    scenario = {**scenario, "routing": {**scenario["routing"]}}
    scenario["routing"]["sources"] = [{"id": "default", "max_concurrency": 1}]

    with contract_environment(scenario) as env:
        for i in range(4):
            with pytest.raises(Boom) as caught:
                asyncio.run(call(env.service))
            assert not isinstance(caught.value, SourceCapacityExceeded), (
                f"{label}: admission slot leaked by request {i}"
            )
        _assert_balanced(env.service, label)
