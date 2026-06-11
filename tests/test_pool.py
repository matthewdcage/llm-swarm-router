"""Tests for netllm-core routing pool."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import Backend, BackendHealth
from netllm_core.pool import BatchDedupLedger, RouterPool, _stable_shard_index

_MOCK_ONLINE = {"status": "online", "models": ["m"], "model_count": 1}


def test_backend_resolve_api_key_defaults_omlx() -> None:
    backend = Backend(id="x", base_url="http://127.0.0.1:8080/v1", provider="omlx")
    assert backend.resolve_api_key() == "omlx-local"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_is_healthy_uses_default_omlx_api_key(mock_probe: object) -> None:
    backend = Backend(
        id="x",
        base_url="http://127.0.0.1:8080/v1",
        provider="omlx",
    )
    pool = RouterPool()
    pool.set_backends([backend])
    assert pool.is_healthy(backend) is True
    mock_probe.assert_called_once_with("http://127.0.0.1:8080/v1", api_key="omlx-local")


def test_stable_shard_index_deterministic() -> None:
    a = _stable_shard_index("session-1", 3)
    b = _stable_shard_index("session-1", 3)
    assert a == b
    assert 0 <= a < 3


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_batch_shard_plan(_mock: object) -> None:
    pool = RouterPool()
    pool.set_backends(
        [
            Backend(
                id="a",
                base_url="http://a/v1",
                health=BackendHealth(models=["m"], status="online"),
            ),
            Backend(
                id="b",
                base_url="http://b/v1",
                health=BackendHealth(models=["m"], status="online"),
            ),
        ]
    )
    plan = pool.plan_batch_shard("m", 4, strategy="batch_shard")
    assert plan.assignments[0] == "http://a/v1"
    assert plan.assignments[1] == "http://b/v1"
    assert plan.assignments[2] == "http://a/v1"
    assert plan.assignments[3] == "http://b/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_failover_select_advances(_mock: object) -> None:
    pool = RouterPool()
    pool.set_backends(
        [
            Backend(
                id="a", base_url="http://a/v1", health=BackendHealth(status="online")
            ),
            Backend(
                id="b", base_url="http://b/v1", health=BackendHealth(status="online")
            ),
        ]
    )
    first = pool.select_backend("any", "failover", attempt=1)
    second = pool.select_backend("any", "failover", attempt=2)
    assert first is not None and second is not None
    assert first.base_url == "http://a/v1"
    assert second.base_url == "http://b/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_dedup_ledger_reassign(_mock: object) -> None:
    ledger = BatchDedupLedger()
    backends = [
        Backend(id="a", base_url="http://a/v1"),
        Backend(id="b", base_url="http://b/v1"),
    ]
    ledger.assignments[0] = "http://a/v1"
    next_url = ledger.reassign_failed(0, backends, current_url="http://a/v1")
    assert next_url == "http://b/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_latency_weighted_prefers_lower_latency(_mock: object) -> None:
    pool = RouterPool()
    fast = Backend(
        id="a",
        base_url="http://a/v1",
        latency_ema_ms=50.0,
        health=BackendHealth(models=["m"], status="online"),
    )
    slow = Backend(
        id="b",
        base_url="http://b/v1",
        latency_ema_ms=500.0,
        health=BackendHealth(models=["m"], status="online"),
    )
    pool.set_backends([fast, slow])
    selected = pool.select_backend("m", "latency_weighted")
    assert selected is not None
    assert selected.base_url == "http://a/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_local_first_prefers_local_backend(_mock: object) -> None:
    pool = RouterPool()
    local = Backend(
        id="local",
        base_url="http://127.0.0.1:8080/v1",
        local=True,
        health=BackendHealth(models=["m"], status="online"),
    )
    remote = Backend(
        id="remote",
        base_url="http://192.168.1.50:8080/v1",
        local=False,
        health=BackendHealth(models=["m"], status="online"),
    )
    pool.set_backends([remote, local])
    selected = pool.select_backend("m", "local_first")
    assert selected is not None
    assert selected.local is True


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_round_robin_alternates_local_and_peer_agent(_mock: object) -> None:
    pool = RouterPool()
    local = Backend(
        id="local",
        base_url="http://127.0.0.1:8080/v1",
        local=True,
        health=BackendHealth(models=["shared-model"], status="online"),
    )
    peer = Backend(
        id="peer:remote",
        base_url="http://192.168.1.11:11400/v1",
        local=False,
        provider="custom",
        health=BackendHealth(models=["shared-model"], status="online"),
    )
    pool.set_backends([local, peer])
    first = pool.select_backend("shared-model", "round_robin")
    second = pool.select_backend("shared-model", "round_robin")
    assert first is not None and second is not None
    urls = {first.base_url, second.base_url}
    assert urls == {
        "http://127.0.0.1:8080/v1",
        "http://192.168.1.11:11400/v1",
    }


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_least_load_selects_lowest_in_flight(_mock: object) -> None:
    pool = RouterPool()
    a = Backend(id="a", base_url="http://a/v1", in_flight=5)
    b = Backend(id="b", base_url="http://b/v1", in_flight=1)
    pool.set_backends([a, b])
    selected = pool.select_backend("m", "least_load")
    assert selected is not None
    assert selected.base_url == "http://b/v1"
