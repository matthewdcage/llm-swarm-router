"""Map OpenAI-compatible chat payloads onto the official OpenAI Python SDK."""

from __future__ import annotations

from typing import Any

# Parameters accepted by openai.resources.chat.Completions.create (excluding self).
_SDK_CHAT_PARAMS = frozenset(
    {
        "audio",
        "extra_body",
        "extra_headers",
        "extra_query",
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
        "timeout",
        "tool_choice",
        "tools",
        "top_logprobs",
        "top_p",
        "user",
        "verbosity",
        "web_search_options",
    }
)

# Ollama / LM Studio names → vLLM OpenAI-compat field names.
_FIELD_ALIASES: dict[str, str] = {
    "repeat_penalty": "repetition_penalty",
}


def normalize_client_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten client ``extra_body`` and map cross-provider field aliases."""
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

    for src, dst in _FIELD_ALIASES.items():
        if src in normalized and dst not in normalized:
            normalized[dst] = normalized.pop(src)

    return normalized


def adapt_chat_payload_for_sdk(payload: dict[str, Any]) -> dict[str, Any]:
    """Split provider-specific fields into ``extra_body`` for upstream SDK calls.

    Clients (Ollama, vLLM, LM Studio, Cursor, Buzz agents) often send sampling
    knobs like ``top_k`` that are valid on the wire but not typed on the OpenAI
    SDK. The SDK merges ``extra_body`` into the HTTP JSON; vLLM expects those
    fields at the top level, not nested under a literal ``extra_body`` key.
    """
    if not payload:
        return payload

    payload = normalize_client_payload(payload)
    out: dict[str, Any] = {}
    extensions: dict[str, Any] = {}

    for key, value in payload.items():
        if key in _SDK_CHAT_PARAMS:
            out[key] = value
        else:
            extensions[key] = value

    if not extensions:
        return out

    existing = out.pop("extra_body", None)
    if isinstance(existing, dict):
        merged = {**extensions, **existing}
    elif existing is None:
        merged = extensions
    else:
        merged = extensions
        out["extra_body"] = existing

    out["extra_body"] = merged
    return out
