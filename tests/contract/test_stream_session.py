"""Phase 7 lifecycle assertions — ``open_stream`` / ``StreamSession``.

The vector corpus pins what a client *sees*. This file pins the three
things it cannot see, all of which are about a stream that does not end
normally, and all of which were hand-copied fragments in two loops before
``StreamSession`` existed:

- **the pre-flight shape is structural.** ``open_stream`` returns only
  after the upstream produced its first event, so anything that can still
  fail recoverably fails while the route handler is still on the stack.
  The sharpest case is behavior-matrix **D15**: a ``TypeError``-class
  failure during connect (an SDK signature drift — the exact shape of the
  F-30 bug) is not any adapter's classified error, so it must escape as a
  *mapped HTTP status*. It must never become the F-32 failure mode, a 200
  whose body then aborts.
- **no replay after the first byte.** Once bytes are on the wire a second
  upstream response cannot be spliced into the same SSE body, so a
  classified mid-stream failure ends the request with the surface's error
  frame and touches exactly one backend.
- **release is unconditional.** Clean end, mid-stream failure, client
  disconnect (``GeneratorExit``) and unclassified exception all have to
  return the pool slot, the in-flight gauge and the per-source admission
  slot — and exactly once, so a double release cannot drive a counter
  negative either.

Asserted per stream surface (C-S, M-S, RESP-S), because "it holds for
chat" was the shape of the original defect.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from drivers import contract_environment
from farm import midstream_drop, ok, ok_stream
from netllm_agent.service.engine import StreamSession, open_stream
from netllm_agent.service.surfaces import ChatAdapter, MessagesAdapter
from netllm_agent.taxonomy import Surface

CHAT = "farm-chat"
CLAUDE = "claude-farm"
USER = [{"role": "user", "content": "hi"}]


class Boom(TypeError):
    """D15's class: an SDK signature drift, not an upstream error.

    ``TypeError`` on purpose — F-30 was literally ``messages_stream``
    passing ``stream=`` to ``.stream()``, and the M-S loop's
    ``except (AnthropicUpstreamError, OpenAIUpstreamError)`` let it straight
    through into the F-32 dead zone, where it surfaced as a 200 with an
    aborted body.
    """


def _scenario(
    scripts: dict[str, list[dict[str, Any]]], api_format: str = "openai"
) -> dict[str, Any]:
    model = CLAUDE if api_format == "anthropic" else CHAT
    return {
        "backends": [
            {
                "name": name,
                "api_format": api_format,
                "models": [model],
                "script": script,
            }
            for name, script in scripts.items()
        ],
        "routing": {"default_strategy": "failover"},
    }


def _chat_scenario(*scripts: list[dict[str, Any]]) -> dict[str, Any]:
    return _scenario(dict(zip(("alpha", "beta", "gamma"), scripts)))


def _messages_scenario(*scripts: list[dict[str, Any]]) -> dict[str, Any]:
    return _scenario(
        dict(zip(("ant1", "ant2", "ant3"), scripts)), api_format="anthropic"
    )


# (id, contract path, scenario factory, service stream method, body)
STREAM_SURFACES = [
    (
        "chat-s",
        "chat_s",
        _chat_scenario,
        "proxy_chat_completion_stream",
        {"model": CHAT, "messages": USER, "stream": True},
    ),
    (
        "messages-s",
        "messages_s",
        _messages_scenario,
        "proxy_messages_stream",
        {"model": CLAUDE, "messages": USER, "max_tokens": 16, "stream": True},
    ),
    (
        "responses-s",
        "responses_s",
        _chat_scenario,
        "proxy_responses_stream",
        {"model": CHAT, "input": "hi", "stream": True},
    ),
]
SURFACE_IDS = [case[0] for case in STREAM_SURFACES]


def _assert_balanced(service: Any, label: str) -> None:
    backend_in_flight = {b.base_url: b.in_flight for b in service.pool.backends}
    assert all(v == 0 for v in backend_in_flight.values()), (
        f"{label}: leaked pool reservation {backend_in_flight}"
    )
    assert all(v == 0 for v in dict(service._source_in_flight).values()), (
        f"{label}: leaked admission slot {dict(service._source_in_flight)}"
    )


async def _consume(gen: Any, limit: int | None = None) -> list[str]:
    out: list[str] = []
    async for chunk in gen:
        out.append(chunk)
        if limit is not None and len(out) >= limit:
            await gen.aclose()
            break
    return out


# ---------------------------------------------------------------------------
# D15 adversarial pin — an unclassified connect failure is an HTTP status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "path", "scenario_factory", "method", "body"),
    STREAM_SURFACES,
    ids=SURFACE_IDS,
)
def test_unclassified_connect_failure_is_a_mapped_status_not_a_200(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    path: str,
    scenario_factory: Any,
    method: str,
    body: dict[str, Any],
) -> None:
    """[D15] The whole point of ``open_stream``'s shape.

    A ``TypeError`` raised while connecting is not classified, so it is not
    a candidate failure: it propagates out of ``open_stream``, which is
    still being awaited inside the route handler. The client gets a real
    status code with a real body — never ``200`` followed by an aborted
    stream, which is what this same defect produced before F-32/Phase 7.
    """
    for adapter_cls in (ChatAdapter, MessagesAdapter):
        monkeypatch.setattr(
            adapter_cls,
            "invoke_stream",
            lambda self, plan, invocation: (_ for _ in ()).throw(
                Boom("SDK signature drift")
            ),
        )

    with contract_environment(scenario_factory([ok_stream()], [ok_stream()])) as env:
        result = env.drive({"path": path, "body": body})
        assert result.status != 200, (
            f"{label}: an unclassified connect failure surfaced as 200 — "
            "this is the F-32/D15 dead zone reopening"
        )
        assert result.status >= 500
        _assert_balanced(env.service, label)


@pytest.mark.parametrize(
    ("label", "path", "scenario_factory", "method", "body"),
    STREAM_SURFACES,
    ids=SURFACE_IDS,
)
def test_unclassified_connect_failure_touches_one_backend_only(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    path: str,
    scenario_factory: Any,
    method: str,
    body: dict[str, Any],
) -> None:
    """An unclassified error is a bug in *us*, not a sick backend, so the
    walk stops rather than burning the whole attempt budget on it."""
    calls: list[str] = []

    def boom(self: Any, plan: Any, invocation: Any) -> Any:
        calls.append(invocation.backend.base_url)
        raise Boom("SDK signature drift")

    for adapter_cls in (ChatAdapter, MessagesAdapter):
        monkeypatch.setattr(adapter_cls, "invoke_stream", boom)

    with contract_environment(
        scenario_factory([ok_stream()], [ok_stream()], [ok_stream()])
    ) as env:
        env.drive({"path": path, "body": body})
        assert len(calls) == 1, f"{label}: retried an unclassified failure"
        _assert_balanced(env.service, label)


# ---------------------------------------------------------------------------
# No replay once a byte is out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "path", "scenario_factory", "method", "body"),
    STREAM_SURFACES,
    ids=SURFACE_IDS,
)
def test_failure_after_the_first_byte_never_retries(
    label: str,
    path: str,
    scenario_factory: Any,
    method: str,
    body: dict[str, Any],
) -> None:
    """A healthy second candidate must stay untouched once bytes are out."""
    scenario = scenario_factory([midstream_drop(2)], [ok_stream()])
    with contract_environment(scenario) as env:
        result = env.drive({"path": path, "body": body})
        assert result.status == 200
        served = [c["backend"] for c in env.farm.inference_calls()]
        assert served == ["alpha"] or served == ["ant1"], (
            f"{label}: replayed a second upstream response into one SSE body "
            f"(upstream calls: {served})"
        )
        _assert_balanced(env.service, label)


def test_pre_first_byte_failure_does_retry() -> None:
    """The mirror image, so the rule above is not just "never retry".

    Nothing has reached the client before the first event, so the failover
    walk is free — and the client sees a single clean stream.
    """
    scenario = _chat_scenario([midstream_drop(0)], [ok_stream()])
    with contract_environment(scenario) as env:
        result = env.drive(
            {"path": "chat_s", "body": {"model": CHAT, "messages": USER}}
        )
        assert result.status == 200
        assert [c["backend"] for c in env.farm.inference_calls()] == ["alpha", "beta"]
        assert not any("error" in frame for frame in result.frames or [])
        _assert_balanced(env.service, "chat-s")


# ---------------------------------------------------------------------------
# Release: disconnect, completion, and idempotence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "path", "scenario_factory", "method", "body"),
    STREAM_SURFACES,
    ids=SURFACE_IDS,
)
def test_client_disconnect_releases_everything(
    label: str,
    path: str,
    scenario_factory: Any,
    method: str,
    body: dict[str, Any],
) -> None:
    """``GeneratorExit`` at the suspended yield still runs the release.

    Driven against the service generator directly: ``TestClient`` buffers a
    streaming response to completion, so a real mid-body disconnect is not
    expressible through the route.
    """
    scenario = scenario_factory([ok_stream(["a", "b", "c", "d"])], [ok_stream()])
    with contract_environment(scenario) as env:
        gen = getattr(env.service, method)(dict(body))
        got = asyncio.run(_consume(gen, limit=2))
        assert len(got) == 2
        _assert_balanced(env.service, label)


@pytest.mark.parametrize(
    ("label", "path", "scenario_factory", "method", "body"),
    STREAM_SURFACES,
    ids=SURFACE_IDS,
)
def test_completed_stream_releases_everything(
    label: str,
    path: str,
    scenario_factory: Any,
    method: str,
    body: dict[str, Any],
) -> None:
    scenario = scenario_factory([ok_stream()], [ok_stream()])
    with contract_environment(scenario) as env:
        gen = getattr(env.service, method)(dict(body))
        assert asyncio.run(_consume(gen))
        _assert_balanced(env.service, label)


def test_release_is_idempotent() -> None:
    """Two releases must not drive the ledgers negative.

    ``_pump``'s ``finally`` and an explicit ``release()`` can both fire on
    the disconnect path; the guard is what keeps in-flight from going to
    -1, which the pool would then read as "capacity available".
    """
    scenario = _chat_scenario([ok_stream(["a", "b"])], [ok_stream()])
    with contract_environment(scenario) as env:

        async def run() -> StreamSession:
            plan = env.service.build_request_plan(
                {"model": CHAT, "messages": USER, "stream": True},
                None,
                surface=Surface.CHAT,
            )
            session = await open_stream(ChatAdapter(env.service), plan)
            assert isinstance(session, StreamSession)
            # A session exists, so a backend is held right now.
            assert session.backend.in_flight == 1
            session.release()
            session.release()
            return session

        session = asyncio.run(run())
        assert session.backend.in_flight == 0
        _assert_balanced(env.service, "chat-s")


def test_open_stream_returns_only_after_the_upstream_connected() -> None:
    """The structural claim, asserted directly.

    By the time ``open_stream``'s coroutine resolves, the farm has already
    served the request and the first event is in hand — which is why every
    recoverable failure is still an HTTP status at that moment.
    """
    scenario = _chat_scenario([ok_stream()], [ok_stream()])
    with contract_environment(scenario) as env:

        async def run() -> tuple[Any, list[str]]:
            plan = env.service.build_request_plan(
                {"model": CHAT, "messages": USER, "stream": True},
                None,
                surface=Surface.CHAT,
            )
            session = await open_stream(ChatAdapter(env.service), plan)
            calls_at_connect = [c["backend"] for c in env.farm.inference_calls()]
            chunks = [chunk async for chunk in session]
            return chunks, calls_at_connect

        chunks, calls_at_connect = asyncio.run(run())
        assert calls_at_connect == ["alpha"], (
            "open_stream returned before the upstream was reached"
        )
        assert chunks and chunks[-1] == "data: [DONE]\n\n"
        _assert_balanced(env.service, "chat-s")


# ---------------------------------------------------------------------------
# Exhaustion: no session, admission still released
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "path", "scenario_factory", "method", "body"),
    STREAM_SURFACES,
    ids=SURFACE_IDS,
)
def test_every_candidate_failing_releases_everything(
    label: str,
    path: str,
    scenario_factory: Any,
    method: str,
    body: dict[str, Any],
) -> None:
    """open_stream never returns a session here, so *it* owns the release."""
    fail = [{"behavior": "http", "status": 500}] * 4
    with contract_environment(scenario_factory(fail, fail)) as env:
        result = env.drive({"path": path, "body": body})
        assert result.status >= 400
        _assert_balanced(env.service, label)


def test_usage_absent_is_tolerated() -> None:
    """No ``stream_options.include_usage``: still a full success, zero
    tokens. Usage capture must not become a precondition of accounting."""
    scenario = _chat_scenario([ok_stream(usage_in_final=False)], [ok()])
    with contract_environment(scenario) as env:
        before = env.service._request_count
        result = env.drive(
            {"path": "chat_s", "body": {"model": CHAT, "messages": USER}}
        )
        assert result.status == 200
        assert env.service._request_count == before + 1
        _assert_balanced(env.service, "chat-s")
