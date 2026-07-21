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
