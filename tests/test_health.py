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
