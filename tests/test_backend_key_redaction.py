"""F-59: a Backend must never serialize its API key.

Every payload that dumps a Backend — ``GET /netllm/v1/backends``, the status
body, and the heartbeat that body is gossiped as — carried the raw key, while
``require_read_access`` is a no-op whenever ``swarm.cluster_token`` is empty,
which is the default even on a LAN bind. Any host on the network could read
every configured backend and cloud credential in plaintext.

The key stays readable in-process (``resolve_api_key``); only the wire form
changes, and ``api_key_set`` replaces it — the convention
``_backend_override_export`` already used for the config view.
"""

from __future__ import annotations

import json

from netllm_core.models import Backend, BackendHealth

SECRET = "sk-do-not-leak-me"


def _backend(**kw) -> Backend:
    return Backend(
        id=kw.pop("id", "b1"),
        base_url=kw.pop("base_url", "http://10.0.0.5:1234/v1"),
        provider=kw.pop("provider", "custom"),
        **kw,
    )


def test_api_key_is_absent_from_every_dump_mode() -> None:
    b = _backend(api_key=SECRET)
    for dumped in (b.model_dump(), b.model_dump(mode="json")):
        assert "api_key" not in dumped
        assert SECRET not in json.dumps(dumped)


def test_api_key_set_reports_presence_without_the_value() -> None:
    assert _backend(api_key=SECRET).model_dump()["api_key_set"] is True
    assert _backend().model_dump()["api_key_set"] is False


def test_key_is_still_usable_in_process() -> None:
    """Redaction is a serialization concern; routing must be unaffected."""
    assert _backend(api_key=SECRET).resolve_api_key() == SECRET


def test_dumped_backend_still_revalidates() -> None:
    """Round-trip must not break: peers re-parse these rows from heartbeats."""
    b = _backend(api_key=SECRET, health=BackendHealth(models=["m1"]))
    again = Backend.model_validate(b.model_dump(mode="json"))
    assert again.id == b.id
    assert again.base_url == b.base_url
    assert again.health.models == ["m1"]
    # The receiving node gets no credential — by design.
    assert again.api_key == ""


def test_master_to_spoke_routing_never_needed_the_key() -> None:
    """A peer row is reached at its own agent surface, not with its keys.

    ``SwarmRegistry.peer_agent_backends`` builds peer rows with no api_key at
    all — a spoke authenticates the hop with the cluster token. This pins that
    property, so redaction cannot be blamed for a mesh regression later.
    """
    from netllm_core.models import NetllmConfig
    from netllm_discovery.swarm import PeerRecord, SwarmRegistry

    cfg = NetllmConfig()
    cfg.agent.agent_id = "master"
    registry = SwarmRegistry(cfg)
    registry.register_peer(
        PeerRecord(
            agent_id="spoke-1",
            listen_url="http://10.0.0.9:11400",
            role="peer",
            hostname="spoke",
            backends=[{"id": "remote", "base_url": "http://x/v1", "api_key": SECRET}],
        )
    )
    rows = registry.peer_agent_backends()
    assert rows, "expected the spoke to be routable from the master"
    for row in rows:
        assert row.api_key == ""
        assert row.base_url.endswith("/v1")


def test_a_redacted_payload_does_not_blank_a_stored_key() -> None:
    """The read-modify-write path stays safe.

    A dashboard/Settings save round-trips what it read. Because config_merge
    treats keys as write-only, an omitted key preserves the stored one — this
    is what makes redaction non-breaking rather than destructive.
    """
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.models import BackendOverride, NetllmConfig

    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(base_url="http://10.0.0.5:1234/v1", api_key=SECRET)
    ]
    # The client GETs a redacted view, edits an unrelated field, POSTs it back.
    patch = {
        "routing": {
            "backends": [{"base_url": "http://10.0.0.5:1234/v1", "enabled": False}]
        }
    }
    merged = apply_config_patch(cfg, patch)
    assert merged.routing.backends[0].api_key == SECRET
    assert merged.routing.backends[0].enabled is False
