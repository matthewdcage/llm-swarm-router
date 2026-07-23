"""Per-source routing overrides (docs/cli-source-routing-plan.md Phase 2):
strategy/local_only/allow_cloud/prefer_provider precedence, source-scoped
policy matching, source model_rewrites, per-source concurrency admission
control, and the cloud_providers allowlist."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import (
    Backend,
    BackendHealth,
    CloudConfig,
    NetllmConfig,
    RoutingPolicy,
    SourceConfig,
)
from netllm_core.pool import RouterPool
from netllm_core.routing_policy import resolve_routing

_MOCK_ONLINE = {"status": "online", "models": ["m"], "model_count": 1}


def test_source_strategy_overrides_default() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", strategy="round_robin")
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.strategy == "round_robin"


def test_source_strategy_wins_over_matching_policy() -> None:
    """Precedence: source defaults rank above routing.policies."""
    cfg = NetllmConfig()
    cfg.routing.policies = [RoutingPolicy(strategy="failover", allow_cloud=True)]
    source = SourceConfig(id="buzz", strategy="local_spillover")
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.strategy == "local_spillover"


def test_header_strategy_wins_over_source_strategy() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", strategy="local_spillover")
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        header_strategy="round_robin",
        source=source,
    )
    assert resolved.strategy == "round_robin"


def test_source_local_only_forces_local_and_blocks_cloud() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", local_only=True)
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.local_only is True
    assert resolved.allow_cloud_inject is False


def test_source_allow_cloud_reverses_policy_forcing_local() -> None:
    """A source ranks above routing.policies: allow_cloud=True on the
    source can re-enable cloud even though the matching policy alone
    would have forced local-only."""
    cfg = NetllmConfig()
    cfg.routing.policies = [RoutingPolicy(allow_cloud=False)]
    source = SourceConfig(id="buzz", allow_cloud=True)
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.local_only is False
    assert resolved.allow_cloud_inject is True


def test_header_local_only_is_an_absolute_ceiling_over_source() -> None:
    """The caller's explicit x-netllm-local-only wins over everything,
    including a source that requests allow_cloud=True."""
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", allow_cloud=True)
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=True,
        source=source,
    )
    assert resolved.local_only is True
    assert resolved.allow_cloud_inject is False


def test_source_prefer_provider() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", prefer_provider="ollama")
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.prefer_provider == "ollama"


def test_source_cloud_providers_sets_allowlist() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", allow_cloud=True, cloud_providers=["openrouter"])
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.cloud_provider_allowlist == frozenset({"openrouter"})


def test_policy_scoped_to_source_only_matches_that_source() -> None:
    cfg = NetllmConfig()
    cfg.routing.policies = [
        RoutingPolicy(source="buzz", strategy="round_robin", allow_cloud=True)
    ]
    other_source = SourceConfig(id="codex")
    resolved_other = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=other_source,
    )
    # Policy doesn't match "codex" -- global default strategy applies.
    assert resolved_other.strategy == cfg.routing.default_strategy

    buzz_source = SourceConfig(id="buzz")
    resolved_buzz = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=buzz_source,
    )
    assert resolved_buzz.strategy == "round_robin"


def test_unscoped_policy_still_matches_any_source() -> None:
    """Backward compat: policies written before source identity existed
    (empty `source`) keep matching regardless of caller."""
    cfg = NetllmConfig()
    cfg.routing.policies = [RoutingPolicy(strategy="failover")]
    source = SourceConfig(id="buzz")
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.strategy == "failover"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_cloud_provider_allowlist_excludes_non_allowlisted_cloud_backend(
    mock_probe: object,
) -> None:
    pool = RouterPool()
    pool.merge_backends(
        [
            Backend(
                id="local",
                base_url="http://127.0.0.1:11434/v1",
                provider="ollama",
                local=True,
                health=BackendHealth(status="online", models=["m"]),
            ),
            Backend(
                id="moonshot",
                base_url="https://api.moonshot.ai/v1",
                provider="custom",
                local=False,
                cloud_provider="moonshot",
                health=BackendHealth(status="online", models=["m"]),
            ),
            Backend(
                id="openrouter",
                base_url="https://openrouter.ai/api/v1",
                provider="custom",
                local=False,
                cloud_provider="openrouter",
                health=BackendHealth(status="online", models=["m"]),
            ),
        ]
    )
    selected = pool.select_backend(
        "m",
        "round_robin",
        prefer_cloud=True,
        cloud_provider_allowlist=frozenset({"openrouter"}),
    )
    assert selected is not None
    assert selected.cloud_provider == "openrouter"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_cloud_provider_allowlist_never_excludes_local_backends(
    mock_probe: object,
) -> None:
    pool = RouterPool()
    pool.merge_backends(
        [
            Backend(
                id="local",
                base_url="http://127.0.0.1:11434/v1",
                provider="ollama",
                local=True,
                health=BackendHealth(status="online", models=["m"]),
            ),
            Backend(
                id="moonshot",
                base_url="https://api.moonshot.ai/v1",
                provider="custom",
                local=False,
                cloud_provider="moonshot",
                health=BackendHealth(status="online", models=["m"]),
            ),
        ]
    )
    # Allowlist names a provider that isn't configured at all; the local
    # backend must still be selectable (allowlist only narrows cloud rows).
    selected = pool.select_backend(
        "m",
        "local_first",
        cloud_provider_allowlist=frozenset({"some-other-provider"}),
    )
    assert selected is not None
    assert selected.id == "local"


def test_apply_source_model_rewrite() -> None:
    from netllm_agent.service import AgentService

    source = SourceConfig(id="buzz", model_rewrites={"claude-sonnet-5": "qwen3:32b"})
    assert (
        AgentService._apply_source_model_rewrite(source, "claude-sonnet-5")
        == "qwen3:32b"
    )
    assert AgentService._apply_source_model_rewrite(source, "other-model") == (
        "other-model"
    )
    assert AgentService._apply_source_model_rewrite(None, "other-model") == (
        "other-model"
    )


def test_source_capacity_admission_control() -> None:
    from netllm_agent.service import AgentService, SourceCapacityExceeded

    cfg = NetllmConfig()
    cfg.routing.sources = [SourceConfig(id="buzz", max_concurrency=1)]
    service = AgentService(cfg)
    source_cfg = service._source_config("buzz")

    # Under the cap: no error.
    service._check_source_capacity("buzz", source_cfg)
    service._source_acquire("buzz")

    # At the cap: rejected.
    try:
        service._check_source_capacity("buzz", source_cfg)
        raise AssertionError("expected SourceCapacityExceeded")
    except SourceCapacityExceeded as exc:
        assert exc.source_id == "buzz"
        assert exc.limit == 1

    # Released: capacity available again.
    service._source_release("buzz")
    service._check_source_capacity("buzz", source_cfg)


def test_source_without_max_concurrency_is_never_capped() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.routing.sources = [SourceConfig(id="buzz")]
    service = AgentService(cfg)
    source_cfg = service._source_config("buzz")
    for _ in range(50):
        service._source_acquire("buzz")
    service._check_source_capacity("buzz", source_cfg)  # never raises


def test_cloud_disabled_master_switch_still_wins_over_source_allow_cloud() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(id="buzz", allow_cloud=True)
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
        cloud=CloudConfig(enabled=False),
    )
    assert resolved.allow_cloud_inject is False
