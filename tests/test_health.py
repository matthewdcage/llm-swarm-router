"""Tests for health probe helpers."""

from __future__ import annotations

from netllm_core.health import is_online, status_from_response


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | str) -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else ""

    def json(self) -> dict:
        return self._body  # type: ignore[return-value]


def test_online_from_models_200() -> None:
    resp = _FakeResponse(200, {"data": [{"id": "gemma"}]})
    status = status_from_response(resp)  # type: ignore[arg-type]
    assert is_online(status)
    assert status["model_count"] == 1


def test_auth_still_online() -> None:
    resp = _FakeResponse(401, "")
    status = status_from_response(resp)  # type: ignore[arg-type]
    assert is_online(status)


def test_anthropic_probe_auth_still_online() -> None:
    from netllm_core.health import _anthropic_probe_status

    resp = _FakeResponse(401, "")
    status = _anthropic_probe_status(resp)  # type: ignore[arg-type]
    assert is_online(status)


def test_anthropic_probe_reachable_on_400() -> None:
    from netllm_core.health import _anthropic_probe_status

    resp = _FakeResponse(400, "bad request")
    status = _anthropic_probe_status(resp)  # type: ignore[arg-type]
    assert is_online(status)


def test_diagnose_probe_skips_embedding_models() -> None:
    """The 1-token diagnose probe must not chat at an embedding model.

    oMLX catalogs sort bge-* first; probing models[0] blindly 400s on
    every provider scan and spams the provider's log.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from netllm_core.health import diagnose_backend

    health = {
        "status": "online",
        "models": ["bge-m3-mlx-8bit", "gemma-4-12B-8bit"],
        "model_count": 2,
    }
    client = AsyncMock()
    client.post.return_value = _FakeResponse(200, {"choices": []})
    with patch("netllm_core.health.probe_openai_compat", return_value=health):
        asyncio.run(diagnose_backend("http://x/v1", client))
    payload = client.post.call_args.kwargs["json"]
    assert payload["model"] == "gemma-4-12B-8bit"


# --- F-07: health probes must not be billable inference calls ---------------


def test_anthropic_probe_prefers_the_free_models_endpoint() -> None:
    """The Messages probe was the only path, with a hardcoded model id — so
    every health refresh of a cloud Anthropic backend was a paid API call,
    roughly one per routing.health_ttl_s for the life of the agent."""
    from unittest.mock import MagicMock, patch

    from netllm_core.health import probe_anthropic_compat_sync

    models_resp = MagicMock()
    models_resp.status_code = 200
    models_resp.json.return_value = {"data": [{"id": "claude-opus-4-7"}]}

    client = MagicMock()
    client.get.return_value = models_resp

    with patch("netllm_core.health._shared_sync_client", return_value=client):
        status = probe_anthropic_compat_sync(
            "https://api.anthropic.com", api_key="sk-ant-test"
        )

    assert status["status"] == "online"
    assert status["models"] == ["claude-opus-4-7"]
    client.post.assert_not_called()
    assert client.get.call_args.args[0].endswith("/v1/models")


def test_anthropic_probe_falls_back_to_messages_without_a_catalog() -> None:
    """Providers with no GET /v1/models (Z.ai) still need a reachability
    signal — and it must use a model that provider actually serves, not a
    hardcoded Anthropic id."""
    from unittest.mock import MagicMock, patch

    from netllm_core.health import probe_anthropic_compat_sync

    no_catalog = MagicMock()
    no_catalog.status_code = 404
    messages_ok = MagicMock()
    messages_ok.status_code = 200

    client = MagicMock()
    client.get.return_value = no_catalog
    client.post.return_value = messages_ok

    with patch("netllm_core.health._shared_sync_client", return_value=client):
        status = probe_anthropic_compat_sync(
            "https://api.z.ai/api/anthropic",
            api_key="zk-test",
            fallback_model="glm-5.2",
        )

    assert status["status"] == "online"
    assert client.post.call_args.kwargs["json"]["model"] == "glm-5.2"


def test_anthropic_probe_without_key_skips_the_catalog_attempt() -> None:
    from unittest.mock import MagicMock, patch

    from netllm_core.health import probe_anthropic_compat_sync

    messages_ok = MagicMock()
    messages_ok.status_code = 401
    client = MagicMock()
    client.post.return_value = messages_ok

    with patch("netllm_core.health._shared_sync_client", return_value=client):
        status = probe_anthropic_compat_sync("https://api.anthropic.com")

    assert status["status"] == "online"
    client.get.assert_not_called()
