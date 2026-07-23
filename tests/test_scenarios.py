"""Scenario classification (docs/cli-source-routing-plan.md Phase 3) —
long_context/web_search/think/background classification, per-scenario
source overrides, and precedence against source defaults."""

from __future__ import annotations

from netllm_core.models import NetllmConfig, ScenarioRule, SourceConfig
from netllm_core.routing_policy import resolve_routing
from netllm_core.scenarios import classify_scenario


def test_default_scenario_for_plain_request() -> None:
    assert classify_scenario({"messages": [{"role": "user", "content": "hi"}]},
                              api_format="openai") == "default"


def test_long_context_from_estimated_token_count() -> None:
    big_text = "x" * (4 * 40_000)  # ~40k estimated tokens at 4 chars/token
    payload = {"messages": [{"role": "user", "content": big_text}]}
    assert classify_scenario(payload, api_format="openai") == "long_context"


def test_long_context_threshold_is_configurable() -> None:
    text = "x" * (4 * 100)  # ~100 tokens
    payload = {"messages": [{"role": "user", "content": text}]}
    assert (
        classify_scenario(
            payload, api_format="openai", long_context_token_threshold=50
        )
        == "long_context"
    )
    assert (
        classify_scenario(
            payload, api_format="openai", long_context_token_threshold=1000
        )
        != "long_context"
    )


def test_long_context_counts_anthropic_content_blocks_and_system() -> None:
    payload = {
        "system": "s" * (4 * 20_000),
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "t" * (4 * 20_000)}],
            }
        ],
    }
    assert classify_scenario(payload, api_format="anthropic") == "long_context"


def test_web_search_tool_detected_openai_shape() -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "web_search"}}],
    }
    assert classify_scenario(payload, api_format="openai") == "web_search"


def test_web_search_tool_detected_anthropic_shape() -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    assert classify_scenario(payload, api_format="anthropic") == "web_search"


def test_think_from_anthropic_thinking_param() -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 4096},
    }
    assert classify_scenario(payload, api_format="anthropic") == "think"


def test_think_disabled_thinking_is_not_think() -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "disabled"},
    }
    assert classify_scenario(payload, api_format="anthropic") == "default"


def test_think_from_openai_reasoning_effort() -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "high",
    }
    assert classify_scenario(payload, api_format="openai") == "think"


def test_background_from_cheap_model_and_small_max_tokens() -> None:
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert classify_scenario(payload, api_format="anthropic") == "background"


def test_background_not_triggered_by_cheap_model_alone() -> None:
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert classify_scenario(payload, api_format="anthropic") == "default"


def test_background_from_claude_code_user_agent_and_small_budget() -> None:
    payload = {
        "model": "claude-sonnet-5",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert (
        classify_scenario(payload, api_format="anthropic", user_agent="claude-code/1.0")
        == "background"
    )


def test_priority_long_context_beats_think_and_web_search() -> None:
    big_text = "x" * (4 * 40_000)
    payload = {
        "messages": [{"role": "user", "content": big_text}],
        "thinking": {"type": "enabled"},
        "tools": [{"type": "web_search_20250305"}],
    }
    assert classify_scenario(payload, api_format="anthropic") == "long_context"


def test_priority_web_search_beats_think() -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled"},
        "tools": [{"type": "web_search_20250305"}],
    }
    assert classify_scenario(payload, api_format="anthropic") == "web_search"


# --- Resolution: scenario rule vs. source defaults vs. headers ---


def test_scenario_rule_overrides_source_default_strategy() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(
        id="buzz",
        strategy="local_first",
        scenarios={"background": ScenarioRule(strategy="round_robin")},
    )
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
        scenario="background",
    )
    assert resolved.strategy == "round_robin"


def test_scenario_rule_only_applies_for_matching_scenario() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(
        id="buzz",
        strategy="local_first",
        scenarios={"background": ScenarioRule(strategy="round_robin")},
    )
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
        scenario="think",
    )
    assert resolved.strategy == "local_first"


def test_scenario_rule_allow_cloud_reverses_source_local_only() -> None:
    """A scenario rule can reopen cloud even if the source's own default
    is local_only=False but a *different* signal would otherwise apply --
    demonstrates the rule is applied strictly after source defaults."""
    cfg = NetllmConfig()
    source = SourceConfig(
        id="buzz",
        scenarios={"think": ScenarioRule(allow_cloud=True)},
    )
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
        scenario="think",
    )
    assert resolved.local_only is False
    assert resolved.allow_cloud_inject is True


def test_scenario_rule_local_only_wins_over_source_allow_cloud() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(
        id="buzz",
        allow_cloud=True,
        scenarios={"background": ScenarioRule(local_only=True)},
    )
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
        scenario="background",
    )
    assert resolved.local_only is True
    assert resolved.allow_cloud_inject is False


def test_header_strategy_wins_over_scenario_rule() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(
        id="buzz",
        scenarios={"background": ScenarioRule(strategy="round_robin")},
    )
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        header_strategy="failover",
        source=source,
        scenario="background",
    )
    assert resolved.strategy == "failover"


def test_no_scenario_or_no_source_is_a_no_op() -> None:
    cfg = NetllmConfig()
    source = SourceConfig(
        id="buzz",
        scenarios={"background": ScenarioRule(strategy="round_robin")},
    )
    # No scenario passed.
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        source=source,
    )
    assert resolved.strategy == cfg.routing.default_strategy

    # No source at all.
    resolved = resolve_routing(
        cfg.routing,
        model="any",
        api_format="openai",
        header_local_only=False,
        scenario="background",
    )
    assert resolved.strategy == cfg.routing.default_strategy


# --- Service-level: scenario model override ---


def test_apply_scenario_model_overrides_rewritten_model() -> None:
    from netllm_agent.service import AgentService

    source = SourceConfig(
        id="buzz",
        model_rewrites={"claude-sonnet-5": "qwen3:32b"},
        scenarios={"background": ScenarioRule(model="qwen3:4b")},
    )
    rewritten = AgentService._apply_source_model_rewrite(source, "claude-sonnet-5")
    assert rewritten == "qwen3:32b"
    final = AgentService._apply_scenario_model(source, "background", rewritten)
    assert final == "qwen3:4b"
    # A scenario with no rule for this scenario name is a no-op.
    assert AgentService._apply_scenario_model(source, "think", rewritten) == rewritten
    assert AgentService._apply_scenario_model(None, "background", rewritten) == (
        rewritten
    )


def test_service_classify_and_record_scenario_counts() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    service = AgentService(cfg)
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
    }
    scenario = service._classify_and_record_scenario(
        payload, api_format="anthropic", source_id="default", headers={}
    )
    assert scenario == "background"
    assert service._scenario_counts[("default", "background")] == 1
    assert service.status_payload()["scenario_requests"] == {"default:background": 1}
