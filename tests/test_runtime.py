"""Tests for port preflight and runtime helpers."""

from __future__ import annotations

import socket
from unittest.mock import patch

from netllm_core.models import NetllmConfig
from netllm_discovery.runtime import (
    PortConflict,
    format_port_conflict_message,
    is_port_in_use,
    port_conflict_hints,
    probe_netllm_agent,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_is_port_in_use_false_for_free_port() -> None:
    port = _free_port()
    assert is_port_in_use("127.0.0.1", port) is False


def test_is_port_in_use_true_when_bound() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert is_port_in_use("127.0.0.1", port) is True


def test_format_port_conflict_message_includes_agent_id() -> None:
    conflict = PortConflict(
        port=11400,
        pid=1234,
        url="http://127.0.0.1:11400",
        occupied_by_netllm=True,
        agent_id="abc123",
        hostname="macbook",
    )
    msg = format_port_conflict_message(conflict)
    assert "11400" in msg
    assert "abc123" in msg
    assert "1234" in msg


def test_port_conflict_hints_include_replace() -> None:
    conflict = PortConflict(
        port=11400,
        pid=None,
        url="http://127.0.0.1:11400",
        occupied_by_netllm=True,
    )
    hints = port_conflict_hints(conflict, replace_flag="netllm serve --replace")
    assert any("--replace" in h for h in hints)


def test_probe_netllm_agent_returns_status_on_success() -> None:
    status = {"agent_id": "x", "hostname": "h", "backends": []}

    class FakeResp:
        def __init__(self, code: int, payload: dict | None = None) -> None:
            self.status_code = code
            self._payload = payload or {}

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> FakeResp:
            if url.endswith("/health"):
                return FakeResp(200)
            return FakeResp(200, status)

    with patch("netllm_discovery.runtime.httpx.Client", return_value=FakeClient()):
        result = probe_netllm_agent("http://127.0.0.1:11400")

    assert result is not None
    assert result["agent_id"] == "x"


def test_check_listen_port_none_when_free() -> None:
    from netllm_discovery.runtime import check_listen_port

    cfg = NetllmConfig()
    port = _free_port()
    cfg.agent.listen = f"127.0.0.1:{port}"
    assert check_listen_port(cfg) is None
