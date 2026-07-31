"""Map OpenAI-compatible wire payloads onto the official OpenAI Python SDK."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# SDK per-call *control* kwargs (client-level transport knobs). A wire payload
# must never set these: extra_headers would let a request body inject arbitrary
# upstream HTTP headers, extra_query rewrites the URL, timeout overrides ours.
# They are stripped entirely in normalize_client_payload (F-42) — not routed
# into extra_body — so they never reach the upstream in any form.
_SDK_CONTROL_PARAMS = frozenset({"extra_headers", "extra_query", "timeout"})

# Body parameters accepted by openai.resources.chat.completions.
# AsyncCompletions.create (excluding self and _SDK_CONTROL_PARAMS).
# Drift-checked against the pinned SDK in tests/test_sdk_param_drift.py (F-36).
_SDK_CHAT_PARAMS = frozenset(
    {
        "audio",
        "extra_body",
        "frequency_penalty",
        "function_call",
        "functions",
        "logit_bias",
        "logprobs",
        "max_completion_tokens",
        "max_tokens",
        "messages",
        "metadata",
        "modalities",
        "model",
        "moderation",
        "n",
        "parallel_tool_calls",
        "prediction",
        "presence_penalty",
        "prompt_cache_key",
        "prompt_cache_retention",
        "reasoning_effort",
        "response_format",
        "safety_identifier",
        "seed",
        "service_tier",
        "stop",
        "store",
        "stream",
        "stream_options",
        "temperature",
        "tool_choice",
        "tools",
        "top_logprobs",
        "top_p",
        "user",
        "verbosity",
        "web_search_options",
    }
)

# Body parameters accepted by openai.resources.embeddings.AsyncEmbeddings.create
# (excluding self and _SDK_CONTROL_PARAMS). Drift-checked alongside the chat
# set (F-36).
_SDK_EMBEDDINGS_PARAMS = frozenset(
    {
        "dimensions",
        "encoding_format",
        "extra_body",
        "input",
        "model",
        "user",
    }
)

# Ollama / LM Studio names → vLLM OpenAI-compat field names.
_FIELD_ALIASES: dict[str, str] = {
    "repeat_penalty": "repetition_penalty",
}


def normalize_client_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten client ``extra_body``, strip SDK controls, map field aliases."""
    normalized = dict(payload)

    nested = normalized.pop("extra_body", None)
    if isinstance(nested, dict):
        for key, value in nested.items():
            if key not in normalized:
                normalized[key] = value
            elif key == "chat_template_kwargs" and isinstance(value, dict):
                existing = normalized.get(key)
                if isinstance(existing, dict):
                    normalized[key] = {**value, **existing}
                else:
                    normalized[key] = value
    elif nested is not None:
        # Malformed on the wire (extra_body must be an object): drop it (F-37).
        logger.debug("dropping non-dict extra_body of type %s", type(nested).__name__)

    # Runs after flattening so controls smuggled inside extra_body go too.
    for key in _SDK_CONTROL_PARAMS:
        normalized.pop(key, None)

    for src, dst in _FIELD_ALIASES.items():
        if src in normalized and dst not in normalized:
            normalized[dst] = normalized.pop(src)

    return normalized


def _adapt_payload_for_sdk(
    payload: dict[str, Any], allowed: frozenset[str]
) -> dict[str, Any]:
    """Split fields not typed on the SDK method into ``extra_body``.

    Clients (Ollama, vLLM, LM Studio, Cursor, Buzz agents) often send knobs
    like ``top_k`` that are valid on the wire but not typed on the OpenAI SDK.
    The SDK merges ``extra_body`` into the HTTP JSON; vLLM expects those fields
    at the top level, not nested under a literal ``extra_body`` key.
    """
    if not payload:
        return payload

    payload = normalize_client_payload(payload)
    out: dict[str, Any] = {}
    extensions: dict[str, Any] = {}

    for key, value in payload.items():
        if key in allowed:
            out[key] = value
        else:
            extensions[key] = value

    if not extensions:
        return out

    existing = out.pop("extra_body", None)
    if isinstance(existing, dict):
        merged = {**extensions, **existing}
    else:
        merged = extensions

    out["extra_body"] = merged
    return out


def adapt_chat_payload_for_sdk(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt a wire chat-completions payload for the SDK call."""
    return _adapt_payload_for_sdk(payload, _SDK_CHAT_PARAMS)


def adapt_embeddings_payload_for_sdk(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt a wire embeddings payload for the SDK call (F-35)."""
    return _adapt_payload_for_sdk(payload, _SDK_EMBEDDINGS_PARAMS)
