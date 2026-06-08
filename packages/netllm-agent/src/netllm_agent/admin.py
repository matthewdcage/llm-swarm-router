"""Loopback-gated admin helpers for the local web dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from netllm_core.models import NetllmConfig

from netllm_agent.service import AgentService

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
        issues.append({
            "title": "LAN exposure without cluster token",
            "fix": "Set swarm.cluster_token when listening on 0.0.0.0",
        })

    if cfg.agent.role == "gateway" and not cfg.agent.advertise:
        issues.append({
            "title": "Gateway not advertising",
            "fix": "Set agent.advertise = true so workers can find the gateway",
        })

    enabled = [b for b in service.pool.backends if b.enabled]
    healthy = [b for b in enabled if service.pool.is_healthy(b)]
    if not healthy:
        issues.append({
            "title": "No healthy inference backends",
            "fix": "Start Ollama, LM Studio, or vLLM, then run Discover",
        })

    if cfg.swarm.mdns and cfg.agent.advertise:
        try:
            import zeroconf  # noqa: F401
            mdns_ok = True
        except ImportError:
            mdns_ok = False
        if not mdns_ok:
            issues.append({
                "title": "mDNS enabled but zeroconf unavailable",
                "fix": "Reinstall netllm (uv sync) or use static swarm.peers",
            })

    return {"ok": not issues, "issues": issues}


def config_summary(cfg: NetllmConfig) -> dict[str, Any]:
    """Non-secret config slices for read-only dashboard display."""
    token = cfg.swarm.cluster_token
    return {
        "agent": {
            "listen": cfg.agent.listen,
            "role": cfg.agent.role,
            "advertise": cfg.agent.advertise,
            "hostname": cfg.agent.hostname,
        },
        "discovery": {
            "providers": list(cfg.discovery.providers),
            "provider_urls": dict(cfg.discovery.provider_urls),
            "custom_endpoints": list(cfg.discovery.custom_endpoints),
        },
        "swarm": {
            "mdns": cfg.swarm.mdns,
            "subnet_scan": cfg.swarm.subnet_scan,
            "peers": list(cfg.swarm.peers),
            "cluster_token_set": bool(token),
        },
        "routing": {
            "default_strategy": cfg.routing.default_strategy,
            "allow_remote": cfg.routing.allow_remote,
            "backend_count": len(cfg.routing.backends),
        },
        "ui": {
            "auto_start_on_launch": cfg.ui.auto_start_on_launch,
            "log_dir": cfg.ui.log_dir or str(cfg.resolved_log_dir()),
        },
    }


def client_env_vars(base_url: str) -> dict[str, str]:
    """OpenAI + Anthropic env vars for editor wiring."""
    base = base_url.rstrip("/")
    return {
        "OPENAI_BASE_URL": f"{base}/v1",
        "OPENAI_API_KEY": "netllm-local",
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": "netllm-local",
    }
