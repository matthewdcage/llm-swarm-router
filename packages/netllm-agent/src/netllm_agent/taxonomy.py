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

__all__ = [
    "Surface",
    "SurfaceSpec",
    "SURFACE_SPECS",
    "spec_for",
    "ExhaustionContext",
    "exhaustion_error",
    "model_not_found_error",
]


class Surface(StrEnum):
    """Dialect a request is being served in, for taxonomy purposes."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    MESSAGES = "messages"


@dataclass(frozen=True)
class SurfaceSpec:
    """Per-surface facts that used to be `Surface` branches out in the tree.

    Axis C's rule (PROGRAM.md §3) is that a `Surface` branch may exist only
    on the outside of the failover loop, in the adapter package. Four had
    escaped into `candidates.py`, `request_plan.py`, `policy.py` and this
    module -- each a place a fifth surface could be silently forgotten,
    because nothing enumerates them.

    They live *here*, beside `Surface` itself, rather than on the adapters:
    `surfaces/base.py` imports `CandidateSchedule` from `candidates.py`, so
    `candidates.py` importing the adapters would be a cycle. The adapters
    expose the same values as protocol members, so the seam is still the
    adapter from the loop's point of view; this is where the values are
    *stated once*.
    """

    surface: Surface

    excluded_api_formats: frozenset[str] = frozenset()
    """Wire dialects this surface's strategy loop must never select (D8)."""

    classifier_api_format: str = "openai"
    """Dialect the scenario classifier and routing policy see (D14)."""

    reads_anthropic_credentials: bool = False
    """Whether a request on this surface carries an `x-api-key` Anthropic
    credential that participates in routing (`policy.py`'s header read)."""

    missing_credential_message: str = ""
    """What "nothing matched, and you gave me no key" means on this surface.

    D11: Messages reads exhaustion-with-no-key as a 401 telling the caller to
    supply a cloud credential, where every OpenAI surface answers
    404-model-not-found. Empty means the surface has no such reading and falls
    through to model-not-found."""

    exhaustion_message: str = ""
    """What exhaustion means when a credential *was* supplied. Empty means
    model-not-found, which is the OpenAI surfaces' answer."""


SURFACE_SPECS: dict[Surface, SurfaceSpec] = {
    # CHAT excludes nothing: it has always been willing to select an
    # anthropic-format row and talk OpenAI to it (`_openai_upstream` is used
    # for every row regardless of `api_format`). Preserved verbatim, divergent
    # or not.
    Surface.CHAT: SurfaceSpec(surface=Surface.CHAT),
    # EMBEDDINGS excludes anthropic because the Anthropic API has no
    # embeddings endpoint -- those rows are unservable, full stop, with no
    # fallback tier to catch them. It still classifies as "openai" (D14).
    Surface.EMBEDDINGS: SurfaceSpec(
        surface=Surface.EMBEDDINGS,
        excluded_api_formats=frozenset({"anthropic"}),
    ),
    # MESSAGES excludes anthropic from *selection* only: those rows are not
    # unservable, they are deferred into the ordered fallback tier so the
    # cloud never shadows the local mesh in a rotation.
    Surface.MESSAGES: SurfaceSpec(
        surface=Surface.MESSAGES,
        excluded_api_formats=frozenset({"anthropic"}),
        classifier_api_format="anthropic",
        reads_anthropic_credentials=True,
        missing_credential_message=(
            "ANTHROPIC_API_KEY required for cloud Messages API"
        ),
        exhaustion_message="No healthy backends available for model",
    ),
}


def spec_for(surface: Surface) -> SurfaceSpec:
    """The spec for a surface. KeyError is deliberate: a new `Surface` member
    with no spec must fail loudly at first use rather than inherit defaults
    that happen to be the OpenAI ones."""
    return SURFACE_SPECS[surface]


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
    spec = spec_for(context.surface)
    # D11 as declared data rather than a branch: a surface either has a
    # reading of "exhausted with no credential" or it does not, and a new
    # surface has to answer that on its spec instead of inheriting whichever
    # side of an `is Surface.MESSAGES` test it happens to land on.
    if spec.exhaustion_message:
        if spec.missing_credential_message and not context.api_key:
            return AnthropicUpstreamError(
                spec.missing_credential_message, status_code=401
            )
        return AnthropicUpstreamError(spec.exhaustion_message)
    return model_not_found_error(
        context.pool, context.requested_model, capability=context.capability
    )
