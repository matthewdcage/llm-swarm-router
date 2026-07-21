"""Configurable routing policies — explicit cloud rules, local-first defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args

from netllm_core.models import (
    ApiFormat,
    CloudConfig,
    RoutingConfig,
    RoutingPolicy,
    RoutingStrategy,
)

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
    # cloud.fallback = "local": try materialized cloud backends before the
    # local/peer mesh (mesh becomes the fallback tier). False (default)
    # preserves today's local-first-then-cloud order.
    cloud_leads: bool = False


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
    cloud: CloudConfig | None = None,
) -> ResolvedRouting:
    """Merge default routing config, optional policy match, and
    per-request header overrides (strategy header wins over policy).

    `cloud` (default CloudConfig(), i.e. today's behavior: enabled=True,
    fallback="cloud") gates the default cloud-allowed stance:
    - cloud.enabled=False: cloud never injected, no policy can override.
    - cloud.fallback="none" (or fallback_enabled=False): no *automatic*
      cloud fallback; an explicit policy with allow_cloud=True still opts
      a specific model/route in.
    - cloud.fallback="local": cloud_leads=True, so callers try
      materialized cloud backends before the local/peer mesh.
    """
    cloud = cloud or CloudConfig()
    strategy: RoutingStrategy = routing.default_strategy
    local_only = header_local_only
    cloud_master_on = cloud.enabled
    cloud_default_allowed = (
        cloud_master_on and cloud.fallback_enabled and cloud.fallback != "none"
    )
    allow_cloud_inject = cloud_default_allowed and not header_local_only
    cloud_leads = (
        cloud_master_on and cloud.fallback_enabled and cloud.fallback == "local"
    )
    prefer_provider: str | None = None

    policy = match_routing_policy(routing.policies, model=model, api_format=api_format)
    if policy is not None:
        if policy.strategy is not None:
            strategy = policy.strategy
        if policy.prefer_provider:
            prefer_provider = policy.prefer_provider
        if policy.allow_cloud:
            # An explicit per-route opt-in still requires the cloud master
            # switch to be on; it can override fallback="none" for this
            # specific route.
            allow_cloud_inject = cloud_master_on
        else:
            local_only = True
            allow_cloud_inject = False
            cloud_leads = False

    if header_strategy:
        requested = header_strategy.strip().lower()
        if requested in VALID_STRATEGIES:
            strategy = requested  # type: ignore[assignment]

    if header_local_only or local_only:
        cloud_leads = False

    pinned = (header_backend or "").strip() or None

    return ResolvedRouting(
        strategy=strategy,
        local_only=local_only,
        allow_cloud_inject=allow_cloud_inject,
        prefer_provider=prefer_provider,
        pinned_backend=pinned,
        cloud_leads=cloud_leads,
    )
