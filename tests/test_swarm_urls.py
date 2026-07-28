"""Tests for swarm listen URL resolution."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import NetllmConfig
from netllm_discovery.swarm import SwarmRegistry


def test_local_agent_url_uses_lan_ip_for_wildcard_bind() -> None:
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    registry = SwarmRegistry(cfg)

    with patch("netllm_discovery.lan.local_lan_ip", return_value="10.0.0.32"):
        assert registry.local_agent_url() == "http://10.0.0.32:11400"


def test_local_agent_url_keeps_loopback_bind() -> None:
    cfg = NetllmConfig()
    cfg.agent.listen = "127.0.0.1:11400"
    registry = SwarmRegistry(cfg)
    assert registry.local_agent_url() == "http://127.0.0.1:11400"


# --- F-11: IPv6 listen values must parse the same way everywhere ------------


def test_split_listen_handles_ipv6_and_bare_hosts() -> None:
    """AgentConfig._validate_listen accepts bracketed IPv6, but callers used
    to split with partition(":"), yielding host "[" and port ":]:11400" — so
    `netllm serve` died on int() for a value the config layer had just
    declared valid."""
    from netllm_core.models import split_listen

    assert split_listen("127.0.0.1:11400") == ("127.0.0.1", 11400)
    assert split_listen("0.0.0.0:12000") == ("0.0.0.0", 12000)
    assert split_listen("[::]:11400") == ("::", 11400)
    assert split_listen("[::1]:8080") == ("::1", 8080)
    # Unbracketed multi-colon is a bare IPv6 address with no port; splitting
    # on the last colon would read "::1" as host ":" and port "1".
    assert split_listen("::1") == ("::1", 11400)
    assert split_listen("2001:db8::1") == ("2001:db8::1", 11400)
    assert split_listen("localhost") == ("localhost", 11400)
    assert split_listen("http://host:9000/") == ("host", 9000)


def test_format_listen_round_trips_ipv6() -> None:
    from netllm_core.models import format_listen, split_listen

    for value in ("127.0.0.1:11400", "[::]:11400", "[::1]:8080"):
        host, port = split_listen(value)
        assert split_listen(format_listen(host, port)) == (host, port)


def test_agent_url_from_listen_brackets_ipv6_authority() -> None:
    """A bare IPv6 host has to be re-bracketed inside a URL authority or the
    resulting URL is unparseable."""
    from netllm_discovery.lan import agent_url_from_listen

    assert agent_url_from_listen("[::1]:11400") == "http://[::1]:11400"
    assert agent_url_from_listen("[::]:11400", lan_ip="10.0.0.5") == (
        "http://10.0.0.5:11400"
    )
