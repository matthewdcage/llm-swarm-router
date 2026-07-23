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
    SourceConfig,
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
    # Non-empty only when the resolved source set SourceConfig.cloud_providers:
    # cloud-tagged backends (Backend.cloud_provider) outside this set are
    # excluded from selection. Never excludes local/peer (non-cloud) rows.
    cloud_provider_allowlist: frozenset[str] = frozenset()


def match_routing_policy(
    policies: list[RoutingPolicy],
    *,
    model: str,
    api_format: ApiFormat,
    source_id: str = "",
) -> RoutingPolicy | None:
    """Return the first enabled policy that matches the request."""
    for policy in policies:
        if not policy.enabled:
            continue
        if policy.model_prefix and not model.startswith(policy.model_prefix):
            continue
        if policy.api_format is not None and policy.api_format != api_format:
            continue
        if policy.source and policy.source != source_id:
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
    source: SourceConfig | None = None,
) -> ResolvedRouting:
    """Merge default routing config, optional policy match, optional
    per-source overrides, and per-request header overrides.

    Precedence, highest wins (docs/cli-source-routing-plan.md Phase 0):
    per-request headers > source defaults > routing.policies > [routing]
    globals. `source` (the caller's resolved routing.sources entry, if
    any) is applied AFTER the policy match specifically so a source can
    override what a matching policy decided -- e.g. a source with
    allow_cloud=True can still reach cloud even if a matching policy
    would otherwise force it local-only, and vice versa.

    `cloud` (default CloudConfig(), i.e. today's behavior: enabled=True,
    fallback="cloud") gates the default cloud-allowed stance:
    - cloud.enabled=False: cloud never injected, no policy/source can override.
    - cloud.fallback="none" (or fallback_enabled=False): no *automatic*
      cloud fallback; an explicit policy/source with allow_cloud=True still
      opts a specific route in.
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
    cloud_provider_allowlist: frozenset[str] = frozenset()

    policy = match_routing_policy(
        routing.policies,
        model=model,
        api_format=api_format,
        source_id=source.id if source is not None else "",
    )
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

    if source is not None:
        if source.strategy is not None:
            strategy = source.strategy
        if source.prefer_provider:
            prefer_provider = source.prefer_provider
        if source.local_only:
            local_only = True
            allow_cloud_inject = False
            cloud_leads = False
        elif source.allow_cloud:
            # Ranks above routing.policies: a source can re-enable cloud
            # (clearing any local_only a matching policy set) even if
            # that policy -- or cloud.fallback="none" -- would otherwise
            # keep this route local-only. Reversed only by
            # source.local_only above (checked first) or the caller's
            # x-netllm-local-only header (an absolute ceiling, checked
            # below).
            local_only = False
            allow_cloud_inject = cloud_master_on
            if source.cloud_providers:
                cloud_provider_allowlist = frozenset(source.cloud_providers)

    if header_strategy:
        requested = header_strategy.strip().lower()
        if requested in VALID_STRATEGIES:
            strategy = requested  # type: ignore[assignment]

    if header_local_only:
        # The caller's explicit opt-out is an absolute ceiling: neither a
        # policy nor a source may reopen cloud/remote routing for this
        # one request.
        local_only = True
        allow_cloud_inject = False
        cloud_leads = False

    if local_only:
        cloud_leads = False

    pinned = (header_backend or "").strip() or None

    return ResolvedRouting(
        strategy=strategy,
        local_only=local_only,
        allow_cloud_inject=allow_cloud_inject,
        prefer_provider=prefer_provider,
        pinned_backend=pinned,
        cloud_leads=cloud_leads,
        cloud_provider_allowlist=cloud_provider_allowlist,
    )
