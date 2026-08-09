"""OpenAI cloud failover inject when no local backends."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig
from wire_mock import chat_completion_body, wire_mock_chat_json


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_openai_cloud_inject_when_no_local_backends(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client: TestClient,
) -> None:
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    wire_mock_chat_json(
        mock_client,
        chat_completion_body("cloud reply", model="gpt-4", id="chatcmpl-cloud"),
    )

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
    sent = mock_client._build_request.call_args.args[0].json_data
    assert sent["model"] == "gpt-4"


# --- F-04: a caller's key must never become shared pool state ---------------


def _cloud_body() -> dict:
    return chat_completion_body("cloud reply", model="gpt-4", id="chatcmpl-cloud")


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_caller_key_cloud_row_never_enters_the_pool(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client: TestClient,
) -> None:
    """The legacy inject deduped on backend *existence*, so the first caller
    to present a real key seeded a pooled row carrying that key — and every
    later request from any client on the LAN could be routed to it and billed
    to that first caller. The row is request-scoped now."""
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    wire_mock_chat_json(mock_client, _cloud_body())

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-caller-one"},
    )
    assert resp.status_code == 200

    service = client.app.state.service
    pooled_ids = {b.id for b in service.pool.backends}
    assert "openai-cloud" not in pooled_ids, (
        "a caller-supplied credential must not survive the request in the pool"
    )
    assert not any(b.cloud_provider for b in service.pool.backends)


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_second_caller_does_not_inherit_the_first_callers_key(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client: TestClient,
) -> None:
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    wire_mock_chat_json(mock_client, _cloud_body())

    for key in ("sk-caller-one", "sk-caller-two"):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert resp.status_code == 200

    # Each request built its own upstream client with its own credential.
    used_keys = [c.kwargs.get("api_key") for c in mock_openai_cls.call_args_list]
    assert "sk-caller-one" in used_keys
    assert "sk-caller-two" in used_keys, (
        "the second caller was served with the first caller's cached client"
    )


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_keyless_caller_gets_no_cloud_route(
    mock_openai_cls: MagicMock,
    mock_scan: AsyncMock,
    client: TestClient,
) -> None:
    """With no local backend and no credential of their own, a caller must be
    told there is nothing to route to — not silently served on someone
    else's account."""
    mock_scan.return_value = []
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    wire_mock_chat_json(mock_client, _cloud_body())

    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-caller-one"},
    )
    mock_client._client.send.reset_mock()

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer netllm-local"},
    )
    assert resp.status_code != 200
    mock_client._client.send.assert_not_awaited()
