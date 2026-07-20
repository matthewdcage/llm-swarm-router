"""Configurable routing policies — explicit cloud rules, local-first defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args

from netllm_core.models import ApiFormat, RoutingConfig, RoutingPolicy, RoutingStrategy

VALID_STRATEGIES: frozenset[str] = frozenset(get_args(RoutingStrategy))


@dataclass(frozen=True)
class ResolvedRouting:
    strategy: RoutingStrategy
    local_only: bool
    allow_cloud_inject: bool
    prefer_provider: str | None
    # Per-request backend pin (x-netllm-backend): backend id,
    # peer agent id, or base URL. Wins over strategy on attempt 1.
    pinned_backend: str | None = None


def match_routing_policy(
    policies: list[RoutingPolicy],
    *,
    model: str,
    api_format: ApiFormat,
) -> RoutingPolicy | None:
    """Return the first enabled policy that matches the request."""
    for policy in policies:
        if not policy.enabled:
            continue
        if policy.model_prefix and not model.startswith(policy.model_prefix):
            continue
        if policy.api_format is not None and policy.api_format != api_format:
            continue
        return policy
    return None


def resolve_routing(
    routing: RoutingConfig,
    *,
    model: str,
    api_format: ApiFormat,
    header_local_only: bool,
    header_strategy: str | None = None,
    header_backend: str | None = None,
) -> ResolvedRouting:
    """Merge default routing config, optional policy match, and
    per-request header overrides (strategy header wins over policy)."""
    strategy: RoutingStrategy = routing.default_strategy
    local_only = header_local_only
    allow_cloud_inject = not header_local_only
    prefer_provider: str | None = None

    policy = match_routing_policy(routing.policies, model=model, api_format=api_format)
    if policy is not None:
        if policy.strategy is not None:
            strategy = policy.strategy
        if policy.prefer_provider:
            prefer_provider = policy.prefer_provider
        if policy.allow_cloud:
            allow_cloud_inject = True
        else:
            local_only = True
            allow_cloud_inject = False

    if header_strategy:
        requested = header_strategy.strip().lower()
        if requested in VALID_STRATEGIES:
            strategy = requested  # type: ignore[assignment]

    pinned = (header_backend or "").strip() or None

    return ResolvedRouting(
        strategy=strategy,
        local_only=local_only,
        allow_cloud_inject=allow_cloud_inject,
        prefer_provider=prefer_provider,
        pinned_backend=pinned,
    )
