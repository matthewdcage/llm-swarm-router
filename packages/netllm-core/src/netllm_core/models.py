"""Configuration and domain models."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

RoutingStrategy = Literal[
    "failover",
    "round_robin",
    "local_first",
    "least_load",
    "latency_weighted",
    "batch_shard",
]

AgentRole = Literal["peer", "gateway"]
ProviderId = Literal["omlx", "ollama", "lmstudio", "custom", "anthropic", "openai"]


class BackendOverride(BaseModel):
    base_url: str
    provider: ProviderId = "custom"
    api_key: str = ""
    api_key_env: str = ""
    enabled: bool = True
    local: bool = True

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""


class DiscoveryLocalConfig(BaseModel):
    providers: list[str] = Field(default_factory=lambda: ["omlx", "ollama", "lmstudio"])
    custom_endpoints: list[str] = Field(default_factory=list)


class DiscoverySwarmConfig(BaseModel):
    peers: list[str] = Field(default_factory=list)
    mdns: bool = True
    subnet_scan: bool = False
    subnet_cidrs: list[str] = Field(default_factory=list)
    cluster_token: str = ""
    heartbeat_interval_s: float = 10.0


class RoutingConfig(BaseModel):
    default_strategy: RoutingStrategy = "local_first"
    allow_remote: bool = True
    require_same_model_for_shard: bool = True
    backends: list[BackendOverride] = Field(default_factory=list)


class AgentConfig(BaseModel):
    listen: str = "127.0.0.1:11400"
    role: AgentRole = "peer"
    advertise: bool = True
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    hostname: str = Field(default_factory=lambda: os.uname().nodename)


class NetllmConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    discovery: DiscoveryLocalConfig = Field(default_factory=DiscoveryLocalConfig)
    swarm: DiscoverySwarmConfig = Field(default_factory=DiscoverySwarmConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)


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
    api_key: str = ""
    enabled: bool = True
    local: bool = True
    agent_id: str = ""
    health: BackendHealth = Field(default_factory=BackendHealth)
    in_flight: int = 0
    latency_ema_ms: float = 0.0

    def cache_key(self) -> str:
        return f"{self.provider}:{self.base_url}"


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
