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
