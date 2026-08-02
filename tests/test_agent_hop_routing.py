"""Agent-hop routing must not re-apply pool catch-all substitution."""

from __future__ import annotations

from unittest.mock import patch

from netllm_core.models import Backend, BackendHealth, ModelPool
from netllm_core.pool import RouterPool

_MOCK_ONLINE = {"status": "online", "models": ["whatever"], "model_count": 1}


def _backend(bid: str, models: list[str]) -> Backend:
    return Backend(
        id=bid,
        base_url="http://a/v1",
        local=True,
        health=BackendHealth(status="online", models=models),
    )


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_exact_model_only_skips_pool_overflow(_mock: object) -> None:
    pool = RouterPool(
        model_pools={
            "mixed": ModelPool(
                enabled=True,
                hosts=["qwen-host"],
                models=["gemma4:26b", "qwen3-next-80b"],
            )
        }
    )
    qwen_host = _backend("qwen-host", ["qwen3-next-80b"])
    pool.set_backends([qwen_host])

    # Overflow would match gemma request to qwen-only host; exact mode skips.
    pooled = pool.backends_for_model("gemma4:26b")
    assert [b.id for b in pooled] == ["qwen-host"]

    exact = pool.backends_for_model("gemma4:26b", exact_model_only=True)
    assert exact == []
