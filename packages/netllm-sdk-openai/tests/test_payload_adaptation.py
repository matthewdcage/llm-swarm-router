"""Payload adaptation for OpenAI SDK upstream calls."""

from __future__ import annotations

from netllm_sdk_openai.payload import adapt_chat_payload_for_sdk


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
