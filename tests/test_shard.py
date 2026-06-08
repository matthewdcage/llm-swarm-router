"""Tests for batch_shard context extraction and agent routing."""

from __future__ import annotations

from unittest.mock import patch

from netllm_agent.service import AgentService
from netllm_agent.shard import (
    BatchRequestLedger,
    extract_shard_context,
)
from netllm_core.models import Backend, BackendHealth, NetllmConfig
from netllm_core.pool import RouterPool, shard_index

_MOCK_ONLINE = {"status": "online", "models": ["m"], "model_count": 1}


def test_extract_shard_context_from_headers() -> None:
    ctx = extract_shard_context(
        {},
        {
            "X-Netllm-Batch-Id": "job-1",
            "X-Netllm-Shard-Index": "3",
        },
    )
    assert ctx is not None
    assert ctx.batch_id == "job-1"
    assert ctx.index == 3


def test_extract_shard_context_from_user_field() -> None:
    ctx = extract_shard_context({"user": "netllm:enrichment:5"}, {})
    assert ctx is not None
    assert ctx.batch_id == "enrichment"
    assert ctx.index == 5


def test_shard_index_numeric_matches_plan_batch_shard() -> None:
    assert shard_index("0", 2) == 0
    assert shard_index("1", 2) == 1
    assert shard_index("2", 2) == 0
    assert shard_index("3", 2) == 1


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_select_backend_batch_shard_uses_numeric_shard_key(_mock: object) -> None:
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
    for i in range(4):
        selected = pool.select_backend("m", "batch_shard", shard_key=str(i))
        assert selected is not None
        assert selected.base_url == plan.assignments[i]


def test_batch_request_ledger_reassign() -> None:
    ledger = BatchRequestLedger()
    backends = [
        Backend(id="a", base_url="http://a/v1"),
        Backend(id="b", base_url="http://b/v1"),
    ]
    first = ledger.assign("batch-1", 0, backends)
    assert first == "http://a/v1"
    second = ledger.reassign_failed("batch-1", 0, backends, current_url="http://a/v1")
    assert second == "http://b/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_agent_batch_shard_pins_index_to_backend(_mock: object) -> None:
    cfg = NetllmConfig()
    cfg.routing.default_strategy = "batch_shard"
    service = AgentService(cfg)
    service.pool.set_backends(
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
    shard = extract_shard_context(
        {},
        {"X-Netllm-Batch-Id": "run-1", "X-Netllm-Shard-Index": "1"},
    )
    backend = service._select_backend_for_request("m", "batch_shard", 1, shard)
    assert backend is not None
    assert backend.base_url == "http://b/v1"

    same = service._select_backend_for_request("m", "batch_shard", 1, shard)
    assert same is not None
    assert same.base_url == "http://b/v1"


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_agent_batch_shard_retries_on_next_backend(_mock: object) -> None:
    cfg = NetllmConfig()
    cfg.routing.default_strategy = "batch_shard"
    service = AgentService(cfg)
    service.pool.set_backends(
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
    shard = extract_shard_context(
        {},
        {"X-Netllm-Batch-Id": "run-2", "X-Netllm-Shard-Index": "0"},
    )
    first = service._select_backend_for_request("m", "batch_shard", 1, shard)
    assert first is not None
    assert first.base_url == "http://a/v1"

    second = service._select_backend_for_request("m", "batch_shard", 2, shard)
    assert second is not None
    assert second.base_url == "http://b/v1"
