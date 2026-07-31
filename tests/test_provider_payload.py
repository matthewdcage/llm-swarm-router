"""Provider payload adaptation for diverse client traffic."""

from __future__ import annotations

from netllm_core.models import Backend, BackendHealth
from netllm_core.provider_payload import adapt_chat_completion_payload


def _backend(provider: str, *, peer: bool = False) -> Backend:
    return Backend(
        id="peer:abc" if peer else f"{provider}-1",
        base_url="http://127.0.0.1:8000/v1",
        provider=provider,  # type: ignore[arg-type]
        local=not peer,
        health=BackendHealth(status="online", models=["test-model"]),
    )


def test_vllm_strips_top_k_and_preserves_in_extra_body() -> None:
    payload = {
        "model": "qwen3-next-80b",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
        "top_k": 40,
        "temperature": 0.7,
    }
    adapted = adapt_chat_completion_payload(payload, _backend("vllm"))
    assert "top_k" not in adapted
    assert adapted["temperature"] == 0.7
    assert adapted["extra_body"]["top_k"] == 40


def test_peer_hop_payload_unmodified() -> None:
    payload = {
        "model": "qwen3-next-80b",
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 40,
    }
    adapted = adapt_chat_completion_payload(payload, _backend("custom", peer=True))
    assert adapted is payload
    assert adapted["top_k"] == 40


def test_unknown_provider_drops_unsupported_keys() -> None:
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 10,
    }
    adapted = adapt_chat_completion_payload(payload, _backend("custom"))
    assert "top_k" not in adapted
    assert "extra_body" not in adapted
