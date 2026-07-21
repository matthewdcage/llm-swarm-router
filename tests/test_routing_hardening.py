"""Tests for routing hardening: per-request overrides, peer pruning,
offline recovery, hop guard, config hot-apply, and one-shot LAN defaults."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import (
    Backend,
    BackendHealth,
    NetllmConfig,
    ensure_lan_mesh_defaults,
)
from netllm_core.pool import RouterPool
from netllm_core.routing_policy import resolve_routing

_MOCK_ONLINE = {"status": "online", "models": ["shared-model"], "model_count": 1}


def _local(bid: str = "local", url: str = "http://127.0.0.1:8080/v1") -> Backend:
    return Backend(
        id=bid,
        base_url=url,
        provider="omlx",
        local=True,
        health=BackendHealth(status="online", models=["shared-model"]),
    )


def _peer(agent: str = "remote", host: str = "192.168.1.11") -> Backend:
    return Backend(
        id=f"peer:{agent}",
        base_url=f"http://{host}:11400/v1",
        provider="custom",
        local=False,
        health=BackendHealth(status="online", models=["shared-model"]),
    )


def test_resolve_routing_strategy_header_overrides_default() -> None:
    cfg = NetllmConfig()
    cfg.routing.default_strategy = "local_first"
    routing = resolve_routing(
        cfg.routing,
        model="shared-model",
        api_format="openai",
        header_local_only=False,
        header_strategy="round_robin",
    )
    assert routing.strategy == "round_robin"

    invalid = resolve_routing(
        cfg.routing,
        model="shared-model",
        api_format="openai",
        header_local_only=False,
        header_strategy="not-a-strategy",
    )
    assert invalid.strategy == "local_first"


def test_resolve_routing_backend_pin_header() -> None:
    cfg = NetllmConfig()
    routing = resolve_routing(
        cfg.routing,
        model="m",
        api_format="openai",
        header_local_only=False,
        header_backend="peer:remote",
    )
    assert routing.pinned_backend == "peer:remote"


def test_backend_by_id_matches_id_agent_and_url() -> None:
    pool = RouterPool()
    pool.set_backends([_local(), _peer("remote")])
    assert pool.backend_by_id("peer:remote").id == "peer:remote"
    assert pool.backend_by_id("remote").id == "peer:remote"
    assert pool.backend_by_id("http://192.168.1.11:11400/v1").id == "peer:remote"
    assert pool.backend_by_id("nope") is None


def test_prune_peer_rows_removes_stale_and_hops() -> None:
    pool = RouterPool()
    peer = _peer("gone")
    pool.merge_backends([_local(), peer])
    pool.acquire(peer)
    pool.release(peer)
    assert any(b.id == "peer:gone" for b in pool.backends)

    pool.prune_peer_rows(keep_urls=set())
    assert not any(b.id.startswith("peer:") for b in pool.backends)
    assert peer.base_url not in pool._own_peer_hops
    # Local rows are untouched.
    assert any(b.local for b in pool.backends)


@patch("netllm_core.pool.probe_openai_compat_sync")
def test_offline_backend_reprobes_after_retry_window(mock_probe: object) -> None:
    mock_probe.return_value = _MOCK_ONLINE
    pool = RouterPool(max_failures=2, offline_retry_s=5.0, health_ttl_s=30.0)
    b = _local()
    pool.set_backends([b])
    pool.mark_failure(b)
    pool.mark_failure(b)
    assert b.health.status == "offline"
    entry = pool._health_cache[b.cache_key()]
    assert entry.online is False

    # Within the retry window: still offline, no probe fired.
    assert pool.is_healthy(b) is False
    assert mock_probe.call_count == 0

    # After the (shorter) offline retry window: re-probe recovers it —
    # well before the 30s healthy TTL would have.
    entry.last_check -= 6.0
    assert pool.is_healthy(b) is True
    assert mock_probe.call_count == 1


@patch("netllm_core.pool.probe_openai_compat_sync")
def test_failed_probe_keeps_last_known_models(mock_probe: object) -> None:
    mock_probe.return_value = {"status": "offline", "models": [], "model_count": 0}
    pool = RouterPool()
    b = _peer("remote")
    pool.set_backends([b])
    assert pool.is_healthy(b) is False
    # Heartbeat-sourced catalog survives the failed probe.
    assert b.health.models == ["shared-model"]


def test_wants_local_only_hop_backstop() -> None:
    from netllm_agent.service import AgentService

    assert not AgentService._wants_local_only({"x-netllm-hops": "1"})
    assert AgentService._wants_local_only({"x-netllm-hops": "2"})
    assert AgentService._wants_local_only({"x-netllm-hops": "7"})


def test_select_backend_honors_pin() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    service = AgentService(cfg)
    local = _local()
    peer = _peer("remote")
    service.pool.set_backends([local, peer])

    picked = service._select_backend_for_request(
        "shared-model", "local_first", 1, None, pinned="peer:remote"
    )
    assert picked is peer
    # local_only requests never pin to a remote (loop guard wins).
    with patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE):
        picked = service._select_backend_for_request(
            "shared-model",
            "local_first",
            1,
            None,
            local_only=True,
            pinned="peer:remote",
        )
    assert picked is local


def test_merge_backends_hydrates_peer_health_from_cache() -> None:
    pool = RouterPool()
    peer = _peer()
    peer.health.status = "unknown"
    pool.set_backends([peer])
    # A successful routed request marks the peer online in the cache.
    pool.mark_success(peer)
    # Heartbeat refresh rebuilds the row with default (unknown) health.
    rebuilt = _peer()
    rebuilt.health.status = "unknown"
    pool.merge_backends([rebuilt])
    row = next(b for b in pool.backends if b.id == "peer:remote")
    assert row.health.status == "online"


def test_peer_config_warnings_on_strategy_and_version_drift() -> None:
    from netllm_agent.service import AgentService
    from netllm_discovery.swarm import PeerRecord

    cfg = NetllmConfig()
    cfg.routing.default_strategy = "least_load"
    service = AgentService(cfg)
    service.swarm.register_peer(
        PeerRecord(
            agent_id="drifty",
            listen_url="http://192.168.1.11:11400",
            hostname="other-mac",
            routing_strategy="round_robin",
            version="0.0.1",
        )
    )
    warnings = service.peer_config_warnings()
    assert any("round_robin" in w for w in warnings)
    assert any("0.0.1" in w for w in warnings)
    # Peers predating the fields (empty strings) produce no noise.
    service.swarm.register_peer(
        PeerRecord(agent_id="older", listen_url="http://192.168.1.12:11400")
    )
    assert len(service.peer_config_warnings()) == 2


def test_shardless_batch_shard_counts_fallbacks() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.routing.default_strategy = "batch_shard"
    service = AgentService(cfg)
    local = _local()
    service.pool.set_backends([local])
    service.pool.mark_success(local)
    picked = service._select_backend_for_request("shared-model", "batch_shard", 1, None)
    assert picked is local
    assert service._shardless_fallbacks == 1
    assert service.status_payload()["shardless_fallbacks"] == 1


def test_auto_strategy_maps_shard_context_to_batch_shard() -> None:
    from netllm_agent.service import AgentService
    from netllm_agent.shard import ShardContext

    cfg = NetllmConfig()
    service = AgentService(cfg)
    a = _local("a", "http://127.0.0.1:8080/v1")
    b = _local("b", "http://127.0.0.1:8081/v1")
    b.in_flight = 3
    service.pool.set_backends([a, b])
    service.pool.mark_success(a)
    service.pool.mark_success(b)
    # No shard context: auto balances by load.
    assert service._select_backend_for_request("shared-model", "auto", 1, None) is a
    # Shard context: auto shards deterministically (numeric key = index).
    shard = ShardContext(shard_key="1")
    picked = service._select_backend_for_request("shared-model", "auto", 1, shard)
    assert picked is b


def test_apply_config_hot_syncs_pool() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    service = AgentService(cfg)
    merged = NetllmConfig()
    merged.routing.allow_remote = False
    merged.routing.spillover_max_local_in_flight = 7
    merged.routing.max_backend_failures = 1
    merged.routing.model_aliases = {"canon": ["real-id"]}
    service.apply_config(merged)
    assert service.pool.allow_remote is False
    assert service.pool.spillover_max_local_in_flight == 7
    assert service.pool.max_failures == 1
    assert service.pool.model_aliases == {"canon": ["real-id"]}
    assert service.swarm.config is merged


def test_lan_mesh_defaults_one_shot() -> None:
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    assert ensure_lan_mesh_defaults(cfg)
    assert cfg.routing.default_strategy == "local_spillover"
    assert cfg.routing.lan_defaults_applied is True

    # Explicit user choice survives later loads/saves.
    cfg.routing.default_strategy = "local_first"
    ensure_lan_mesh_defaults(cfg)
    assert cfg.routing.default_strategy == "local_first"


def test_shard_context_ignores_plain_user_field() -> None:
    from netllm_agent.shard import extract_shard_context

    assert extract_shard_context({"user": "customer-42"}, {}) is None
    ctx = extract_shard_context({"user": "netllm:batchX:3"}, {})
    assert ctx is not None
    assert ctx.batch_id == "batchX"
    assert ctx.index == 3


def test_swarm_registry_remembers_lost_peers() -> None:
    from netllm_discovery.swarm import PeerRecord, SwarmRegistry

    cfg = NetllmConfig()
    cfg.swarm.peer_stale_after_s = 0.01
    reg = SwarmRegistry(cfg)
    reg.register_peer(
        PeerRecord(agent_id="remote", listen_url="http://192.168.1.11:11400")
    )
    assert reg.lost_peer_urls() == []
    import time

    time.sleep(0.02)
    reg.prune_stale()
    assert reg.peers == {}
    # The URL is remembered for re-discovery.
    assert reg.lost_peer_urls() == ["http://192.168.1.11:11400"]


def test_config_json_import_merges_over_existing(tmp_path) -> None:
    """A partial import (e.g. from the macOS app's typed structs) must
    not wipe fields the client doesn't model."""
    from netllm_cli.config_json import export_config, import_config
    from netllm_core.models import load_config, save_config

    path = tmp_path / "config.toml"
    cfg = NetllmConfig()
    cfg.routing.model_aliases = {"canon": ["real-id"]}
    cfg.routing.spillover_max_local_in_flight = 5
    save_config(cfg, path)

    data = export_config(path)
    del data["routing"]["model_aliases"]
    del data["routing"]["spillover_max_local_in_flight"]
    data["routing"]["default_strategy"] = "round_robin"
    import_config(data, path)

    loaded = load_config(path)
    assert loaded.routing.default_strategy == "round_robin"
    assert loaded.routing.model_aliases == {"canon": ["real-id"]}
    assert loaded.routing.spillover_max_local_in_flight == 5


def test_listen_validation_rejects_malformed() -> None:
    import pytest as _pytest

    NetllmConfig(agent={"listen": "127.0.0.1:11400"})
    NetllmConfig(agent={"listen": "[::]:11400"})
    for bad in ("no-port", "host:notaport", ":11400", "host:0", ""):
        with _pytest.raises(Exception):
            NetllmConfig(agent={"listen": bad})


def test_routed_counts_incremented_on_success() -> None:
    pool = RouterPool()
    b = _local()
    pool.set_backends([b])
    pool.mark_success(b)
    pool.mark_success(b)
    assert pool.routed_counts == {"local": 2}


def test_inference_token_gate() -> None:
    from unittest.mock import patch as _patch

    from fastapi.testclient import TestClient
    from netllm_agent.app import create_app

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.swarm.cluster_token = "sekrit"
    cfg.swarm.require_token_for_inference = True

    with _patch(
        "netllm_core.platform.local_admin_client_hosts",
        return_value=frozenset({"10.9.9.9"}),
    ):
        app = create_app(cfg)
        with TestClient(app) as client:
            # TestClient's host ("testclient") is not local → rejected.
            resp = client.get("/v1/models")
            assert resp.status_code == 401
            resp = client.get("/v1/models", headers={"Authorization": "Bearer sekrit"})
            assert resp.status_code == 200
            resp = client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 401


def test_peer_forward_uses_cluster_token() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.swarm.cluster_token = "sekrit"
    service = AgentService(cfg)
    assert service._upstream_api_key(_peer("remote")) == "sekrit"
    # Local providers keep their own key resolution (omlx default here).
    assert service._upstream_api_key(_local()) == "omlx-local"
    local_custom = Backend(id="c", base_url="http://127.0.0.1:9000/v1", local=True)
    assert service._upstream_api_key(local_custom) == "netllm-local"


def test_openai_upstream_client_reused() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    service = AgentService(cfg)
    peer = _peer("remote")
    a = service._openai_upstream(peer, {"x-netllm-hops": "0"})
    b = service._openai_upstream(peer, {"x-netllm-hops": "0"})
    assert a is b
    # Different hop depth → different forward headers → distinct client.
    c = service._openai_upstream(peer, {"x-netllm-hops": "1"})
    assert c is not a
