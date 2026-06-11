"""Tests for mDNS advertiser recovery."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch


class _FakeNonUnique(Exception):
    pass


def test_mdns_advertiser_retries_on_non_unique_name() -> None:
    register_calls = 0

    class FakeZeroconf:
        def register_service(self, info: object) -> None:
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


def test_mdns_advertise_address_uses_lan_for_wildcard_bind() -> None:
    from netllm_discovery.mdns import _advertise_address

    with patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"):
        host, _addr = _advertise_address("0.0.0.0:11400")
    assert host == "10.0.0.32"


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
