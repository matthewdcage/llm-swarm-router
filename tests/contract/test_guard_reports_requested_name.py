"""F-57: a capability rejection must echo the name the caller sent.

The guard runs after the source ``model_rewrites`` / scenario-model chain, so
before this fix its 400 body quoted the *rewritten* upstream id. An operator
who maps a public name onto an internal one had that internal id handed back
to their users.

Classification still uses the rewritten name — that is the model that would
actually run, so it is what determines capability. Only the reported name
changes.
"""

from __future__ import annotations

import pytest
from netllm_agent.service.policy import PolicyMixin
from netllm_sdk_anthropic.client import AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstreamError

# Rewritten name classifies as an embedding model; requested name is what the
# caller typed. The two must not be confused in either direction.
INTERNAL = "internal-bge-embed-v2"
PUBLIC = "acme-fast-model"


def test_chat_guard_reports_requested_not_rewritten_name() -> None:
    with pytest.raises(OpenAIUpstreamError) as exc:
        PolicyMixin._reject_non_chat_model(INTERNAL, PUBLIC)
    body = str(exc.value)
    assert PUBLIC in body
    assert INTERNAL not in body, "rewritten upstream id leaked to the caller"
    # Classification still came from the rewritten name.
    assert "capability: embedding" in body
    assert exc.value.status_code == 400


def test_messages_guard_reports_requested_not_rewritten_name() -> None:
    with pytest.raises(AnthropicUpstreamError) as exc:
        PolicyMixin._reject_non_chat_messages_model(INTERNAL, PUBLIC)
    body = str(exc.value)
    assert PUBLIC in body
    assert INTERNAL not in body
    assert exc.value.status_code == 400


def test_embeddings_guard_reports_requested_not_rewritten_name() -> None:
    with pytest.raises(OpenAIUpstreamError) as exc:
        PolicyMixin._reject_non_embedding_model("acme-chat-70b", PUBLIC)
    body = str(exc.value)
    assert PUBLIC in body
    assert "acme-chat-70b" not in body
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "guard, model",
    [
        (PolicyMixin._reject_non_chat_model, INTERNAL),
        (PolicyMixin._reject_non_chat_messages_model, INTERNAL),
        (PolicyMixin._reject_non_embedding_model, "acme-chat-70b"),
    ],
)
def test_guard_falls_back_to_the_model_when_no_rewrite_happened(
    guard, model: str
) -> None:
    """With no rewrite in play the two names coincide, so nothing changes."""
    with pytest.raises((OpenAIUpstreamError, AnthropicUpstreamError)) as exc:
        guard(model)
    assert model in str(exc.value)
