"""Classify a proxied request into a routing scenario (Phase 3 of
docs/cli-source-routing-plan.md) so a source can route background chatter,
reasoning turns, oversized prompts, and web-search tool calls differently
-- the claude-code-router pattern, built natively on top of source identity
instead of an external proxy.

Classification is a cheap heuristic over the request body alone (no
tokenizer, no model call) -- it only ever narrows which SourceConfig.strategy/
model_rewrites apply for this one request; an unclassifiable or ambiguous
request always falls back to "default", the same as no scenario existing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, get_args

Scenario = Literal["long_context", "web_search", "think", "background", "default"]

VALID_SCENARIOS: frozenset[str] = frozenset(get_args(Scenario))

# Rough chars-per-token approximation (no tokenizer dependency) -- good
# enough to decide "is this prompt big enough to need a long-context
# model", not to bill or truncate anything.
_CHARS_PER_TOKEN_ESTIMATE = 4
DEFAULT_LONG_CONTEXT_TOKEN_THRESHOLD = 32_000
_BACKGROUND_MODEL_MARKERS = ("haiku", "mini", "flash", "nano")
_BACKGROUND_MAX_TOKENS_CEILING = 512
_WEB_SEARCH_MARKERS = ("web_search", "websearch", "web-search")


def _text_len(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    total += len(text)
            elif isinstance(block, str):
                total += len(block)
        return total
    return 0


def _estimate_prompt_tokens(payload: Mapping[str, Any]) -> int:
    chars = 0
    for message in payload.get("messages") or []:
        if isinstance(message, dict):
            chars += _text_len(message.get("content"))
    system = payload.get("system")
    if isinstance(system, str):
        chars += len(system)
    elif isinstance(system, list):
        chars += _text_len(system)
    return chars // _CHARS_PER_TOKEN_ESTIMATE


def _has_web_search_tool(payload: Mapping[str, Any]) -> bool:
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        haystack = " ".join(str(tool.get(key, "")) for key in ("type", "name")).lower()
        function = tool.get("function")
        if isinstance(function, dict):
            haystack += " " + str(function.get("name", "")).lower()
        if any(marker in haystack for marker in _WEB_SEARCH_MARKERS):
            return True
    return False


def _wants_thinking(payload: Mapping[str, Any], *, api_format: str) -> bool:
    if api_format == "anthropic":
        thinking = payload.get("thinking")
        return isinstance(thinking, dict) and thinking.get("type") == "enabled"
    effort = payload.get("reasoning_effort")
    if isinstance(effort, str) and effort.lower() not in ("", "none", "minimal"):
        return True
    return isinstance(payload.get("reasoning"), dict)


def _looks_like_background(payload: Mapping[str, Any], *, user_agent: str) -> bool:
    model = str(payload.get("model", "")).lower()
    max_tokens = payload.get("max_tokens") or payload.get("max_completion_tokens") or 0
    small_budget = (
        isinstance(max_tokens, int) and 0 < max_tokens <= _BACKGROUND_MAX_TOKENS_CEILING
    )
    cheap_model = any(marker in model for marker in _BACKGROUND_MODEL_MARKERS)
    if cheap_model and small_budget:
        return True
    # Claude Code's own background/haiku-tier sub-agent calls are the
    # documented reference case (see cli-routing-research.md); other
    # harnesses can opt in via the same UA substring convention used by
    # SourceMatch.
    return "claude-code" in user_agent.lower() and small_budget


def classify_scenario(
    payload: Mapping[str, Any],
    *,
    api_format: str,
    surface: str | None = None,
    user_agent: str = "",
    long_context_token_threshold: int = DEFAULT_LONG_CONTEXT_TOKEN_THRESHOLD,
) -> Scenario:
    """First matching signal wins, in priority order:

    1. long_context -- estimated prompt size over the threshold. A
       structural constraint (will this fit?), so it outranks intent
       signals like "think" or "web_search".
    2. web_search -- a web-search-shaped tool is present in the request.
    3. think -- Anthropic extended thinking enabled, or an OpenAI
       reasoning_effort/reasoning field present.
    4. background -- small max_tokens paired with a cheap-tier model name
       or a Claude Code sub-agent User-Agent. Weakest/heuristic signal,
       checked last.

    Anything else -> "default".

    [D14] `surface` ("chat" | "embeddings" | "messages") says which proxy
    surface is asking. Classification itself is deliberately unchanged by
    it: /v1/embeddings traffic still runs this chat-shaped heuristic and
    can still come out as "think" or "long_context", exactly as before, so
    existing scenario_counts and existing configs keep their meaning. What
    the surface gates is whether a matching `ScenarioRule` is allowed to
    fire (`ScenarioRule.applies_to`, consumed by `resolve_routing` and the
    plan builder's model override) — narrowing is opt-in, per rule. The
    parameter is threaded here rather than only at the rule so a future
    surface-specific classifier needs no further signature churn.
    """
    if _estimate_prompt_tokens(payload) >= long_context_token_threshold:
        return "long_context"
    if _has_web_search_tool(payload):
        return "web_search"
    if _wants_thinking(payload, api_format=api_format):
        return "think"
    if _looks_like_background(payload, user_agent=user_agent):
        return "background"
    return "default"
