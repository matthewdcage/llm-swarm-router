"""Model capability classification heuristics."""

from __future__ import annotations

import pytest
from netllm_core.capabilities import is_chat_capable, model_capability


@pytest.mark.parametrize(
    "model_id",
    [
        "nomic-embed-text",
        "bge-m3",
        "mxbai-embed-large",
        "embeddinggemma",
        "text-embedding-3-small",
        "mlx-community--nomicai-modernbert-embed-base-bf16",
        "mlx-community--answerdotai-ModernBERT-base-4bit",
        "snowflake-arctic-embed",
        "e5-mistral-7b-instruct",
        "all-MiniLM-L6-v2",
    ],
)
def test_embedding_models_classified(model_id: str) -> None:
    assert model_capability(model_id) == "embedding"
    assert not is_chat_capable(model_id)


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("mlx-community--snac_24khz", "audio"),
        ("whisper-large-v3", "audio"),
        ("kokoro-82M", "audio"),
        ("bge-reranker-v2-m3", "rerank"),
        ("MarkItDown", "other"),
    ],
)
def test_non_chat_models_classified(model_id: str, expected: str) -> None:
    assert model_capability(model_id) == expected


@pytest.mark.parametrize(
    "model_id",
    [
        "llama3:8b-instruct-q4_K_M",
        "gemma-4-26B-A4B-it-assistant-4bit",
        "qwen2.5-coder",
        "gpt-4o-mini",
        "claude-sonnet-4-5",
        "Meta-Llama-3-8B-Instruct",
        "roberta-chat-tuned",  # 'roberta' must not match the 'bert' token
        "some-totally-unknown-model",
    ],
)
def test_chat_and_unknown_models_default_to_chat(model_id: str) -> None:
    assert model_capability(model_id) == "chat"
    assert is_chat_capable(model_id)
