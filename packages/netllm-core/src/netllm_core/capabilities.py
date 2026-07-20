"""Heuristic model capability classification from model IDs.

Local providers (oMLX, Ollama, LM Studio, vLLM) list every loaded model on
``GET /v1/models`` — chat models next to embedding encoders, TTS voices, and
rerankers. Routing a chat completion to an encoder fails upstream with
confusing errors ("tokenizer.chat_template is not set"), so the router
classifies models by name and refuses obvious mismatches early.

Classification is intentionally conservative: anything unrecognized counts
as ``chat`` so unusual names keep routing exactly as before.
"""

from __future__ import annotations

import re
from typing import Literal

ModelCapability = Literal["chat", "embedding", "audio", "rerank", "other"]

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Encoder / embedding families that never serve chat completions.
_EMBEDDING_TOKENS = frozenset(
    {
        "bge",
        "gte",
        "e5",
        "minilm",
        "bert",
        "modernbert",
        "colbert",
        "splade",
    }
)
_AUDIO_TOKENS = frozenset(
    {
        "whisper",
        "tts",
        "snac",
        "speech",
        "audio",
        "parakeet",
        "kokoro",
        "bark",
        "voice",
        "musicgen",
    }
)
# Tools / converters that are not inference chat models.
_OTHER_TOKENS = frozenset({"markitdown", "ocr"})


def model_capability(model_id: str) -> ModelCapability:
    """Best-effort capability for a served model ID.

    Unknown names default to ``chat`` — never block routing on a guess.
    """
    name = model_id.casefold()
    tokens = {t for t in _TOKEN_SPLIT.split(name) if t}
    if "rerank" in name:
        return "rerank"
    if "embed" in name or tokens & _EMBEDDING_TOKENS:
        return "embedding"
    if tokens & _AUDIO_TOKENS:
        return "audio"
    if tokens & _OTHER_TOKENS:
        return "other"
    return "chat"
