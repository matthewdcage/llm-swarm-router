"""Configuration and domain models."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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

RoutingStrategy = Literal[
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


def infer_api_format(provider: ProviderId) -> ApiFormat:
    if provider == "anthropic":
        return "anthropic"
    return "openai"


class BackendOverride(BaseModel):
    base_url: str
    provider: ProviderId = "custom"
    api_format: ApiFormat | None = None
    api_key: str = ""
    api_key_env: str = ""
    enabled: bool = True
    local: bool = True

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


class DiscoveryLocalConfig(BaseModel):
    providers: list[str] = Field(default_factory=default_discovery_providers)
    custom_endpoints: list[str] = Field(default_factory=list)
    # Per-machine overrides, e.g. omlx on :8088 — tried before default port scan.
    provider_urls: dict[str, list[str]] = Field(default_factory=dict)


class DiscoverySwarmConfig(BaseModel):
    peers: list[str] = Field(default_factory=list)
    mdns: bool = True
    subnet_scan: bool = False
    subnet_cidrs: list[str] = Field(default_factory=list)
    cluster_token: str = ""
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


class RoutingPolicy(BaseModel):
    """Match rules for per-request routing. Cloud paths require allow_cloud = true."""

    name: str = ""
    model_prefix: str = ""
    api_format: ApiFormat | None = None
    strategy: RoutingStrategy | None = None
    prefer_provider: ProviderId | None = None
    allow_cloud: bool = False
    enabled: bool = True


class RoutingConfig(BaseModel):
    default_strategy: RoutingStrategy = "local_first"
    allow_remote: bool = True
    require_same_model_for_shard: bool = True
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
    # Set once ensure_lan_mesh_defaults() has upgraded a LAN-bound
    # config; prevents re-overriding an explicit user strategy choice.
    lan_defaults_applied: bool = False
    # Canonical model name -> provider-specific IDs. Lets mixed fleets
    # (oMLX vs Ollama vs LM Studio naming) serve one model name:
    #   [routing.model_aliases]
    #   "llama3" = ["llama3:8b-instruct-q4_K_M", "Meta-Llama-3-8B-Instruct"]
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)
    backends: list[BackendOverride] = Field(default_factory=list)
    policies: list[RoutingPolicy] = Field(default_factory=list)


class AgentConfig(BaseModel):
    listen: str = "127.0.0.1:11400"
    role: AgentRole = "peer"
    advertise: bool = True
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    hostname: str = Field(default_factory=default_hostname)

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


class UiConfig(BaseModel):
    auto_start_on_launch: bool = True
    log_dir: str = ""
    check_for_updates_automatically: bool = True


class NetllmConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    discovery: DiscoveryLocalConfig = Field(default_factory=DiscoveryLocalConfig)
    swarm: DiscoverySwarmConfig = Field(default_factory=DiscoverySwarmConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    ui: UiConfig = Field(default_factory=UiConfig)

    def resolved_log_dir(self) -> Path:
        if self.ui.log_dir:
            return Path(self.ui.log_dir).expanduser()
        return default_log_dir()


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
    api_key: str = ""
    enabled: bool = True
    local: bool = True
    agent_id: str = ""
    health: BackendHealth = Field(default_factory=BackendHealth)
    in_flight: int = 0
    latency_ema_ms: float = 0.0

    def cache_key(self) -> str:
        return f"{self.provider}:{self.base_url}"

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_map = {
            "omlx": "OMLX_API_KEY",
            "ollama": "OLLAMA_API_KEY",
            "lmstudio": "LMSTUDIO_API_KEY",
            "vllm": "VLLM_API_KEY",
        }
        env_name = env_map.get(self.provider, "")
        if env_name:
            from_env = os.environ.get(env_name, "")
            if from_env:
                return from_env
        defaults: dict[str, str] = {"omlx": "omlx-local"}
        return defaults.get(self.provider, "")


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

    The strategy upgrade is one-shot (tracked via
    routing.lan_defaults_applied): after the first upgrade, an explicit
    user choice of local_first is respected instead of being silently
    rewritten on every load/save.
    """
    if not is_lan_listen(cfg.agent.listen):
        return False
    changed = False
    if not cfg.routing.lan_defaults_applied:
        if cfg.routing.default_strategy == "local_first":
            cfg.routing.default_strategy = "local_spillover"
        cfg.routing.lan_defaults_applied = True
        changed = True
    if not cfg.swarm.subnet_scan:
        cfg.swarm.subnet_scan = True
        changed = True
    return changed


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
    payload = _drop_none_values(config.model_dump(mode="json"))
    cfg_path.write_text(
        tomli_w.dumps(payload),  # type: ignore[arg-type]
        encoding="utf-8",
    )
    # Config may hold cluster tokens / API keys — owner-only.
    if os.name == "posix":
        os.chmod(cfg_path, 0o600)
    return cfg_path
