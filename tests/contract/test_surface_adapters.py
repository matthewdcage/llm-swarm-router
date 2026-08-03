"""SurfaceAdapter conformance (plan-f24-f26.md §1, §3 Phase 6).

The engine's correctness rests on a claim the loop itself cannot check:
that every adapter implements the whole protocol, and that the protocol is
the *only* place surface knowledge lives. These tests check both ends —
every member is implemented by every adapter (no accidental inheritance of
``BaseAdapter``'s ``NotImplementedError``), and the members that differ
really do differ.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from netllm_agent.errors import anthropic_error_body, openai_error_body
from netllm_agent.service.surfaces import (
    ChatAdapter,
    EmbeddingsAdapter,
    MessagesAdapter,
    adapter_for,
)
from netllm_agent.service.surfaces.base import BaseAdapter, SurfaceAdapter
from netllm_agent.taxonomy import Surface
from netllm_core.models import NetllmConfig
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

ADAPTERS = (ChatAdapter, EmbeddingsAdapter, MessagesAdapter)
ADAPTER_IDS = [cls.__name__ for cls in ADAPTERS]

# Every member run_with_failover reaches through. Adding one here without
# implementing it everywhere is the failure mode this file exists for.
PROTOCOL_MEMBERS = (
    "guard",
    "candidates",
    "build_invocation",
    "invoke",
    "extract_usage",
    "restore_model",
    "classify_error",
    "exhaustion_error",
    "wire_error",
    # Phase 7 (streaming). Present on every adapter: the ones with no
    # stream arm inherit BaseAdapter's explicit refusal, which is a
    # different thing from silently not having the member.
    "invoke_stream",
    "extract_stream_usage",
    "restore_stream_line",
    "mid_stream_error_frame",
)


@pytest.fixture
def service() -> Any:
    from netllm_agent.service import AgentService

    return AgentService(NetllmConfig())


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=ADAPTER_IDS)
def test_adapter_implements_every_protocol_member(adapter_cls: type) -> None:
    missing = [name for name in PROTOCOL_MEMBERS if not hasattr(adapter_cls, name)]
    assert not missing, f"{adapter_cls.__name__} is missing {missing}"


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=ADAPTER_IDS)
def test_adapter_inherits_no_unimplemented_base_member(
    adapter_cls: type, service: Any
) -> None:
    """``BaseAdapter`` raises ``NotImplementedError`` for the members it
    deliberately refuses to guess. A concrete adapter must override them."""
    adapter = adapter_cls(service)
    for name in ("legacy_cloud_backends", "wire_error"):
        base_impl = getattr(BaseAdapter, name)
        assert getattr(type(adapter), name) is not base_impl, (
            f"{adapter_cls.__name__}.{name} still inherits BaseAdapter's stub"
        )


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=ADAPTER_IDS)
def test_adapter_satisfies_the_runtime_protocol(
    adapter_cls: type, service: Any
) -> None:
    assert isinstance(adapter_cls(service), SurfaceAdapter)


def test_adapter_for_maps_every_surface(service: Any) -> None:
    """Every Surface member has an adapter — including any added later."""
    expected = {
        Surface.CHAT: ChatAdapter,
        Surface.EMBEDDINGS: EmbeddingsAdapter,
        Surface.MESSAGES: MessagesAdapter,
    }
    assert set(expected) == set(Surface), "a Surface member has no adapter mapping"
    for surface, cls in expected.items():
        assert isinstance(adapter_for(service, surface), cls)


# --- the members that must actually differ ---------------------------------


def test_error_classification_is_per_dialect(service: Any) -> None:
    """Messages retries across both dialects (its translated arm reaches
    openai-format rows); the OpenAI surfaces know only their own."""
    oai = OpenAIUpstreamError("boom", status_code=500)
    ant = AnthropicUpstreamError("boom", status_code=500)

    for cls in (ChatAdapter, EmbeddingsAdapter):
        adapter = cls(service)
        assert adapter.classify_error(oai)
        assert not adapter.classify_error(ant)

    messages = MessagesAdapter(service)
    assert messages.classify_error(oai)
    assert messages.classify_error(ant)

    # Nothing classifies a bug in the router as a sick backend.
    for cls in ADAPTERS:
        assert not cls(service).classify_error(TypeError("signature drift"))


def test_wire_error_is_the_surface_native_envelope(service: Any) -> None:
    """F-38: an OpenAI client and an Anthropic client parse different shapes.

    Held on the adapter (not only in ``errors.install_error_handlers``,
    which keys on the request path) because Phase 7's mid-stream error
    frames need the same envelope from inside the loop, where there is no
    request object to inspect.
    """
    for cls in (ChatAdapter, EmbeddingsAdapter):
        assert cls(service).wire_error(429, "slow down") == openai_error_body(
            429, "slow down"
        )
    body = MessagesAdapter(service).wire_error(429, "slow down")
    assert body == anthropic_error_body(429, "slow down")
    assert body["type"] == "error"


@pytest.mark.parametrize(
    ("adapter_cls", "bad_model", "expected_exc"),
    [
        (ChatAdapter, "bge-m3", OpenAIUpstreamError),
        (EmbeddingsAdapter, "farm-chat", OpenAIUpstreamError),
        (MessagesAdapter, "bge-m3", AnthropicUpstreamError),
    ],
    ids=ADAPTER_IDS,
)
def test_guard_rejects_the_wrong_capability_in_the_right_dialect(
    service: Any, adapter_cls: type, bad_model: str, expected_exc: type
) -> None:
    with pytest.raises(expected_exc) as caught:
        adapter_cls(service).guard(bad_model, bad_model)
    assert caught.value.status_code == 400


def test_adapters_hold_no_per_request_state(service: Any) -> None:
    """Frozen dataclasses over the service: constructing one per request (or
    twice — ``build_request_plan`` makes one for the guard) must be free and
    side-effect-free."""
    for cls in ADAPTERS:
        adapter = cls(service)
        assert adapter == cls(service)
        with pytest.raises(Exception):
            adapter.log_label = "mutated"  # type: ignore[misc]


def test_invoke_signature_is_uniform() -> None:
    """The loop calls ``invoke(plan, invocation)``; a drifting signature
    would only fail at runtime, on one surface, under load."""
    for cls in ADAPTERS:
        params = list(inspect.signature(cls.invoke).parameters)
        assert params == ["self", "plan", "invocation"], f"{cls.__name__}: {params}"
        assert inspect.iscoroutinefunction(cls.invoke)


# ---------------------------------------------------------------------------
# Streaming members (plan §3 Phase 7)
# ---------------------------------------------------------------------------


def test_only_the_streaming_surfaces_have_a_streaming_arm(service: Any) -> None:
    """``/v1/embeddings`` has no stream arm and must say so explicitly."""
    for adapter_cls in (ChatAdapter, MessagesAdapter):
        assert adapter_cls.invoke_stream is not BaseAdapter.invoke_stream, (
            f"{adapter_cls.__name__} inherits BaseAdapter's streaming stub"
        )
    assert EmbeddingsAdapter.invoke_stream is BaseAdapter.invoke_stream
    with pytest.raises(NotImplementedError):
        EmbeddingsAdapter(service).invoke_stream(None, None)  # type: ignore[arg-type]


def test_mid_stream_error_frames_are_dialect_native_and_terminated(
    service: Any,
) -> None:
    """Both dialects end a failed stream; neither leaves the client hanging.

    The terminator is the divergence D9 closed: C-S has always sent
    ``data: [DONE]``, M-S sent the error event and simply stopped.
    """
    exc = RuntimeError("upstream died")

    chat = ChatAdapter(service).mid_stream_error_frame(exc)
    assert chat[0].startswith("data: {")
    assert "upstream died" in chat[0]
    assert chat[-1] == "data: [DONE]\n\n"

    messages = MessagesAdapter(service).mid_stream_error_frame(exc)
    assert messages[0].startswith("event: error\ndata: {")
    assert "upstream died" in messages[0]
    assert messages[-1].startswith("event: message_stop")
    assert '"type": "message_stop"' in messages[-1]


def test_stream_restore_is_a_no_op_when_the_name_already_matches(
    service: Any,
) -> None:
    """Restore must never re-serialize a frame it agrees with.

    Byte-identity on the untouched frames is what let Phase 7 change only
    the five D9-annotated vectors: any gratuitous JSON round-trip would
    have moved the whole M-S streaming corpus.
    """
    from netllm_agent.service.surfaces.base import (
        restore_anthropic_sse_chunk,
        restore_openai_sse_chunk,
    )

    frame = 'data: {"type":"message_start","message":{"model":"canon","id":"m1"}}\n\n'
    assert restore_anthropic_sse_chunk(frame, "canon") == frame
    assert restore_anthropic_sse_chunk("event: message_stop\n\n", "canon") == (
        "event: message_stop\n\n"
    )
    assert '"model": "canon"' in restore_anthropic_sse_chunk(
        'data: {"type":"message_start","message":{"model":"served-a"}}\n\n', "canon"
    )
    assert restore_openai_sse_chunk("data: [DONE]\n\n", "canon") == "data: [DONE]\n\n"
