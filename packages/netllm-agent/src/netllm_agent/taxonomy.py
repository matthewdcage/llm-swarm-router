"""Error taxonomy — the single place a request that ran out of candidates
becomes an exception (F-24/F-26 plan §3 Phase 3).

Two concerns live here:

- :func:`exhaustion_error` — every failover loop ends the same way: hand
  back the last upstream error if there was one, otherwise synthesize the
  surface's "nothing could serve this" error. The *outcomes* differ per
  surface (the OpenAI surfaces answer 404 with a catalog hint, the
  Messages surface answers a keyless 401 or a status-less "no healthy
  backends", D11) and the contract vectors pin that split; this module
  reproduces it verbatim instead of leaving one copy per loop.
- :data:`Surface` — the taxonomy's view of which dialect a request is
  being served in. ``RESPONSES`` is deliberately absent: the Responses
  bridge delegates to the chat path and inherits ``Surface.CHAT``, the
  same way the behavior matrix treats it.

The HTTP *envelope* (OpenAI vs Anthropic JSON shape) is not decided here
— that is ``errors.install_error_handlers`` (F-38), keyed on the request
path. This module only decides the exception type, status, and message.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from netllm_core.pool import RouterPool
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

__all__ = ["Surface", "ExhaustionContext", "exhaustion_error", "model_not_found_error"]


class Surface(StrEnum):
    """Dialect a request is being served in, for taxonomy purposes."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    MESSAGES = "messages"


@dataclass(frozen=True)
class ExhaustionContext:
    """Everything the taxonomy needs to describe an exhausted request.

    Stands in for the ``RequestPlan`` that plan §1 introduces later; the
    call signature (``context, last_error``) is already the target one.
    """

    surface: Surface
    pool: RouterPool
    requested_model: str
    capability: str | None = None
    # Messages only: the resolved Anthropic key, "" when the request had
    # neither an ``x-api-key`` header nor ``ANTHROPIC_API_KEY``.
    api_key: str = ""


def model_not_found_error(
    pool: RouterPool, model: str, *, capability: str | None = None
) -> OpenAIUpstreamError:
    """The OpenAI surfaces' exhaustion outcome: 404 with a catalog hint.

    Degrades to a status-less error (route-mapped to 502) when the pool
    holds no backends at all — with nothing discovered, "model not found"
    would point the caller at the wrong problem.
    """
    if not pool.backends:
        return OpenAIUpstreamError("No healthy backends available for model")
    known = pool.known_models(capability=capability) if capability else []
    if not known:
        known = pool.known_models()
    listing = ", ".join(known) if known else "none discovered yet"
    return OpenAIUpstreamError(
        f"Model '{model}' not found on any backend. "
        f"Known models: {listing}. "
        "Map provider-specific names with [routing.model_aliases], or "
        "add the host to a [routing.model_pools] entry to accept any "
        "request name.",
        status_code=404,
    )


def exhaustion_error(
    context: ExhaustionContext, last_error: Exception | None
) -> Exception:
    """The exception a failover loop raises once no candidate is left.

    Returns (never raises) so callers keep their own ``raise`` site and
    traceback. An upstream error always wins: exhaustion-after-failure is
    reported as that failure, on every surface.
    """
    if last_error is not None:
        return last_error
    if context.surface is Surface.MESSAGES:
        # D11: the Messages surface reads "nothing matched" as "you did
        # not give me a cloud key" and answers 401, where every OpenAI
        # surface answers 404-model-not-found.
        if not context.api_key:
            return AnthropicUpstreamError(
                "ANTHROPIC_API_KEY required for cloud Messages API",
                status_code=401,
            )
        return AnthropicUpstreamError("No healthy backends available for model")
    return model_not_found_error(
        context.pool, context.requested_model, capability=context.capability
    )
