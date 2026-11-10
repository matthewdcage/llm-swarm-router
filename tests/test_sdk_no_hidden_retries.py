"""The router owns retries; vendor SDK clients must not retry in place.

Both SDKs default to ``max_retries=2``. Left alone, a hung backend costs three
read timeouts, and a 429/503 is replayed on the busy node, before the router's
failover loop ever sees the error (audit F-99).
"""

from __future__ import annotations

import httpx
import pytest
from netllm_sdk_anthropic.client import AnthropicUpstream
from netllm_sdk_openai.client import OpenAIUpstream, OpenAIUpstreamError


def test_openai_upstream_disables_sdk_retries() -> None:
    up = OpenAIUpstream("http://127.0.0.1:9/v1")
    assert up._async.max_retries == 0
    assert up._sync.max_retries == 0


def test_anthropic_upstream_disables_sdk_retries() -> None:
    up = AnthropicUpstream("netllm-local", base_url="http://127.0.0.1:9")
    assert up._client.max_retries == 0


async def test_embeddings_503_reaches_caller_after_one_attempt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "busy"}})

    up = OpenAIUpstream("http://upstream.test/v1")
    up._async._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream.test/v1"
    )
    with pytest.raises(OpenAIUpstreamError):
        await up.embeddings({"model": "m", "input": "x"})
    assert calls == 1
