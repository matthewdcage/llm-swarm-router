"""OpenAI cloud failover inject when no local backends."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_openai_cloud_inject_when_no_local_backends(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client: TestClient,
) -> None:
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "id": "chatcmpl-cloud",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "cloud reply"},
                "finish_reason": "stop",
            }
        ],
        "model": "gpt-4",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer sk-openai-test"},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "cloud reply"
    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["model"] == "gpt-4"
