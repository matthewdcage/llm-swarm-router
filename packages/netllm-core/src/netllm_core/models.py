"""Configuration and domain models."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from netllm_core.cloud_providers import CloudProviderId
from netllm_core.platform import (
    default_discovery_providers,
    default_hostname,
    default_log_dir,
)

__all__ = [
    "default_log_dir",
    "default_discovery_providers",
    "default_hostname",
]

logger = logging.getLogger(__name__)


class ConfigModel(BaseModel):
    """Base for everything reachable from a config.toml.

    `extra="allow"` is the whole point: `save_config` rewrites the entire
    file from `model_dump()`, so any key pydantic discards at load time is
    *deleted from the user's config* on the next save -- by the dashboard's
    `POST /netllm/v1/admin/config`, by `netllm config import` (the macOS
    Settings Save button), and by `netllm join`. Under the default
    `extra="ignore"` that meant every write path silently destroyed keys
    written by a newer netllm, which is the ordinary mixed-version upgrade
    path (upgrade one machine, configure a provider there, press Save on an
    older machine). Allowing extras turns that into a lossless round-trip:
    unknown keys are carried on the model and re-emitted verbatim.

    This is the generalization of F-01 (docs/architecture/07-findings-register.md),
    which was the same failure for fields this build *does* know but the
    merge layer forgot to copy. See docs/extending/PROGRAM.md §3 Axis E.1.

    Not applied to runtime-only models (`Backend`, `BackendHealth`): they
    are never persisted, so a typo there should still fail loudly.
    """

    model_config = ConfigDict(extra="allow")


RoutingStrategy = Literal[
    "auto",
    "failover",
    "round_robin",
    "local_first",
    "least_load",
    "latency_weighted",
    "batch_shard",
    "local_spillover",
]

AgentRole = Literal["peer", "gateway"]
ProviderId = Literal[
    "omlx", "ollama", "lmstudio", "vllm", "custom", "anthropic", "openai"
]
ApiFormat = Literal["openai", "anthropic"]

# [D14] The proxy surfaces a scenario rule can be scoped to. Mirrors
# netllm_agent.taxonomy.Surface's values; Responses is deliberately absent
# because it delegates to the chat surface and inherits its plan.
SurfaceName = Literal["chat", "embeddings", "messages"]

ANTHROPIC_CLOUD_BASE_URL = "https://api.anthropic.com"
OPENAI_CLOUD_BASE_URL = "https://api.openai.com/v1"
LOCAL_ONLY_HEADER = "x-netllm-local-only"
# Per-request routing overrides (optional; strategy must be a RoutingStrategy).
STRATEGY_HEADER = "x-netllm-strategy"
BACKEND_PIN_HEADER = "x-netllm-backend"
# Hop counter set on agent→agent forwards. Backstop loop guard in case a
# peer ignores or strips the local-only header.
HOPS_HEADER = "x-netllm-hops"
MAX_FORWARD_HOPS = 2
# Explicit caller identity (docs/cli-source-routing-plan.md). Optional;
# only honored when it names a configured, enabled routing.sources entry.
SOURCE_HEADER = "x-netllm-source"
DEFAULT_SOURCE_ID = "default"


def infer_api_format(provider: ProviderId) -> ApiFormat:
    if provider == "anthropic":
        return "anthropic"
    return "openai"


class BackendOverride(ConfigModel):
    base_url: str
    provider: ProviderId = "custom"
    api_format: ApiFormat | None = None
    api_key: str = Field(
        default="", json_schema_extra={"widget": "secret", "write_only": True}
    )
    api_key_env: str = ""
    enabled: bool = True
    local: bool = True
    # Tags a backend row as materialized from [cloud.providers.<id>] (see
    # CloudConfig below). Empty for hand-authored [[routing.backends]] rows.
    # Additive field: old readers ignore it; old writers omit it (defaults
    # to ""). Not user-settable from a form — server-materialized only.
    cloud_provider: str = Field(default="", json_schema_extra={"read_only": True})
    # Manual per-backend concurrency cap (0 = defer to the pool's global
    # routing.max_in_flight_per_backend). Same semantics as Backend.max_concurrency.
    max_concurrency: int = Field(default=0, ge=0)

    def resolved_api_format(self) -> ApiFormat:
        if self.api_format is not None:
            return self.api_format
        return infer_api_format(self.provider)

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""


class DiscoveryLocalConfig(ConfigModel):
    providers: list[str] = Field(default_factory=default_discovery_providers)
    custom_endpoints: list[str] = Field(default_factory=list)
    # Per-machine overrides, e.g. omlx on :8088 — tried before default port scan.
    provider_urls: dict[str, list[str]] = Field(default_factory=dict)


class DiscoverySwarmConfig(ConfigModel):
    peers: list[str] = Field(default_factory=list)
    mdns: bool = True
    subnet_scan: bool = False
    subnet_cidrs: list[str] = Field(default_factory=list)
    cluster_token: str = Field(
        default="", json_schema_extra={"widget": "secret", "write_only": True}
    )
    # When true (and a cluster_token is set), /v1/* inference routes
    # require the Bearer token from non-local clients. Peer agents
    # forward with the cluster token automatically.
    require_token_for_inference: bool = False
    heartbeat_interval_s: float = Field(default=10.0, gt=0.0)
    # Drop a peer from the registry after this many seconds without a
    # heartbeat. Re-discovery (below) can bring it back.
    peer_stale_after_s: float = Field(default=45.0, gt=0.0)
    # Periodically re-probe previously seen peers and (when subnet_scan
    # is on) re-scan the subnet, so peers lost to sleep/Wi-Fi blips
    # rejoin without a restart. 0 disables.
    rediscover_interval_s: float = Field(default=60.0, ge=0.0)


class RoutingPolicy(ConfigModel):
    """Match rules for per-request routing. Cloud paths require allow_cloud = true."""

    name: str = ""
    model_prefix: str = ""
    api_format: ApiFormat | None = None
    # Scope this policy to one routing.sources id (e.g. "buzz"). Empty
    # (default) matches any source, including "default" -- unchanged
    # behavior for configs written before source identity existed.
    source: str = ""
    strategy: RoutingStrategy | None = None
    prefer_provider: ProviderId | None = None
    allow_cloud: bool = False
    enabled: bool = True


class SourceMatch(ConfigModel):
    """Fallback identification when no header/key match is presented.

    Never grants elevated access on its own -- see SourceConfig.is_elevated.
    """

    user_agent_contains: list[str] = Field(default_factory=list)


class ScenarioRule(ConfigModel):
    """[routing.sources.<id>.scenarios.<scenario>] -- per-source override
    for one classified traffic scenario (netllm_core.scenarios.Scenario:
    long_context, web_search, think, background, default).

    Ranks between the source's own defaults and per-request headers in
    precedence (docs/cli-source-routing-plan.md Phase 0): a scenario rule
    overrides SourceConfig.strategy/local_only/allow_cloud for just this
    scenario, but an explicit x-netllm-* header still wins over it.
    """

    # Concrete model to use for this scenario (e.g. a cheap local model
    # for "background", a strong cloud model for "think"). Empty = no
    # override; the source's model_rewrites (or the client's requested
    # model) is used unchanged.
    model: str = ""
    strategy: RoutingStrategy | None = None
    local_only: bool = False
    allow_cloud: bool = False
    # [D14] Which proxy surfaces this rule may fire on: any subset of
    # {"chat", "embeddings", "messages"}. Empty (the default) means EVERY
    # surface, which is the pre-Phase-4d behavior verbatim — scenario
    # classification runs the same chat-shaped heuristic on /v1/embeddings
    # traffic, so a rule written for chat has always applied there too. That
    # is a real footgun (a `think` rule swapping in a reasoning model breaks
    # an embeddings request outright), but silently narrowing existing
    # configs would be a bigger one, so the narrowing is opt-in: name the
    # surfaces you mean.
    surfaces: list[SurfaceName] = Field(default_factory=list)

    def applies_to(self, surface: str | None) -> bool:
        """True when this rule is allowed to fire on `surface`.

        An unqualified rule (the compat default) fires everywhere; so does
        any rule when the caller did not say which surface it is on.
        """
        if not self.surfaces or surface is None:
            return True
        return surface in self.surfaces


class SourceConfig(ConfigModel):
    """[[routing.sources]] -- a known CLI or harness with durable routing.

    Identity resolution (netllm_core.source_identity.resolve_source), first
    match wins: x-netllm-source header naming this id -> virtual API key
    "netllm-<id>" -> User-Agent substring in match.user_agent_contains ->
    "default". Attributive by default: an unrecognized key/header/UA falls
    back to "default" rather than a 401 -- the real access boundary stays
    agent.listen / swarm.cluster_token.

    Setting `secret`/`secret_env` changes that for THIS source only: once
    a secret is configured, no signal grants this identity except a
    virtual key carrying it ("netllm-<id>.<secret>") -- a bare header or
    key naming this id is no longer enough. A source that grants elevated
    capability (see is_elevated) must set one once the agent binds beyond
    loopback; enforced at config-apply time (admin._validate_elevated_sources).
    """

    id: str
    # Links this row back to a netllm_core.known_harnesses.KnownHarness id
    # for detection/badge purposes only (docs/cli-source-routing-plan.md
    # Phase 4c) -- not read by identity resolution or routing.
    known_id: str | None = None
    enabled: bool = True
    description: str = ""
    secret: str = Field(
        default="", json_schema_extra={"widget": "secret", "write_only": True}
    )
    secret_env: str = ""
    strategy: RoutingStrategy | None = None
    local_only: bool = False
    allow_cloud: bool = False
    prefer_provider: ProviderId | None = None
    # Cloud provider ids (e.g. "openrouter", "anthropic") this source may
    # reach when allow_cloud is true. Empty = no restriction beyond the
    # cloud master switch / global fallback policy. A non-empty list
    # counts as elevated (see is_elevated) and only ever narrows which
    # cloud backends are reachable -- it never excludes local/peer rows.
    cloud_providers: list[str] = Field(default_factory=list)
    # Per-source concurrency ceiling (0 = defer to
    # routing.max_in_flight_per_backend). A value above that global cap
    # counts as elevated (see is_elevated).
    max_concurrency: int = Field(default=0, ge=0)
    # Requested model name -> concrete model name, applied for this
    # source only, before model_aliases/model_pools resolution.
    model_rewrites: dict[str, str] = Field(default_factory=dict)
    # Per-scenario overrides (netllm_core.scenarios.classify_scenario),
    # keyed by scenario name -- e.g. {"background": {"model": "..."},
    # "think": {"strategy": "failover", "allow_cloud": true}}.
    scenarios: dict[str, ScenarioRule] = Field(default_factory=dict)
    match: SourceMatch = Field(default_factory=SourceMatch)

    def resolve_secret(self) -> str:
        if self.secret:
            return self.secret
        if self.secret_env:
            return os.environ.get(self.secret_env, "")
        return ""

    def is_elevated(self, *, default_max_concurrency: int) -> bool:
        """True when this source's config grants more than plain attribution.

        Elevated sources must be secret-backed once agent.listen is
        LAN-reachable (see admin._validate_elevated_sources) -- a spoofed
        source name can then win only default-tier local routing, never
        cloud access or an above-default concurrency allowance.
        """
        if self.allow_cloud or self.cloud_providers:
            return True
        if self.max_concurrency and self.max_concurrency > default_max_concurrency:
            return True
        return False


class ModelPool(ConfigModel):
    """[routing.model_pools.<name>] — a host-scoped catch-all pool.

    Unlike model_aliases (canonical name -> served IDs, matched against
    the *requested* name), a pool bypasses name matching entirely: any
    backend listed in `hosts` becomes a candidate for ANY requested
    model, as long as it actually serves one of `models`. The served
    model in that intersection is what gets invoked upstream — the
    client's requested name is irrelevant once a pool backend is
    selected. Meant for a host running a fixed set of loaded models that
    should absorb overflow/misnamed requests rather than 404.
    """

    enabled: bool = True
    # Backend refs: backend id, "peer:<agent-id>", bare agent_id, or
    # base_url — same ref forms accepted by the x-netllm-backend pin.
    hosts: list[str] = Field(default_factory=list)
    # Models this pool is allowed to serve for any incoming request name.
    models: list[str] = Field(default_factory=list)


class RoutingConfig(ConfigModel):
    # "auto": requests with shard context use batch_shard; everything
    # else balances by live in-flight load (least_load).
    default_strategy: RoutingStrategy = "local_first"
    allow_remote: bool = True
    # Deprecated and inert. Its only consumer, plan_batch_shard, was deleted
    # as dead code (routing-hardening-plan.md Phase 5); the semantics move to
    # the planned model_groups feature. Kept ONLY so existing config.toml
    # files still load — removed from config_summary, the dashboard and macOS
    # Settings in 0.4.6 so nobody can toggle something that does nothing
    # (audit F-17). Drop the field entirely one release after that.
    require_same_model_for_shard: bool = True
    # Back-pressure cap applied by every strategy: selection prefers
    # backends with fewer than this many requests in flight. 0 = off.
    max_in_flight_per_backend: int = Field(default=0, ge=0)
    # Peer-role agents adopt the gateway's advertised default_strategy
    # from heartbeats (runtime only, not persisted), so a mesh can't run
    # conflicting strategies by accident. Set false to opt out.
    follow_gateway: bool = True
    # local_spillover: serve locally while fewer than this many requests
    # are in flight locally; at or above it, spill to a LAN peer only
    # when that peer is strictly less loaded.
    spillover_max_local_in_flight: int = Field(default=2, ge=1)
    # Health cache: how long a probe result stays fresh, and how many
    # consecutive request failures mark a backend offline.
    health_ttl_s: float = Field(default=30.0, gt=0.0)
    # Offline backends are re-probed after this many seconds instead of
    # waiting out the full health TTL (faster recovery from blips).
    offline_retry_s: float = Field(default=10.0, gt=0.0)
    max_backend_failures: int = Field(default=3, ge=1)
    # Upstream HTTP timeouts, in seconds. The read timeout bounds a single
    # generation: a large local model on slow hardware can exceed the old
    # hardcoded 120 s, and there was no way to raise it without editing
    # source (audit F-20).
    upstream_connect_timeout_s: float = Field(default=5.0, gt=0.0)
    upstream_read_timeout_s: float = Field(default=120.0, gt=0.0)
    # Set once ensure_lan_mesh_defaults() has upgraded a LAN-bound
    # config; prevents re-overriding an explicit user strategy choice.
    lan_defaults_applied: bool = Field(
        default=False, json_schema_extra={"read_only": True}
    )
    # Canonical model name -> provider-specific IDs. Lets mixed fleets
    # (oMLX vs Ollama vs LM Studio naming) serve one model name:
    #   [routing.model_aliases]
    #   "llama3" = ["llama3:8b-instruct-q4_K_M", "Meta-Llama-3-8B-Instruct"]
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)
    # Host-scoped catch-all pools that bypass model_aliases matching
    # entirely for their member backends — see ModelPool.
    #   [routing.model_pools.<name>]
    #   enabled = true
    #   hosts = ["mac-studio"]
    #   models = ["qwen2.5:72b-instruct"]
    model_pools: dict[str, ModelPool] = Field(default_factory=dict)
    backends: list[BackendOverride] = Field(default_factory=list)
    # default_factory names a client-side named builder for "Add row"
    # (a sensible starting policy, not an empty one) — see
    # docs/config-schema-rewrite-plan.md §6 risk 1.
    policies: list[RoutingPolicy] = Field(
        default_factory=list,
        json_schema_extra={"default_factory": "local_openai_policy"},
    )
    # Known CLI/harness sources with durable per-caller routing overrides.
    # See SourceConfig and netllm_core.source_identity.resolve_source.
    sources: list[SourceConfig] = Field(default_factory=list)


class AgentConfig(ConfigModel):
    listen: str = "127.0.0.1:11400"
    role: AgentRole = "peer"
    advertise: bool = True
    agent_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8],
        json_schema_extra={"read_only": True},
    )
    hostname: str = Field(
        default_factory=default_hostname, json_schema_extra={"read_only": True}
    )
    # Self-declared ceiling on this machine's own concurrent requests
    # (summed across all its local backends), broadcast via heartbeat so
    # every peer's least_load/local_spillover selection respects it.
    # 0 = unlimited (pre-existing behavior; only the sending machine's own
    # config controls this — no peer imposes it on another).
    max_concurrency: int = Field(default=0, ge=0)

    @field_validator("listen")
    @classmethod
    def _validate_listen(cls, v: str) -> str:
        """Fail at load time with a clear message instead of deep in serve.

        Accepts host:port and bracketed IPv6 [::]:port.
        """
        raw = v.strip()
        if not raw:
            raise ValueError("agent.listen must be host:port (e.g. 127.0.0.1:11400)")
        if raw.startswith("["):
            host, sep, port = raw.partition("]:")
            valid = sep and port.isdigit()
        else:
            host, sep, port = raw.rpartition(":")
            valid = bool(sep) and bool(host) and port.isdigit()
        if not valid or not (0 < int(port) < 65536):
            raise ValueError(
                f"agent.listen {v!r} is not host:port "
                "(e.g. 127.0.0.1:11400 or [::]:11400)"
            )
        return raw


class UiConfig(ConfigModel):
    auto_start_on_launch: bool = True
    log_dir: str = ""
    check_for_updates_automatically: bool = True
    model_favorites: list[str] = Field(default_factory=list)
    menubar_show_cpu: bool = False
    menubar_show_gpu: bool = False
    menubar_show_mem: bool = False
    menubar_show_live: bool = False
    menubar_merge_gauges: bool = False
    menubar_models_favorites_only: bool = False


CloudFallbackMode = Literal["cloud", "local", "none"]
CloudAuthMode = Literal["api_key", "oauth_pkce", "plan_token"]


class CloudProviderConfig(ConfigModel):
    """[cloud.providers.<id>] — one pre-configured cloud provider.

    All fields default to the provider's registry entry (see
    netllm_core.cloud_providers). enabled defaults False, so an absent or
    default-valued entry changes nothing at runtime.
    """

    enabled: bool = False
    region: str = Field(
        default="",
        json_schema_extra={"widget": "select", "options_from": "registry.regions"},
    )
    api_format: ApiFormat | None = None
    auth: CloudAuthMode = "api_key"
    api_key: str = Field(
        default="", json_schema_extra={"widget": "secret", "write_only": True}
    )
    api_key_env: str = ""
    models: list[str] = Field(default_factory=list)
    base_url: str = ""


class CloudConfig(ConfigModel):
    """[cloud] — master switch, fallback policy, and per-provider config.

    Absent section == identical behavior to pre-cloud-feature releases:
    enabled defaults True (preserves today's env-key-triggered inject),
    fallback defaults "cloud" (today's implicit local-then-cloud order),
    and no provider is enabled by default.
    """

    enabled: bool = True
    fallback: CloudFallbackMode = "cloud"
    fallback_enabled: bool = True
    # One-shot migration flag (ensure_cloud_defaults), mirrors
    # routing.lan_defaults_applied.
    cloud_defaults_applied: bool = Field(
        default=False, json_schema_extra={"read_only": True}
    )
    providers: dict[str, CloudProviderConfig] = Field(default_factory=dict)

    # A `_drop_unknown_provider_ids` validator used to live here, filtering
    # `providers` down to the code-owned CLOUD_PROVIDERS ids on the grounds
    # that a stray key is unremovable through the merge path (see
    # docs/config-guards-audit.md). But "unknown to *this build*" and
    # "stray" are not the same thing: a provider added in a later release
    # is unknown here and the filter deleted it on the next save, which is
    # exactly the mixed-version data loss ConfigModel exists to stop.
    # Unknown ids are now kept and reported instead --
    # netllm_core.config_report.unknown_cloud_provider_issues surfaces them
    # in `netllm doctor` and the dashboard's doctor panel, and every
    # consumer already skips a provider whose `get_provider_spec` is None
    # (service/cloud.py, admin.py), so an unknown id materializes nothing.

    def provider(self, provider_id: CloudProviderId) -> CloudProviderConfig:
        return self.providers.get(provider_id, CloudProviderConfig())


class NetllmConfig(ConfigModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    discovery: DiscoveryLocalConfig = Field(default_factory=DiscoveryLocalConfig)
    swarm: DiscoverySwarmConfig = Field(default_factory=DiscoverySwarmConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)

    def resolved_log_dir(self) -> Path:
        if self.ui.log_dir:
            return Path(self.ui.log_dir).expanduser()
        return default_log_dir()


def unknown_cloud_provider_ids(config: NetllmConfig) -> list[str]:
    """`[cloud.providers.<id>]` keys with no entry in CLOUD_PROVIDERS.

    Either a typo or -- the case that matters -- a provider this build is
    too old to know. Kept on the model either way; see CloudConfig.
    """
    from netllm_core.cloud_providers import CLOUD_PROVIDERS

    return sorted(pid for pid in config.cloud.providers if pid not in CLOUD_PROVIDERS)


def _model_extra_paths(model: BaseModel, prefix: str = "") -> list[str]:
    paths = [f"{prefix}{key}" for key in (model.model_extra or {})]
    for name in type(model).model_fields:
        value = getattr(model, name, None)
        if isinstance(value, BaseModel):
            paths.extend(_model_extra_paths(value, f"{prefix}{name}."))
    return paths


def preserved_extra_paths(config: NetllmConfig) -> list[str]:
    """Dotted paths of every key this build does not model but is keeping.

    Covers top-level sections, fields within them, and unknown
    `[cloud.providers.<id>]` subtrees. Deliberately does *not* descend into
    list rows ([[routing.backends]], [[routing.sources]], ...): their
    extras round-trip the same way, but naming them would turn one
    orienting log line into a wall of indices.
    """
    paths = _model_extra_paths(config)
    paths.extend(f"cloud.providers.{pid}" for pid in unknown_cloud_provider_ids(config))
    return sorted(paths)


class BackendHealth(BaseModel):
    status: str = "unknown"
    http_status: int | None = None
    model_count: int = 0
    models: list[str] = Field(default_factory=list)
    detail: str | None = None
    latency_p50_ms: float | None = None
    last_check: float = 0.0


class Backend(BaseModel):
    """A routable upstream OpenAI-compatible or Anthropic endpoint."""

    id: str
    base_url: str
    provider: ProviderId = "custom"
    api_format: ApiFormat = "openai"
    # [F-59] Never serialized. Every payload that dumps a Backend --
    # GET /netllm/v1/backends, the status body, and the heartbeat that
    # body is gossiped as -- would otherwise carry the raw key, and
    # require_read_access is a no-op while swarm.cluster_token is empty
    # (the default even on a LAN bind). The field stays readable in
    # process for resolve_api_key(); `api_key_set` is the wire-visible
    # form, matching _backend_override_export's existing convention.
    # Safe on the write path: config_merge treats keys as write-only, so
    # an omitted key preserves the stored one rather than blanking it.
    api_key: str = Field(default="", exclude=True)
    enabled: bool = True
    local: bool = True
    agent_id: str = ""
    health: BackendHealth = Field(default_factory=BackendHealth)
    in_flight: int = 0
    latency_ema_ms: float = 0.0
    # Set when this Backend was materialized from a [cloud.providers.<id>]
    # config entry (see CloudConfig). Empty for local/peer/manual backends.
    cloud_provider: str = ""
    # "api_key" (x-api-key / Bearer per SDK default) or "bearer" (force
    # Authorization: Bearer — Anthropic plan_token mode, WIF tokens).
    auth_mode: str = "api_key"
    # Concurrency ceiling for this specific row (0 = defer to the pool's
    # global routing.max_in_flight_per_backend). For a peer row this is
    # copied from that peer's self-declared agent.max_concurrency
    # (swarm.py peer_agent_backends) — a peer never has this imposed by
    # anyone but its own config. For a local/manual row it comes from
    # BackendOverride.max_concurrency.
    max_concurrency: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_key_set(self) -> bool:
        """Wire-visible replacement for the redacted key (F-59)."""
        return bool(self.api_key)

    def cache_key(self) -> str:
        return f"{self.provider}:{self.base_url}"

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        from netllm_core.local_providers import api_key_env_for, default_api_key_for

        env_name = api_key_env_for(self.provider)
        if not env_name and self.cloud_provider:
            from netllm_core.cloud_providers import get_provider_spec

            spec = get_provider_spec(self.cloud_provider)
            if spec is not None:
                env_name = spec.api_key_env
        if env_name:
            from_env = os.environ.get(env_name, "")
            if from_env:
                return from_env
        return default_api_key_for(self.provider)


DEFAULT_AGENT_PORT = 11400


def split_listen(listen: str) -> tuple[str, int]:
    """Split an ``agent.listen`` value into (host, port), IPv6-aware.

    ``AgentConfig._validate_listen`` accepts bracketed IPv6 (``[::]:11400``),
    but callers used to parse with ``listen.partition(":")``, which yields
    host ``"["`` and port ``":]:11400"`` — so ``netllm serve`` died on
    ``int()`` for a listen value the config layer had just declared valid
    (docs/architecture/07-findings-register.md F-11). Every caller should use
    this instead of splitting by hand.

    The brackets are stripped from the returned host: that is what socket
    servers and ``urlparse``-built URLs want. Use ``format_listen`` to go back.
    """
    raw = listen.strip()
    if raw.startswith("http"):
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        return (parsed.hostname or "127.0.0.1"), (parsed.port or DEFAULT_AGENT_PORT)
    if raw.startswith("["):
        host, sep, port = raw.partition("]:")
        if sep:
            return host[1:], int(port) if port.isdigit() else DEFAULT_AGENT_PORT
        return raw.strip("[]"), DEFAULT_AGENT_PORT
    if raw.count(":") > 1:
        # Unbracketed and multi-colon: a bare IPv6 address carrying no port.
        # Splitting on the last colon here would read "::1" as host ":" and
        # port "1".
        return raw, DEFAULT_AGENT_PORT
    host, sep, port = raw.rpartition(":")
    if not sep:
        return raw, DEFAULT_AGENT_PORT
    return host, int(port) if port.isdigit() else DEFAULT_AGENT_PORT


def format_listen(host: str, port: int) -> str:
    """Inverse of `split_listen` — re-brackets a bare IPv6 host."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def listen_port(listen: str) -> int:
    return split_listen(listen)[1]


def listen_host(listen: str) -> str:
    return split_listen(listen)[0]


def default_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "netllm" / "config.toml"
    return Path.home() / ".config" / "netllm" / "config.toml"


def is_lan_listen(listen: str) -> bool:
    """True when the agent accepts connections from the LAN."""
    if listen.startswith("0.0.0.0:"):
        return True
    if listen.startswith("[::]:") or listen == "[::]":
        return True
    host = listen.rsplit(":", 1)[0] if ":" in listen else listen
    return host in {"0.0.0.0", "::"}


def ensure_lan_mesh_defaults(cfg: NetllmConfig) -> bool:
    """Apply mesh routing/discovery defaults for LAN bind; never mints tokens.

    The whole upgrade is one-shot (tracked via
    routing.lan_defaults_applied): after the first application, an explicit
    user choice of local_first — or of subnet_scan = false — is respected
    instead of being silently rewritten on every load/save.

    subnet_scan used to be re-forced on *every* call, outside the one-shot
    gate. Because this runs on the CLI/macOS save path, turning subnet_scan
    off in macOS Settings on a LAN-bound agent was undone by the very save
    that turned it off. Both defaults now sit behind the same flag, which is
    also what makes it safe to run this on every write path
    (netllm_core.config_guards.apply_config_guards).
    """
    if not is_lan_listen(cfg.agent.listen):
        return False
    if cfg.routing.lan_defaults_applied:
        return False
    if cfg.routing.default_strategy == "local_first":
        cfg.routing.default_strategy = "local_spillover"
    if not cfg.swarm.subnet_scan:
        cfg.swarm.subnet_scan = True
    cfg.routing.lan_defaults_applied = True
    return True


def load_config(path: Path | None = None) -> NetllmConfig:
    import tomllib

    cfg_path = path or default_config_path()
    if not cfg_path.is_file():
        return NetllmConfig()
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    return NetllmConfig.model_validate(raw)


def _drop_none_values(obj: object) -> object:
    """TOML has no null — strip None leaves (optional fields load back
    as None via pydantic defaults, so this is lossless)."""
    if isinstance(obj, dict):
        return {k: _drop_none_values(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_none_values(v) for v in obj]
    return obj


def save_config(config: NetllmConfig, path: Path | None = None) -> Path:
    import tomli_w

    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    # One line naming everything being carried through, not one per key:
    # this fires on every save of a config written by a newer netllm, and
    # a per-key loop would bury the rest of the serve log.
    extras = preserved_extra_paths(config)
    if extras:
        logger.warning(
            "Preserving %d config key(s) this build does not recognize: %s. "
            "They are re-emitted unchanged (likely written by a newer netllm); "
            "run `netllm doctor` if that is unexpected.",
            len(extras),
            ", ".join(extras),
        )
    payload = _drop_none_values(config.model_dump(mode="json"))
    cfg_path.write_text(
        tomli_w.dumps(payload),  # type: ignore[arg-type]
        encoding="utf-8",
    )
    # Config may hold cluster tokens / API keys — owner-only.
    if os.name == "posix":
        os.chmod(cfg_path, 0o600)
    return cfg_path
