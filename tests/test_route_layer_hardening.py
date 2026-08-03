"""Route-layer hardening regressions (batch D of the 2026-07-31 follow-up audit).

Covers:
- F-40: /netllm/v1/client-env gated by the cluster token like sibling reads.
- F-41: /metrics gated by the cluster token, open when none is configured.
- F-38: /v1/* errors rendered in the OpenAI / Anthropic error envelopes;
  /netllm/* keeps FastAPI's {"detail": ...}.
- F-32: streaming requests surface pre-stream failures as real HTTP status
  codes (429 capacity, 502 no-backend) instead of HTTP 200 + aborted body.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.service import SourceCapacityExceeded
from netllm_core.models import NetllmConfig

REMOTE = ("10.9.9.9", 5555)


def _base_cfg() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    return cfg


def _token_cfg() -> NetllmConfig:
    cfg = _base_cfg()
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.cluster_token = "cluster-tok"
    return cfg


# --- F-40: /netllm/v1/client-env -------------------------------------------


def test_f40_client_env_requires_token_from_remote_client() -> None:
    with TestClient(create_app(_token_cfg()), client=REMOTE) as client:
        assert client.get("/netllm/v1/client-env").status_code == 401
        ok = client.get(
            "/netllm/v1/client-env",
            headers={"Authorization": "Bearer cluster-tok"},
        )
        assert ok.status_code == 200
        assert "vars" in ok.json()


def test_f40_client_env_open_when_no_token_configured() -> None:
    cfg = _token_cfg()
    cfg.swarm.cluster_token = ""
    with TestClient(create_app(cfg), client=REMOTE) as client:
        assert client.get("/netllm/v1/client-env").status_code == 200


# --- F-41: /metrics ---------------------------------------------------------


def test_f41_metrics_requires_token_from_remote_client() -> None:
    with TestClient(create_app(_token_cfg()), client=REMOTE) as client:
        assert client.get("/metrics").status_code == 401
        ok = client.get("/metrics", headers={"Authorization": "Bearer cluster-tok"})
        assert ok.status_code == 200
        assert b"netllm_requests_total" in ok.content


def test_f41_metrics_open_when_no_token_configured() -> None:
    cfg = _token_cfg()
    cfg.swarm.cluster_token = ""
    with TestClient(create_app(cfg), client=REMOTE) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert b"netllm_requests_total" in resp.content


def test_f41_metrics_open_for_local_client_even_with_token() -> None:
    with TestClient(create_app(_token_cfg())) as client:
        assert client.get("/metrics").status_code == 200


# --- F-38: per-surface error envelopes --------------------------------------

_CHAT_BODY = {
    "model": "test-model",
    "messages": [{"role": "user", "content": "hi"}],
}
_MESSAGES_BODY = {
    "model": "test-model",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "hi"}],
}


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
def test_f38_openai_shape_on_no_backend_502(mock_scan: AsyncMock) -> None:
    mock_scan.return_value = []
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/chat/completions", json=_CHAT_BODY)
    assert resp.status_code == 502
    body = resp.json()
    assert "detail" not in body
    err = body["error"]
    assert set(err) == {"message", "type", "param", "code"}
    assert err["type"] == "api_error"
    assert "backend" in err["message"].lower()


@patch(
    "netllm_agent.service.AgentService._source_admit",
    side_effect=SourceCapacityExceeded("cli", 1),
)
def test_f38_openai_shape_on_capacity_429(_mock_admit: object) -> None:
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/chat/completions", json=_CHAT_BODY)
    assert resp.status_code == 429
    err = resp.json()["error"]
    assert err["type"] == "rate_limit_error"
    assert err["code"] == "rate_limit_exceeded"
    assert "cli" in err["message"]


@patch(
    "netllm_agent.service.AgentService._source_admit",
    side_effect=SourceCapacityExceeded("cli", 1),
)
def test_f38_anthropic_shape_on_capacity_429(_mock_admit: object) -> None:
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/messages", json=_MESSAGES_BODY)
    assert resp.status_code == 429
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "cli" in body["error"]["message"]


def test_f38_openai_shape_on_auth_401() -> None:
    cfg = _token_cfg()
    cfg.swarm.require_token_for_inference = True
    with TestClient(create_app(cfg), client=REMOTE) as client:
        resp = client.get("/v1/models")
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["type"] == "authentication_error"


def test_f38_netllm_routes_keep_fastapi_detail() -> None:
    with TestClient(create_app(_token_cfg()), client=REMOTE) as client:
        resp = client.get("/netllm/v1/status")
    assert resp.status_code == 401
    assert "detail" in resp.json()
    assert "error" not in resp.json()


def test_f38_openai_shape_on_malformed_json_400() -> None:
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post(
            "/v1/chat/completions",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert "Invalid JSON" in err["message"]


def test_f38_anthropic_shape_on_malformed_json_400() -> None:
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post(
            "/v1/messages",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "Invalid JSON" in body["error"]["message"]


# --- F-32: streaming pre-flight status codes --------------------------------


@patch(
    "netllm_agent.service.AgentService._source_admit",
    side_effect=SourceCapacityExceeded("cli", 1),
)
def test_f32_stream_capacity_exceeded_is_http_429(_mock_admit: object) -> None:
    """stream=true + admission failure must be a real 429, not a 200 with an
    aborted body (F-32: async generators run nothing until first iteration)."""
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/chat/completions", json={**_CHAT_BODY, "stream": True})
    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_error"


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
def test_f32_stream_no_backend_is_http_5xx(mock_scan: AsyncMock) -> None:
    mock_scan.return_value = []
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/chat/completions", json={**_CHAT_BODY, "stream": True})
    assert resp.status_code != 200
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "api_error"


@patch(
    "netllm_agent.service.AgentService._source_admit",
    side_effect=SourceCapacityExceeded("cli", 1),
)
def test_f32_messages_stream_capacity_exceeded_is_http_429(
    _mock_admit: object,
) -> None:
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/messages", json={**_MESSAGES_BODY, "stream": True})
    assert resp.status_code == 429
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"


@patch("netllm_agent.service.backends.scan_local_providers", new_callable=AsyncMock)
def test_f32_responses_stream_no_backend_is_http_5xx(mock_scan: AsyncMock) -> None:
    mock_scan.return_value = []
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post(
            "/v1/responses",
            json={"model": "test-model", "input": "hi", "stream": True},
        )
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "api_error"


async def _fake_chat_stream(*_args, **_kwargs):
    yield 'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
    yield 'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
    yield "data: [DONE]\n\n"


@patch(
    "netllm_agent.service.AgentService.proxy_chat_completion_stream",
    side_effect=_fake_chat_stream,
)
def test_f32_successful_stream_still_delivers_all_chunks(_mock: object) -> None:
    """The pre-flight first-chunk pull must not drop or reorder stream data."""
    with TestClient(create_app(_base_cfg())) as client:
        resp = client.post("/v1/chat/completions", json={**_CHAT_BODY, "stream": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert '"he"' in resp.text
    assert '"llo"' in resp.text
    assert "[DONE]" in resp.text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
