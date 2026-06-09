"""Routing policy match and cloud guardrails."""

from __future__ import annotations

from netllm_core.models import NetllmConfig, RoutingPolicy
from netllm_core.pool import RouterPool
from netllm_core.routing_policy import match_routing_policy, resolve_routing


def test_match_policy_by_model_prefix_and_api_format() -> None:
    policies = [
        RoutingPolicy(
            name="cloud-gpt",
            model_prefix="gpt-",
            api_format="openai",
            allow_cloud=True,
            strategy="failover",
        )
    ]
    matched = match_routing_policy(policies, model="gpt-4o-mini", api_format="openai")
    assert matched is not None
    assert matched.name == "cloud-gpt"
    assert match_routing_policy(policies, model="llama-3", api_format="openai") is None


def test_resolve_routing_forces_local_without_allow_cloud() -> None:
    cfg = NetllmConfig()
    cfg.routing.policies = [
        RoutingPolicy(name="local-only", api_format="openai", allow_cloud=False)
    ]
    resolved = resolve_routing(
        cfg.routing,
        model="any-model",
        api_format="openai",
        header_local_only=False,
    )
    assert resolved.local_only is True
    assert resolved.allow_cloud_inject is False


def test_resolve_routing_allow_cloud_policy() -> None:
    cfg = NetllmConfig()
    cfg.routing.policies = [
        RoutingPolicy(
            name="cloud",
            model_prefix="gpt-",
            api_format="openai",
            allow_cloud=True,
            strategy="failover",
        )
    ]
    resolved = resolve_routing(
        cfg.routing,
        model="gpt-4",
        api_format="openai",
        header_local_only=False,
    )
    assert resolved.local_only is False
    assert resolved.allow_cloud_inject is True
    assert resolved.strategy == "failover"


def test_prefer_provider_filters_pool_selection() -> None:
    from netllm_core.models import Backend, BackendHealth

    pool = RouterPool()
    pool.merge_backends(
        [
            Backend(
                id="1",
                base_url="http://127.0.0.1:11434/v1",
                provider="ollama",
                health=BackendHealth(status="online", models=["m"]),
            ),
            Backend(
                id="2",
                base_url="http://127.0.0.1:8080/v1",
                provider="omlx",
                health=BackendHealth(status="online", models=["m"]),
            ),
        ]
    )
    selected = pool.select_backend(
        "m",
        "local_first",
        prefer_provider="omlx",
    )
    assert selected is not None
    assert selected.provider == "omlx"
