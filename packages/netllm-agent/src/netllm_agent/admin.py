"""Loopback-gated admin helpers for the local web dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from netllm_core.models import NetllmConfig, save_config
from netllm_core.platform import local_admin_client_hosts

from netllm_agent.service import AgentService

_CONFIG_SECTIONS = frozenset({"agent", "discovery", "swarm", "routing", "ui"})


def require_admin_access(request: Request, cfg: NetllmConfig) -> None:
    """Allow admin routes from this host or with a valid cluster token."""
    client_host = (request.client.host if request.client else "").lower()
    if client_host in local_admin_client_hosts():
        return
    token = (cfg.swarm.cluster_token or "").strip()
    if token:
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return
    raise HTTPException(
        status_code=403,
        detail="Admin routes require a local client or Bearer cluster token",
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
            "spillover_max_local_in_flight": (
                cfg.routing.spillover_max_local_in_flight
            ),
            "model_aliases": dict(cfg.routing.model_aliases),
            "backends": _backend_override_export(cfg),
            "backend_count": len(cfg.routing.backends),
            "policies": [p.model_dump(mode="json") for p in cfg.routing.policies],
            "policy_count": len(cfg.routing.policies),
        },
        "ui": {
            "auto_start_on_launch": cfg.ui.auto_start_on_launch,
            "log_dir": cfg.ui.log_dir or str(cfg.resolved_log_dir()),
            "check_for_updates_automatically": cfg.ui.check_for_updates_automatically,
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
        if "policies" in routing_patch:
            merged_policies: list[dict[str, Any]] = []
            for entry in routing_patch["policies"] or []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if (
                    not name
                    and not entry.get("model_prefix")
                    and not entry.get("api_format")
                ):
                    continue
                merged_policies.append(
                    {
                        "name": name,
                        "model_prefix": str(entry.get("model_prefix", "")),
                        "api_format": entry.get("api_format"),
                        "strategy": entry.get("strategy"),
                        "prefer_provider": entry.get("prefer_provider"),
                        "allow_cloud": bool(entry.get("allow_cloud", False)),
                        "enabled": entry.get("enabled", True),
                    }
                )
            current["routing"]["policies"] = merged_policies

    updated = NetllmConfig.model_validate(current)
    kept, rejected = _filter_own_swarm_peers(updated)
    if rejected:
        updated.swarm.peers = kept
    return updated


def _filter_own_swarm_peers(cfg: NetllmConfig) -> tuple[list[str], list[str]]:
    from netllm_discovery.lan import filter_own_peer_urls

    return filter_own_peer_urls(list(cfg.swarm.peers), cfg.agent.listen)


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
    warnings: list[str] = []
    swarm_patch = patch.get("swarm") if isinstance(patch.get("swarm"), dict) else None
    if swarm_patch is not None and "peers" in swarm_patch:
        from netllm_discovery.lan import own_agent_urls

        own = own_agent_urls(updated.agent.listen)
        rejected = [
            str(p).rstrip("/")
            for p in swarm_patch.get("peers") or []
            if str(p).rstrip("/") in own
        ]
        if rejected:
            warnings.append(
                f"Removed {len(rejected)} self peer URL(s) from swarm.peers: "
                + ", ".join(rejected)
            )
    saved = save_config(updated, config_path)
    needs_restart = updated.agent.listen != before
    result: dict[str, Any] = {
        "ok": True,
        "needs_restart": needs_restart,
        "path": str(saved),
    }
    if warnings:
        result["warnings"] = warnings
    return result


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
        from netllm_discovery.lan import own_agent_urls

        own = own_agent_urls(cfg.agent.listen)
        existing = {p.rstrip("/") for p in cfg.swarm.peers}
        added = 0
        skipped_self = 0
        for peer in found:
            url = str(peer.get("listen_url", "")).rstrip("/")
            if not url or url in existing:
                continue
            if url in own:
                skipped_self += 1
                continue
            cfg.swarm.peers.append(url)
            existing.add(url)
            added += 1
        if skipped_self:
            warnings.append(f"Skipped {skipped_self} peer URL(s) matching this agent")
        if added:
            save_config(cfg, config_path)
            warnings.append(f"Added {added} peer URL(s) to config")

    return {"ok": True, "peers": found, "warnings": warnings}


def tail_log_file(path: Path, n: int) -> tuple[list[str], bool]:
    """Return the last *n* lines from *path* and whether earlier lines were omitted."""
    if not path.is_file():
        return [], False
    try:
        size = path.stat().st_size
        if size == 0:
            return [], False
        with path.open("rb") as handle:
            block = 8192
            chunks: list[bytes] = []
            pos = size
            newline_count = 0
            while pos > 0 and newline_count <= n:
                read_len = min(block, pos)
                pos -= read_len
                handle.seek(pos)
                chunks.insert(0, handle.read(read_len))
                newline_count = b"".join(chunks).count(b"\n")
            raw_lines = b"".join(chunks).splitlines()
            truncated = len(raw_lines) > n
            tail_lines = raw_lines[-n:] if truncated else raw_lines
            return [
                line.decode("utf-8", errors="replace") for line in tail_lines
            ], truncated
    except OSError:
        return [], False


def logs_payload(cfg: NetllmConfig, *, tail: int = 200) -> dict[str, Any]:
    """Read-only agent log summary for the local dashboard."""
    limit = max(1, min(tail, 2000))
    log_dir = cfg.resolved_log_dir()
    log_file = log_dir / "agent.log"
    exists = log_file.is_file()
    size_bytes = log_file.stat().st_size if exists else 0
    lines, truncated = tail_log_file(log_file, limit) if exists else ([], False)
    return {
        "log_dir": str(log_dir),
        "log_file": str(log_file),
        "exists": exists,
        "size_bytes": size_bytes,
        "tail": lines,
        "truncated": truncated,
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
