"""Tests for mDNS advertiser recovery."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch


class _FakeNonUnique(Exception):
    pass


def test_mdns_advertiser_retries_on_non_unique_name() -> None:
    register_calls = 0

    class FakeZeroconf:
        def register_service(
            self, info: object, allow_name_change: bool = False
        ) -> None:
            nonlocal register_calls
            register_calls += 1
            if register_calls == 1:
                raise _FakeNonUnique("collision")

        def unregister_service(self, info: object) -> None:
            pass

        def close(self) -> None:
            pass

    fake_zc_mod = MagicMock()
    fake_zc_mod.Zeroconf = FakeZeroconf
    fake_zc_mod.ServiceInfo = MagicMock(return_value=MagicMock())
    fake_zc_mod.NonUniqueNameException = _FakeNonUnique

    from netllm_discovery.mdns import MdnsAdvertiser

    advertiser = MdnsAdvertiser("0.0.0.0:11400", "agent-1", "peer")

    with (
        patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.9"),
        patch("netllm_discovery.mdns.time.sleep"),
        patch.dict("sys.modules", {"zeroconf": fake_zc_mod}),
    ):
        thread = threading.Thread(target=advertiser._run, daemon=True)
        thread.start()
        assert advertiser._ready.wait(timeout=3.0)
        advertiser._stop.set()
        thread.join(timeout=3.0)

    assert register_calls == 2
    assert advertiser._error is None
    # Collision fallback re-registers under a pid-suffixed identity so a
    # stale record from a SIGKILLed predecessor cannot block advertising.
    second_info_call = fake_zc_mod.ServiceInfo.call_args_list[-1]
    assert str(__import__("os").getpid()) in second_info_call.args[1]


def test_mdns_advertise_address_uses_lan_for_wildcard_bind() -> None:
    from netllm_discovery.mdns import _advertise_address

    with patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"):
        host, _addr = _advertise_address("0.0.0.0:11400")
    assert host == "10.0.0.32"


def _captured_advertise_props(listen: str) -> dict[str, str]:
    captured: dict[str, dict[str, bytes]] = {}

    class FakeZeroconf:
        def register_service(self, info: object) -> None:
            pass

        def unregister_service(self, info: object) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_service_info(*_args: object, **kwargs: object) -> MagicMock:
        captured["props"] = kwargs.get("properties", {})
        return MagicMock()

    fake_zc_mod = MagicMock()
    fake_zc_mod.Zeroconf = FakeZeroconf
    fake_zc_mod.ServiceInfo = fake_service_info
    fake_zc_mod.NonUniqueNameException = _FakeNonUnique

    from netllm_discovery.mdns import MdnsAdvertiser

    advertiser = MdnsAdvertiser(listen, "agent-x", "peer")
    with (
        patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"),
        patch.dict("sys.modules", {"zeroconf": fake_zc_mod}),
    ):
        thread = threading.Thread(target=advertiser._run, daemon=True)
        thread.start()
        assert advertiser._ready.wait(timeout=3.0)
        advertiser._stop.set()
        thread.join(timeout=3.0)
    return {k: v.decode() for k, v in captured["props"].items()}


def test_mdns_loopback_bind_advertises_unreachable_flag() -> None:
    props = _captured_advertise_props("127.0.0.1:11400")
    assert props["reachable"] == "false"
    assert props["listen_url"] == "http://127.0.0.1:11400"


def test_mdns_lan_bind_advertises_reachable_flag() -> None:
    props = _captured_advertise_props("0.0.0.0:11400")
    assert props["reachable"] == "true"
    assert props["listen_url"] == "http://10.0.0.32:11400"


def test_mdns_advertiser_sets_error_on_hard_failure() -> None:
    class FailingZeroconf:
        def register_service(self, info: object) -> None:
            raise RuntimeError("boom")

        def unregister_service(self, info: object) -> None:
            pass

        def close(self) -> None:
            pass

    fake_zc_mod = MagicMock()
    fake_zc_mod.Zeroconf = FailingZeroconf
    fake_zc_mod.ServiceInfo = MagicMock(return_value=MagicMock())
    fake_zc_mod.NonUniqueNameException = _FakeNonUnique

    from netllm_discovery.mdns import MdnsAdvertiser

    advertiser = MdnsAdvertiser("127.0.0.1:11400", "agent-2", "peer")

    with patch.dict("sys.modules", {"zeroconf": fake_zc_mod}):
        thread = threading.Thread(target=advertiser._run, daemon=True)
        thread.start()
        assert advertiser._ready.wait(timeout=3.0)
        thread.join(timeout=3.0)

    assert advertiser._error is not None
