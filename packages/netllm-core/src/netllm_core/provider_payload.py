"""Provider-aware adaptation of client payloads before upstream calls.

Diverse clients (Cursor, Continue, custom harnesses) send sampling and
tooling fields that are not part of the OpenAI Python SDK's
``chat.completions.create`` signature. Passing them through verbatim makes
the SDK raise before the request ever reaches vLLM, Ollama, or oMLX.

The adapter keeps SDK-known keys and, for providers that accept extra
sampling params in the JSON body, moves stripped extensions into
``extra_body`` so features survive where the upstream supports them.
"""

from __future__ import annotations

from typing import Any

from netllm_core.models import Backend

# OpenAI Python SDK chat.completions.create params (bundled SDK snapshot).
# Update when netllm-sdk-openai bumps its openai pin — do not import openai here.
_SDK_CHAT_CREATE_PARAMS = frozenset(
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

_SDK_EMBEDDINGS_CREATE_PARAMS = frozenset(
    {
        "dimensions",
        "encoding_format",
        "extra_body",
        "extra_headers",
        "extra_query",
        "input",
        "model",
        "timeout",
        "user",
    }
)

# Sampling / client extensions commonly sent by editors and local runtimes
# but absent from the OpenAI SDK signature.
_CLIENT_EXTENSION_KEYS = frozenset(
    {
        "top_k",
        "min_p",
        "top_a",
        "tfs_z",
        "typical_p",
        "repetition_penalty",
        "repeat_penalty",
        "presence_penalty_scale",
        "mirostat",
        "mirostat_tau",
        "mirostat_eta",
        "penalty_alpha",
        "guidance_scale",
        "num_ctx",
        "num_predict",
        "num_keep",
        "options",
    }
)

# Providers that merge extra_body into the upstream HTTP JSON body.
_EXTRA_BODY_PROVIDERS = frozenset({"vllm", "ollama", "omlx", "lmstudio"})


def adapt_chat_completion_payload(
    payload: dict[str, Any],
    backend: Backend,
    *,
    is_agent_hop: bool = False,
) -> dict[str, Any]:
    """Return a payload safe to pass to ``OpenAIUpstream.chat_completion``.

    ``is_agent_hop`` is True when this call targets another netllm agent
    (``peer:`` row). Hop targets receive the caller payload unchanged;
    the terminating agent adapts on the final provider hop.
    """
    if is_agent_hop or backend.id.startswith("peer:"):
        return payload

    allowed = _SDK_CHAT_CREATE_PARAMS
    out: dict[str, Any] = {}
    stripped: dict[str, Any] = {}
    for key, value in payload.items():
        if key in allowed:
            out[key] = value
        else:
            stripped[key] = value

    if not stripped:
        return out

    if backend.provider in _EXTRA_BODY_PROVIDERS:
        extra = dict(out.get("extra_body") or {})
        extra.update(stripped)
        out["extra_body"] = extra
        return out

    # Unknown provider: drop unsupported keys rather than failing the SDK.
    return out


def adapt_embeddings_payload(
    payload: dict[str, Any],
    backend: Backend,
    *,
    is_agent_hop: bool = False,
) -> dict[str, Any]:
    """Strip SDK-unknown keys from embeddings requests."""
    if is_agent_hop or backend.id.startswith("peer:"):
        return payload
    allowed = _SDK_EMBEDDINGS_CREATE_PARAMS
    return {k: v for k, v in payload.items() if k in allowed}
