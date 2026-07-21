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


def test_probe_netllm_agent_degraded_when_status_fails() -> None:
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
            return FakeResp(500)

    with patch("netllm_discovery.runtime.httpx.Client", return_value=FakeClient()):
        result = probe_netllm_agent("http://127.0.0.1:11400")

    assert result is not None
    assert result.get("degraded") is True


def test_check_listen_port_none_when_free() -> None:
    from netllm_discovery.runtime import check_listen_port

    cfg = NetllmConfig()
    port = _free_port()
    cfg.agent.listen = f"127.0.0.1:{port}"
    assert check_listen_port(cfg) is None


def test_stop_netllm_on_port_escalates_to_sigkill() -> None:
    """Port-free alone is not "stopped": a draining uvicorn keeps mDNS
    registered and gossiping, colliding with its replacement. The stop
    helper must wait for process EXIT and escalate to SIGKILL."""
    from netllm_discovery import runtime

    alive: dict[str, bool] = {"alive": True}
    killed: list[int] = []

    def fake_pid_alive(pid: int) -> bool:
        return alive["alive"]

    def fake_force_kill(pid: int) -> bool:
        killed.append(pid)
        alive["alive"] = False
        return True

    with (
        patch.object(runtime, "probe_netllm_agent", return_value={"agent_id": "x"}),
        patch.object(runtime, "port_owner_pid", return_value=4242),
        patch.object(runtime, "terminate_pid", return_value=True),
        patch.object(runtime, "is_port_in_use", return_value=False),
        patch("netllm_discovery.process_util.pid_alive", side_effect=fake_pid_alive),
        patch(
            "netllm_discovery.process_util.force_kill_pid",
            side_effect=fake_force_kill,
        ),
    ):
        assert runtime.stop_netllm_on_port(11400, wait_s=0.3) is True
    assert killed == [4242]


def test_mdns_advertiser_retries_after_collision() -> None:
    """A startup name collision (draining predecessor still registered)
    must not disable LAN advertising forever — retry succeeds later."""
    import asyncio

    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.agent.advertise = True
    cfg.swarm.mdns = True
    service = AgentService(cfg)

    calls = {"n": 0}

    class FlakyAdvertiser:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def start(self) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("mDNS name collision")

        def stop(self) -> None:
            pass

    class FakeBrowser:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    async def run() -> None:
        with (
            patch("netllm_discovery.mdns.MdnsAdvertiser", FlakyAdvertiser),
            patch("netllm_discovery.mdns.MdnsBrowser", FakeBrowser),
        ):
            first = service._try_start_mdns()
            assert first is not None
            assert service._mdns_advertiser is None
            second = service._try_start_mdns()
            assert second is None
            assert service._mdns_advertiser is not None

    asyncio.run(run())
