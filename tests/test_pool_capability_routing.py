"""Runtime pool-aware alias steering and capability-class fallback."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.model_resolution import ModelResolver
from netllm_core.models import Backend, BackendHealth, ModelPool
from netllm_core.pool import RouterPool

_MOCK_ONLINE = {"status": "online", "models": ["whatever"], "model_count": 1}


def _probe_openai_mock(url: str, api_key: str | None = None) -> dict[str, object]:
    if "8015" in url:
        return {
            "status": "online",
            "models": ["nemotron-3.5-lightning-30b"],
            "model_count": 1,
        }
    return _MOCK_ONLINE


def test_is_steering_alias_detects_cross_tier_pool_mapping() -> None:
    resolver = ModelResolver(
        model_aliases={
            "gemma-4-e4b-it-8bit": [
                "gemma-4-e4b-it-8bit",
                "gemma4:e4b",
                "nemotron-3.5-lightning-30b",
            ],
        },
        model_pools={
            "mixed": ModelPool(
                enabled=True,
                hosts=["local"],
                models=[
                    "gemma-4-e4b-it-8bit",
                    "nemotron-3.5-lightning-30b",
                ],
            ),
        },
    )
    assert resolver.is_steering_alias(
        "gemma-4-e4b-it-8bit", "nemotron-3.5-lightning-30b"
    )
    assert not resolver.is_steering_alias("gemma-4-e4b-it-8bit", "gemma4:e4b")


def test_equivalent_gemma_aliases_not_steering() -> None:
    resolver = ModelResolver(
        model_aliases={
            "gemma4:26b": [
                "gemma4:26b",
                "gemma-4-26B-A4B-it-MLX-6bit",
            ],
        },
        model_pools={
            "pool": ModelPool(
                enabled=True,
                hosts=["peer"],
                models=["gemma4:26b", "gemma-4-26B-A4B-it-MLX-6bit"],
            ),
        },
    )
    assert not resolver.is_steering_alias("gemma4:26b", "gemma-4-26B-A4B-it-MLX-6bit")


@patch("netllm_core.pool.probe_openai_compat_sync", side_effect=_probe_openai_mock)
def test_pool_literal_prefers_peer_over_cross_tier_local_alias(_mock: object) -> None:
    """Issue #69: cross-tier alias must not pin Gemma pool traffic on local Nemotron."""
    pool = RouterPool(
        spillover_max_local_in_flight=8,
        model_aliases={
            "gemma-4-e4b-it-8bit": [
                "gemma-4-e4b-it-8bit",
                "gemma4:e4b",
                "nemotron-3.5-lightning-30b",
            ],
        },
        model_pools={
            "mixed": ModelPool(
                enabled=True,
                hosts=["vllm-local", "peer-remote"],
                models=[
                    "gemma-4-e4b-it-8bit",
                    "gemma4:e4b",
                    "nemotron-3.5-lightning-30b",
                ],
            ),
        },
    )
    pool.set_backends(
        [
            Backend(
                id="vllm-local",
                base_url="http://127.0.0.1:8015/v1",
                provider="vllm",
                local=True,
                in_flight=0,
                health=BackendHealth(
                    models=["nemotron-3.5-lightning-30b"], status="online"
                ),
            ),
            Backend(
                id="peer:peer-remote",
                base_url="http://10.0.0.32:11400/v1",
                provider="custom",
                local=False,
                agent_id="peer-remote",
                in_flight=0,
                health=BackendHealth(models=["gemma-4-e4b-it-8bit"], status="online"),
            ),
        ]
    )
    selected = pool.select_backend(
        "gemma-4-e4b-it-8bit",
        "local_spillover",
        required_capability="chat",
    )
    assert selected is not None
    assert selected.id == "peer:peer-remote"


@patch("netllm_core.pool.probe_openai_compat_sync", side_effect=_probe_openai_mock)
def test_capability_fallback_routes_pool_scoped_unmatched_name(_mock: object) -> None:
    pool = RouterPool(
        spillover_max_local_in_flight=2,
        model_pools={
            "mixed": ModelPool(
                enabled=True,
                hosts=["peer-remote"],
                models=["orphan-pool-chat", "gemma-4-26b-a4b-it-4bit"],
            ),
        },
    )
    pool.set_backends(
        [
            Backend(
                id="peer:peer-remote",
                base_url="http://10.0.0.32:11400/v1",
                provider="custom",
                local=False,
                agent_id="peer-remote",
                in_flight=0,
                health=BackendHealth(
                    models=["gemma-4-26b-a4b-it-4bit"], status="online"
                ),
            ),
        ]
    )
    with (
        patch.object(pool, "_select_local_spillover_pooled", return_value=None),
    ):
        real_bf = pool.backends_for_model

        def _backends_for_model(model: str, **kwargs: object) -> list[Backend]:
            if model == "orphan-pool-chat":
                return []
            return real_bf(model, **kwargs)  # type: ignore[arg-type]

        with patch.object(pool, "backends_for_model", side_effect=_backends_for_model):
            selected = pool.select_backend(
                "orphan-pool-chat",
                "local_spillover",
                required_capability="chat",
            )
    assert selected is not None
    assert selected.id == "peer:peer-remote"
    assert pool.capability_fallbacks == 1


@patch("netllm_core.pool.probe_openai_compat_sync", side_effect=_probe_openai_mock)
def test_capability_fallback_skips_embedding_surface(_mock: object) -> None:
    pool = RouterPool(
        model_pools={
            "mixed": ModelPool(
                enabled=True,
                hosts=["peer-remote"],
                models=["gemma-4-26b-a4b-it-4bit"],
            ),
        },
    )
    pool.set_backends(
        [
            Backend(
                id="peer:peer-remote",
                base_url="http://10.0.0.32:11400/v1",
                provider="custom",
                local=False,
                agent_id="peer-remote",
                health=BackendHealth(
                    models=["gemma-4-26b-a4b-it-4bit"], status="online"
                ),
            ),
        ]
    )
    picked = pool._select_capability_pool_fallback(
        "gemma-4-26b-a4b-it-4bit",
        "embedding",
        strategy="local_spillover",
        shard_key=None,
        attempt=1,
        local_only=False,
        exact_model_only=False,
        prefer_provider=None,
        prefer_cloud=False,
        exclude_ids=None,
        cloud_provider_allowlist=None,
        extra_candidates=None,
    )
    assert picked is None
    assert pool.capability_fallbacks == 0
