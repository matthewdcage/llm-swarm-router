"""Payload adaptation for OpenAI SDK upstream calls."""

from __future__ import annotations

from netllm_sdk_openai.payload import (
    adapt_chat_payload_for_sdk,
    adapt_embeddings_payload_for_sdk,
)


def test_top_k_moves_to_extra_body() -> None:
    payload = {
        "model": "qwen3-next-80b",
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 40,
        "temperature": 0.7,
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert adapted["model"] == "qwen3-next-80b"
    assert adapted["temperature"] == 0.7
    assert "top_k" not in adapted
    assert adapted["extra_body"] == {"top_k": 40}


def test_merges_existing_extra_body() -> None:
    payload = {
        "model": "qwen3-next-80b",
        "messages": [],
        "top_k": 20,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert adapted["extra_body"] == {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_flattens_client_extra_body_only() -> None:
    payload = {
        "model": "qwen3-next-80b",
        "messages": [],
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert "extra_body" not in adapted or adapted["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert "chat_template_kwargs" not in adapted


def test_repeat_penalty_alias() -> None:
    payload = {
        "model": "qwen3-next-80b",
        "messages": [],
        "repeat_penalty": 1.1,
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert adapted["extra_body"] == {"repetition_penalty": 1.1}
    assert "repeat_penalty" not in adapted["extra_body"]


def test_ollama_style_extensions() -> None:
    payload = {
        "model": "gemma4:26b",
        "messages": [],
        "top_k": 64,
        "min_p": 0.05,
        "repeat_penalty": 1.1,
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert adapted["extra_body"] == {
        "top_k": 64,
        "min_p": 0.05,
        "repetition_penalty": 1.1,
    }


def test_sdk_fields_unchanged() -> None:
    payload = {
        "model": "gpt-4",
        "messages": [],
        "max_tokens": 128,
        "top_p": 0.9,
        "presence_penalty": 0.1,
    }
    assert adapt_chat_payload_for_sdk(payload) == payload


def test_non_dict_extra_body_dropped() -> None:
    """F-37: a non-dict extra_body is discarded explicitly, not dead-stored."""
    payload = {
        "model": "qwen3-next-80b",
        "messages": [],
        "extra_body": "not-a-dict",
        "top_k": 40,
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert adapted["extra_body"] == {"top_k": 40}

    no_extensions = adapt_chat_payload_for_sdk(
        {"model": "qwen3-next-80b", "messages": [], "extra_body": [1, 2]}
    )
    assert "extra_body" not in no_extensions


def test_sdk_control_kwargs_stripped_from_chat() -> None:
    """F-42: wire payloads must not steer SDK controls (headers/query/timeout)."""
    payload = {
        "model": "qwen3-next-80b",
        "messages": [],
        "extra_headers": {"X-Evil": "1"},
        "extra_query": {"debug": "1"},
        "timeout": 0.001,
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert "extra_headers" not in adapted
    assert "extra_query" not in adapted
    assert "timeout" not in adapted
    # Stripped entirely: they must not reappear inside extra_body either.
    assert "extra_headers" not in adapted.get("extra_body", {})
    assert "extra_query" not in adapted.get("extra_body", {})


def test_sdk_control_kwargs_stripped_even_when_nested() -> None:
    """F-42: controls smuggled inside client extra_body are also stripped."""
    payload = {
        "model": "qwen3-next-80b",
        "messages": [],
        "extra_body": {"extra_headers": {"X-Evil": "1"}, "top_k": 5},
    }
    adapted = adapt_chat_payload_for_sdk(payload)
    assert "extra_headers" not in adapted
    assert adapted["extra_body"] == {"top_k": 5}


def test_embeddings_extension_fields_move_to_extra_body() -> None:
    """F-35: embeddings payloads get the same extension-field adaptation as chat."""
    payload = {
        "model": "nomic-embed-text",
        "input": "hello",
        "truncate": True,
        "dimensions": 256,
    }
    adapted = adapt_embeddings_payload_for_sdk(payload)
    assert adapted["model"] == "nomic-embed-text"
    assert adapted["dimensions"] == 256
    assert "truncate" not in adapted
    assert adapted["extra_body"] == {"truncate": True}


def test_embeddings_sdk_fields_unchanged() -> None:
    """F-35: typed embeddings params pass through untouched."""
    payload = {
        "model": "text-embedding-3-small",
        "input": ["a", "b"],
        "encoding_format": "float",
        "user": "u1",
    }
    assert adapt_embeddings_payload_for_sdk(payload) == payload


def test_embeddings_control_kwargs_stripped() -> None:
    """F-42: embeddings payloads cannot set SDK controls either."""
    payload = {
        "model": "nomic-embed-text",
        "input": "hi",
        "extra_headers": {"X-Evil": "1"},
        "timeout": 1,
    }
    adapted = adapt_embeddings_payload_for_sdk(payload)
    assert "extra_headers" not in adapted
    assert "timeout" not in adapted
    assert "extra_body" not in adapted
