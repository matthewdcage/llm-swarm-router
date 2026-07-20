"""Tests for netllm-core routing pool."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import Backend, BackendHealth
from netllm_core.pool import RouterPool, _stable_shard_index

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
    first = pool.select_backend("m", "failover", attempt=1)
    second = pool.select_backend("m", "failover", attempt=2)
    assert first is not None and second is not None
    assert first.base_url == "http://a/v1"
    assert second.base_url == "http://b/v1"


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


@patch(
    "netllm_core.pool.probe_openai_compat_sync",
    return_value={"status": "online", "models": ["shared-model"], "model_count": 1},
)
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


def _spillover_pool(local_in_flight: int, remote_in_flight: int) -> RouterPool:
    pool = RouterPool(spillover_max_local_in_flight=2)
    pool.set_backends(
        [
            Backend(
                id="local",
                base_url="http://127.0.0.1:8080/v1",
                local=True,
                in_flight=local_in_flight,
                health=BackendHealth(models=["m"], status="online"),
            ),
            Backend(
                id="peer:remote",
                base_url="http://192.168.1.11:11400/v1",
                provider="custom",
                local=False,
                in_flight=remote_in_flight,
                health=BackendHealth(models=["m"], status="online"),
            ),
        ]
    )
    return pool


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_local_spillover_idle_local_never_hops(_mock: object) -> None:
    pool = _spillover_pool(local_in_flight=0, remote_in_flight=0)
    selected = pool.select_backend("m", "local_spillover")
    assert selected is not None
    assert selected.local is True


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_local_spillover_saturated_local_spills_to_idle_peer(_mock: object) -> None:
    pool = _spillover_pool(local_in_flight=2, remote_in_flight=0)
    selected = pool.select_backend("m", "local_spillover")
    assert selected is not None
    assert selected.local is False
    assert selected.base_url == "http://192.168.1.11:11400/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_local_spillover_stays_local_when_peer_equally_busy(_mock: object) -> None:
    pool = _spillover_pool(local_in_flight=3, remote_in_flight=3)
    selected = pool.select_backend("m", "local_spillover")
    assert selected is not None
    assert selected.local is True


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_local_spillover_no_remote_stays_local_when_saturated(_mock: object) -> None:
    pool = RouterPool(spillover_max_local_in_flight=2)
    pool.set_backends(
        [
            Backend(
                id="local",
                base_url="http://127.0.0.1:8080/v1",
                local=True,
                in_flight=9,
                health=BackendHealth(models=["m"], status="online"),
            )
        ]
    )
    selected = pool.select_backend("m", "local_spillover")
    assert selected is not None
    assert selected.local is True


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_local_spillover_remote_only_uses_least_loaded(_mock: object) -> None:
    pool = RouterPool(spillover_max_local_in_flight=2)
    pool.set_backends(
        [
            Backend(
                id="peer:a",
                base_url="http://192.168.1.11:11400/v1",
                provider="custom",
                local=False,
                in_flight=4,
                health=BackendHealth(models=["m"], status="online"),
            ),
            Backend(
                id="peer:b",
                base_url="http://192.168.1.12:11400/v1",
                provider="custom",
                local=False,
                in_flight=1,
                health=BackendHealth(models=["m"], status="online"),
            ),
        ]
    )
    selected = pool.select_backend("m", "local_spillover")
    assert selected is not None
    assert selected.base_url == "http://192.168.1.12:11400/v1"


def test_merge_backends_adds_own_hops_to_peer_rows() -> None:
    """Peer rows are rebuilt from heartbeats every refresh; our active
    forwards must survive the rebuild and stale peer load must not stick."""
    pool = RouterPool()
    peer = Backend(
        id="peer:remote",
        base_url="http://192.168.1.11:11400/v1",
        provider="custom",
        local=False,
        in_flight=0,
    )
    pool.set_backends([peer])
    pool.acquire(peer)
    pool.acquire(peer)
    assert peer.in_flight == 2

    def fresh_row(reported: int) -> Backend:
        return Backend(
            id="peer:remote",
            base_url="http://192.168.1.11:11400/v1",
            provider="custom",
            local=False,
            in_flight=reported,
        )

    # Heartbeat reports 1 busy slot; our 2 active hops are added on top.
    pool.merge_backends([fresh_row(1)])
    assert pool.backends[0].in_flight == 3
    # Hops complete; the next heartbeat-reported value stands alone (no ratchet).
    pool.release(pool.backends[0])
    pool.release(pool.backends[0])
    pool.merge_backends([fresh_row(0)])
    assert pool.backends[0].in_flight == 0


def test_merge_backends_preserves_local_in_flight() -> None:
    pool = RouterPool()
    existing = Backend(
        id="omlx:x", base_url="http://127.0.0.1:8080/v1", local=True, in_flight=2
    )
    pool.set_backends([existing])
    pool.merge_backends(
        [
            Backend(
                id="omlx:x",
                base_url="http://127.0.0.1:8080/v1",
                local=True,
                in_flight=0,
            )
        ]
    )
    assert pool.backends[0].in_flight == 2


def test_merge_backends_keeps_local_object_identity() -> None:
    """A request in flight holds a Backend reference across pool refreshes.

    merge_backends must update the existing instance in place — replacing
    it would orphan the acquire() count (release() decrements the old
    object) and leak in_flight upward forever.
    """
    pool = RouterPool()
    existing = Backend(id="omlx:x", base_url="http://127.0.0.1:8080/v1", local=True)
    pool.set_backends([existing])
    pool.acquire(existing)
    assert existing.in_flight == 1

    # Scan refresh arrives mid-request with a fresh row for the same URL.
    pool.merge_backends(
        [
            Backend(
                id="omlx:x",
                base_url="http://127.0.0.1:8080/v1",
                local=True,
                health=BackendHealth(status="online", models=["m"]),
            )
        ]
    )
    assert pool.backends[0] is existing
    assert pool.backends[0].health.models == ["m"]

    pool.release(existing)
    assert pool.backends[0].in_flight == 0


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_select_backend_excludes_failed_backends(_mock: object) -> None:
    """Retries skip backends that already failed this request, so the
    attempt budget reaches a healthy peer instead of re-hitting locals."""
    pool = RouterPool()
    local_a = Backend(
        id="local-a",
        base_url="http://127.0.0.1:8080/v1",
        local=True,
        health=BackendHealth(status="online", models=["m"]),
    )
    local_b = Backend(
        id="local-b",
        base_url="http://127.0.0.1:1234/v1",
        local=True,
        health=BackendHealth(status="online", models=["m"]),
    )
    peer = Backend(
        id="peer:remote",
        base_url="http://192.168.1.11:11400/v1",
        local=False,
        health=BackendHealth(status="online", models=["m"]),
    )
    pool.set_backends([local_a, local_b, peer])

    first = pool.select_backend("m", "failover", attempt=1)
    assert first is local_a
    second = pool.select_backend("m", "failover", attempt=2, exclude_ids={"local-a"})
    assert second is local_b
    third = pool.select_backend(
        "m", "failover", attempt=3, exclude_ids={"local-a", "local-b"}
    )
    assert third is peer
    none_left = pool.select_backend(
        "m",
        "failover",
        attempt=4,
        exclude_ids={"local-a", "local-b", "peer:remote"},
    )
    assert none_left is None


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_backends_for_model_matches_case_insensitively(_mock: object) -> None:
    pool = RouterPool()
    backend = Backend(
        id="omlx",
        base_url="http://127.0.0.1:8080/v1",
        local=True,
        health=BackendHealth(
            status="online", models=["gemma-4-26B-A4B-it-assistant-4bit"]
        ),
    )
    pool.set_backends([backend])
    matched = pool.backends_for_model("gemma-4-26b-a4b-it-assistant-4bit")
    assert [b.id for b in matched] == ["omlx"]


def test_known_models_capability_filter() -> None:
    pool = RouterPool()
    backend = Backend(
        id="ollama",
        base_url="http://127.0.0.1:11434/v1",
        local=True,
        health=BackendHealth(status="online", models=["qwen2", "nomic-embed-text"]),
    )
    pool.set_backends([backend])
    assert pool.known_models(capability="embedding") == ["nomic-embed-text"]
    assert pool.known_models(capability="chat") == ["qwen2"]
