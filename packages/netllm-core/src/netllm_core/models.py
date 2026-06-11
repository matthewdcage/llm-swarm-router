"""Configuration and domain models."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

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
    heartbeat_interval_s: float = 10.0


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
    # local_spillover: serve locally below this many concurrent requests,
    # spill to the least-loaded LAN peer at or above it.
    spillover_max_local_in_flight: int = 2
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


def load_config(path: Path | None = None) -> NetllmConfig:
    import tomllib

    cfg_path = path or default_config_path()
    if not cfg_path.is_file():
        return NetllmConfig()
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    return NetllmConfig.model_validate(raw)


def save_config(config: NetllmConfig, path: Path | None = None) -> Path:
    import tomli_w

    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        tomli_w.dumps(config.model_dump(mode="json")),
        encoding="utf-8",
    )
    return cfg_path
