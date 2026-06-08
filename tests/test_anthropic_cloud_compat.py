"""Cloud-direct /v1/messages passthrough compatibility tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig
from netllm_sdk_anthropic.client import AnthropicUpstreamError


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_anthropic.client.AsyncAnthropic")
def test_cloud_passthrough_payload(
    mock_anthropic_cls: MagicMock,
    mock_scan: AsyncMock,
    client: TestClient,
) -> None:
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "id": "msg_cloud",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "cloud reply"}],
        "model": "claude-3-5-haiku-20241022",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    resp = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-api-key": "sk-ant-test"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "cloud reply"
    call_kwargs = mock_client.messages.create.await_args.kwargs
    assert call_kwargs["model"] == "claude-3-5-haiku-20241022"
    assert call_kwargs["messages"][0]["content"] == "hi"


@patch(
    "netllm_agent.service.AgentService.proxy_messages",
    new_callable=AsyncMock,
)
def test_anthropic_error_status_passthrough(
    mock_proxy: AsyncMock,
    client: TestClient,
) -> None:
    mock_proxy.side_effect = AnthropicUpstreamError(
        "invalid x-api-key", status_code=401
    )
    resp = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-api-key": "bad"},
    )
    assert resp.status_code == 401
