"""OpenAI-compatible /v1/embeddings routing through the agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig

_OLLAMA_SCAN_ROW = {
    "id": "ollama",
    "status": "online",
    "base_url": "http://127.0.0.1:11434/v1",
    "model_count": 2,
    "models": ["nomic-embed-text", "qwen2"],
}
_OLLAMA_PROBE = {
    "status": "online",
    "models": ["nomic-embed-text", "qwen2"],
    "model_count": 2,
}


def _quiet_config() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    return cfg


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_embeddings_routes_to_serving_backend(
    mock_openai_cls: MagicMock,
    mock_probe: MagicMock,
    mock_scan: AsyncMock,
) -> None:
    mock_scan.return_value = [_OLLAMA_SCAN_ROW]
    mock_probe.return_value = _OLLAMA_PROBE

    sent: list[dict[str, object]] = []
    mock_client = MagicMock()
    mock_openai_cls.side_effect = lambda *a, **k: mock_client

    async def fake_create(**kwargs: object) -> MagicMock:
        sent.append(dict(kwargs))
        resp = MagicMock()
        resp.model_dump.return_value = {
            "object": "list",
            "model": kwargs.get("model"),
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }
        return resp

    mock_client.embeddings.create = fake_create

    with TestClient(create_app(_quiet_config())) as client:
        resp = client.post(
            "/v1/embeddings",
            json={"model": "nomic-embed-text", "input": "hello world"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"][0]["embedding"] == [0.1, 0.2]
    assert sent and sent[0]["model"] == "nomic-embed-text"


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
def test_embeddings_unknown_model_404_lists_embedding_models(
    mock_probe: MagicMock,
    mock_scan: AsyncMock,
) -> None:
    mock_scan.return_value = [_OLLAMA_SCAN_ROW]
    mock_probe.return_value = _OLLAMA_PROBE

    with TestClient(create_app(_quiet_config())) as client:
        resp = client.post(
            "/v1/embeddings",
            json={"model": "not-a-model", "input": "x"},
        )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "not-a-model" in detail
    # Embedding-capable models are listed first for embeddings requests.
    assert "nomic-embed-text" in detail


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
def test_chat_request_to_embedding_model_is_rejected_400(
    mock_probe: MagicMock,
    mock_scan: AsyncMock,
) -> None:
    """Chat completions must never burn the retry budget on encoders."""
    mock_scan.return_value = [_OLLAMA_SCAN_ROW]
    mock_probe.return_value = _OLLAMA_PROBE

    with TestClient(create_app(_quiet_config())) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "nomic-embed-text",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "embedding" in detail
    assert "/v1/embeddings" in detail


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
def test_messages_request_to_embedding_model_is_rejected_400(
    mock_probe: MagicMock,
    mock_scan: AsyncMock,
) -> None:
    mock_scan.return_value = [_OLLAMA_SCAN_ROW]
    mock_probe.return_value = _OLLAMA_PROBE

    with TestClient(create_app(_quiet_config())) as client:
        resp = client.post(
            "/v1/messages",
            json={
                "model": "nomic-embed-text",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 400


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
def test_models_list_includes_capability(
    mock_probe: MagicMock,
    mock_scan: AsyncMock,
) -> None:
    mock_scan.return_value = [_OLLAMA_SCAN_ROW]
    mock_probe.return_value = _OLLAMA_PROBE

    with TestClient(create_app(_quiet_config())) as client:
        resp = client.get("/v1/models")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()["data"]}
    assert by_id["nomic-embed-text"]["capability"] == "embedding"
    assert by_id["qwen2"]["capability"] == "chat"
