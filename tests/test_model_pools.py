"""routing.model_pools: host-scoped catch-all pools that bypass alias
matching entirely for their member backends."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import Backend, BackendHealth, ModelPool, NetllmConfig
from netllm_core.pool import RouterPool

_MOCK_ONLINE = {"status": "online", "models": ["whatever"], "model_count": 1}

POOL_MODELS = ["qwen2.5:72b-instruct", "llama3.1:70b"]


def _backend(
    bid: str, url: str, models: list[str], *, agent_id: str = "", local: bool = True
) -> Backend:
    return Backend(
        id=bid,
        base_url=url,
        local=local,
        agent_id=agent_id,
        health=BackendHealth(status="online", models=models),
    )


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_pool_member_matches_any_requested_name(_mock: object) -> None:
    pool = RouterPool(
        model_pools={
            "big": ModelPool(enabled=True, hosts=["mac-studio"], models=POOL_MODELS)
        }
    )
    big_host = _backend("mac-studio", "http://a/v1", ["qwen2.5:72b-instruct"])
    other = _backend("other", "http://b/v1", ["qwen2"])
    pool.set_backends([big_host, other])
    matched = pool.backends_for_model("gpt-4o")
    assert [b.id for b in matched] == ["mac-studio"]


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_pool_disabled_does_not_bypass_matching(_mock: object) -> None:
    pool = RouterPool(
        model_pools={
            "big": ModelPool(enabled=False, hosts=["mac-studio"], models=POOL_MODELS)
        }
    )
    big_host = _backend("mac-studio", "http://a/v1", ["qwen2.5:72b-instruct"])
    pool.set_backends([big_host])
    assert pool.backends_for_model("gpt-4o") == []


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_pool_member_not_serving_pool_models_excluded(_mock: object) -> None:
    pool = RouterPool(
        model_pools={
            "big": ModelPool(enabled=True, hosts=["mac-studio"], models=POOL_MODELS)
        }
    )
    # Listed as a pool host, but its actual catalog has none of the
    # pool's allowed models — must not become a blind catch-all.
    unrelated = _backend("mac-studio", "http://a/v1", ["something-else"])
    pool.set_backends([unrelated])
    assert pool.backends_for_model("gpt-4o") == []


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_pool_host_ref_matches_agent_id_and_peer_prefix(_mock: object) -> None:
    pool = RouterPool(
        model_pools={
            "big": ModelPool(enabled=True, hosts=["abc123"], models=POOL_MODELS)
        }
    )
    peer = _backend(
        "peer:abc123", "http://a/v1", ["llama3.1:70b"], agent_id="abc123", local=False
    )
    pool.set_backends([peer])
    matched = pool.backends_for_model("anything")
    assert [b.id for b in matched] == ["peer:abc123"]


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_normal_alias_match_still_wins_without_pool_involvement(_mock: object) -> None:
    """Non-pool backends keep exact/alias matching semantics unchanged."""
    pool = RouterPool()
    exact = _backend("exact", "http://a/v1", ["qwen2"])
    pool.set_backends([exact])
    assert pool.backends_for_model("qwen2") == [exact]
    assert pool.backends_for_model("gpt-4o") == []


def test_resolve_via_pool_picks_first_served_pool_model() -> None:
    pool = RouterPool(
        model_pools={
            "big": ModelPool(enabled=True, hosts=["mac-studio"], models=POOL_MODELS)
        }
    )
    backend = _backend(
        "mac-studio", "http://a/v1", ["llama3.1:70b", "qwen2.5:72b-instruct"]
    )
    # POOL_MODELS order wins: "qwen2.5:72b-instruct" precedes "llama3.1:70b".
    assert pool.resolve_via_pool(backend, "gpt-4o") == "qwen2.5:72b-instruct"


def test_resolve_via_pool_none_for_non_member() -> None:
    pool = RouterPool(
        model_pools={
            "big": ModelPool(enabled=True, hosts=["mac-studio"], models=POOL_MODELS)
        }
    )
    other = _backend("other", "http://a/v1", ["qwen2.5:72b-instruct"])
    assert pool.resolve_via_pool(other, "gpt-4o") is None


def test_model_for_backend_falls_back_to_pool() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.routing.model_pools = {
        "big": ModelPool(enabled=True, hosts=["mac-studio"], models=POOL_MODELS)
    }
    service = AgentService(cfg)
    backend = _backend("mac-studio", "http://a/v1", ["qwen2.5:72b-instruct"])
    assert service._model_for_backend("gpt-4o", backend) == "qwen2.5:72b-instruct"


def test_model_for_backend_alias_wins_over_pool() -> None:
    """model_aliases resolution is tried first; the pool is only a
    fallback once alias matching finds nothing."""
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.routing.model_aliases = {"llama3": ["llama3.1:70b"]}
    cfg.routing.model_pools = {
        "big": ModelPool(enabled=True, hosts=["mac-studio"], models=POOL_MODELS)
    }
    service = AgentService(cfg)
    backend = _backend(
        "mac-studio", "http://a/v1", ["llama3.1:70b", "qwen2.5:72b-instruct"]
    )
    assert service._model_for_backend("llama3", backend) == "llama3.1:70b"
