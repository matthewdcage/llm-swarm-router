"""Loopback-gated admin helpers for the local web dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from netllm_core.models import NetllmConfig, save_config

from netllm_agent.service import AgentService

_CONFIG_SECTIONS = frozenset({"agent", "discovery", "swarm", "routing", "ui"})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def require_admin_access(request: Request, cfg: NetllmConfig) -> None:
    """Allow admin routes from loopback or with a valid cluster token."""
    client_host = (request.client.host if request.client else "").lower()
    if client_host in _LOOPBACK_HOSTS:
        return
    token = (cfg.swarm.cluster_token or "").strip()
    if token:
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return
    raise HTTPException(
        status_code=403,
        detail="Admin routes require a loopback client or Bearer cluster token",
    )


def doctor_payload(cfg: NetllmConfig, service: AgentService) -> dict[str, Any]:
    """Read-only doctor summary for the dashboard (subset of CLI doctor)."""
    issues: list[dict[str, str]] = []

    if cfg.agent.listen.startswith("0.0.0.0") and not cfg.swarm.cluster_token:
        issues.append(
            {
                "title": "LAN exposure without cluster token",
                "fix": "Set swarm.cluster_token when listening on 0.0.0.0",
            }
        )

    if cfg.agent.role == "gateway" and not cfg.agent.advertise:
        issues.append(
            {
                "title": "Gateway not advertising",
                "fix": "Set agent.advertise = true so workers can find the gateway",
            }
        )

    enabled = [b for b in service.pool.backends if b.enabled]
    healthy = [b for b in enabled if service.pool.is_healthy(b)]
    if not healthy:
        issues.append(
            {
                "title": "No healthy inference backends",
                "fix": "Start Ollama, LM Studio, or vLLM, then run Discover",
            }
        )

    if cfg.swarm.mdns and cfg.agent.advertise:
        try:
            import zeroconf  # noqa: F401

            mdns_ok = True
        except ImportError:
            mdns_ok = False
        if not mdns_ok:
            issues.append(
                {
                    "title": "mDNS enabled but zeroconf unavailable",
                    "fix": "Reinstall netllm (uv sync) or use static swarm.peers",
                }
            )

    return {"ok": not issues, "issues": issues}


def _backend_override_export(cfg: NetllmConfig) -> list[dict[str, Any]]:
    return [
        {
            "base_url": b.base_url,
            "provider": b.provider,
            "api_format": b.api_format,
            "enabled": b.enabled,
            "local": b.local,
            "api_key_set": bool(b.api_key or b.api_key_env),
        }
        for b in cfg.routing.backends
    ]


def config_summary(cfg: NetllmConfig) -> dict[str, Any]:
    """Non-secret config slices for dashboard display and editing."""
    token = cfg.swarm.cluster_token
    return {
        "agent": {
            "listen": cfg.agent.listen,
            "role": cfg.agent.role,
            "advertise": cfg.agent.advertise,
            "hostname": cfg.agent.hostname,
            "agent_id": cfg.agent.agent_id,
        },
        "discovery": {
            "providers": list(cfg.discovery.providers),
            "provider_urls": dict(cfg.discovery.provider_urls),
            "custom_endpoints": list(cfg.discovery.custom_endpoints),
        },
        "swarm": {
            "mdns": cfg.swarm.mdns,
            "subnet_scan": cfg.swarm.subnet_scan,
            "subnet_cidrs": list(cfg.swarm.subnet_cidrs),
            "heartbeat_interval_s": cfg.swarm.heartbeat_interval_s,
            "peers": list(cfg.swarm.peers),
            "cluster_token_set": bool(token),
        },
        "routing": {
            "default_strategy": cfg.routing.default_strategy,
            "allow_remote": cfg.routing.allow_remote,
            "require_same_model_for_shard": cfg.routing.require_same_model_for_shard,
            "backends": _backend_override_export(cfg),
            "backend_count": len(cfg.routing.backends),
        },
        "ui": {
            "auto_start_on_launch": cfg.ui.auto_start_on_launch,
            "log_dir": cfg.ui.log_dir or str(cfg.resolved_log_dir()),
        },
    }


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def apply_config_patch(cfg: NetllmConfig, patch: dict[str, Any]) -> NetllmConfig:
    """Merge dashboard-editable config sections and validate."""
    if not patch:
        return cfg

    current = cfg.model_dump(mode="json")

    for section, body in patch.items():
        if section not in _CONFIG_SECTIONS:
            continue
        if not isinstance(body, dict):
            continue
        current[section] = _deep_merge_dict(current.get(section, {}), body)

    if "swarm" in patch and isinstance(patch["swarm"], dict):
        swarm_patch = patch["swarm"]
        token_val = swarm_patch.get("cluster_token")
        if token_val is None or token_val == "":
            current["swarm"]["cluster_token"] = cfg.swarm.cluster_token
        else:
            current["swarm"]["cluster_token"] = str(token_val)

    if "agent" in patch and isinstance(patch["agent"], dict):
        agent_patch = patch["agent"]
        if "agent_id" not in agent_patch:
            current["agent"]["agent_id"] = cfg.agent.agent_id
        if "hostname" not in agent_patch:
            current["agent"]["hostname"] = cfg.agent.hostname

    if "routing" in patch and isinstance(patch["routing"], dict):
        routing_patch = patch["routing"]
        if "backends" in routing_patch:
            merged_backends: list[dict[str, Any]] = []
            existing_by_url = {b.base_url: b for b in cfg.routing.backends}
            for entry in routing_patch["backends"] or []:
                if not isinstance(entry, dict):
                    continue
                base_url = str(entry.get("base_url", "")).strip()
                if not base_url:
                    continue
                prior = existing_by_url.get(base_url)
                default_provider = prior.provider if prior else "custom"
                merged: dict[str, Any] = {
                    "base_url": base_url,
                    "provider": entry.get("provider", default_provider),
                    "api_format": entry.get(
                        "api_format", prior.api_format if prior else None
                    ),
                    "api_key": prior.api_key if prior else "",
                    "api_key_env": prior.api_key_env if prior else "",
                    "enabled": entry.get("enabled", prior.enabled if prior else True),
                    "local": entry.get("local", prior.local if prior else True),
                }
                if entry.get("api_key"):
                    merged["api_key"] = str(entry["api_key"])
                merged_backends.append(merged)
            current["routing"]["backends"] = merged_backends

    return NetllmConfig.model_validate(current)


def save_config_patch(
    cfg: NetllmConfig,
    patch: dict[str, Any],
    *,
    config_path: Path | None,
    listen_before: str | None = None,
) -> dict[str, Any]:
    """Apply patch, persist to disk, and report whether restart is needed."""
    if config_path is None:
        raise HTTPException(
            status_code=400,
            detail="Agent was started without a config file path; cannot save",
        )
    before = listen_before or cfg.agent.listen
    updated = apply_config_patch(cfg, patch)
    saved = save_config(updated, config_path)
    needs_restart = updated.agent.listen != before
    return {
        "ok": True,
        "needs_restart": needs_restart,
        "path": str(saved),
    }


async def peers_scan_payload(
    cfg: NetllmConfig,
    *,
    save: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Subnet-scan for LAN agents (same logic as CLI peers --subnet-scan)."""
    from netllm_discovery.lan import default_subnet_cidrs, subnet_scan_agents

    cidrs = list(cfg.swarm.subnet_cidrs) or default_subnet_cidrs()
    if not cidrs:
        return {"ok": True, "peers": [], "warnings": ["No subnet CIDRs to scan"]}

    token = (cfg.swarm.cluster_token or "").strip()
    _, port_str = (
        cfg.agent.listen.rsplit(":", 1)
        if ":" in cfg.agent.listen
        else ("127.0.0.1", "11400")
    )
    try:
        port = int(port_str)
    except ValueError:
        port = 11400

    found = await subnet_scan_agents(
        cidrs,
        port=port,
        cluster_token=token,
    )
    warnings: list[str] = []
    if save and found and config_path is not None:
        existing = {p.rstrip("/") for p in cfg.swarm.peers}
        added = 0
        for peer in found:
            url = str(peer.get("listen_url", "")).rstrip("/")
            if url and url not in existing:
                cfg.swarm.peers.append(url)
                existing.add(url)
                added += 1
        if added:
            save_config(cfg, config_path)
            warnings.append(f"Added {added} peer URL(s) to config")

    return {"ok": True, "peers": found, "warnings": warnings}


def client_env_vars(base_url: str) -> dict[str, str]:
    """OpenAI + Anthropic env vars for editor wiring."""
    base = base_url.rstrip("/")
    return {
        "OPENAI_BASE_URL": f"{base}/v1",
        "OPENAI_API_KEY": "netllm-local",
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": "netllm-local",
    }
