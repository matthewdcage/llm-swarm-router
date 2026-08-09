"""Loopback-gated admin helpers for the local web dashboard."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from netllm_core import config_guards, config_merge
from netllm_core.backend_credentials import backend_override_for_url
from netllm_core.cloud_providers import CLOUD_PROVIDERS, get_provider_spec
from netllm_core.config_report import (
    deprecated_key_issues,
    schema_version_issues,
    unknown_cloud_provider_issues,
)
from netllm_core.harness_detection import detect as detect_harness
from netllm_core.known_harnesses import KNOWN_HARNESSES
from netllm_core.local_providers import api_key_env_for
from netllm_core.models import (
    NetllmConfig,
    default_config_path,
    is_lan_listen,
    listen_port,
    save_config,
)
from netllm_core.platform import local_admin_client_hosts

from netllm_agent.service import AgentService


def require_admin_access(request: Request, cfg: NetllmConfig) -> None:
    """Allow admin routes from this host or with a valid cluster token."""
    client_host = (request.client.host if request.client else "").lower()
    if client_host in local_admin_client_hosts():
        return
    token = (cfg.swarm.cluster_token or "").strip()
    if token:
        auth = request.headers.get("Authorization", "")
        if secrets.compare_digest(auth, f"Bearer {token}"):
            return
    raise HTTPException(
        status_code=403,
        detail="Admin routes require a local client or Bearer cluster token",
    )


def doctor_payload(
    cfg: NetllmConfig,
    service: AgentService,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Read-only doctor summary for the dashboard (subset of CLI doctor).

    `config_path` is the file the agent was started with. It is needed because
    the deprecation report has to read the user's actual TOML -- a validated
    model carries every field at its default and so cannot say which keys the
    user wrote. Optional, defaulting to the standard location, so existing
    two-argument callers keep working.
    """
    issues: list[dict[str, str]] = []
    notes: list[str] = []

    if is_lan_listen(cfg.agent.listen) and not cfg.swarm.cluster_token:
        notes.append(
            "LAN swarm is open (no cluster token). Enable Require cluster token "
            "in Settings on untrusted networks."
        )

    if (
        is_lan_listen(cfg.agent.listen)
        and cfg.swarm.cluster_token
        and not cfg.swarm.require_token_for_inference
    ):
        # The token secures gossip and remote admin, but /v1/* stays open to
        # the LAN until this second flag is set — an easy and consequential
        # thing to get wrong (F-14). New `init --swarm --secure` runs set it;
        # configs written before that need telling rather than rewriting.
        issues.append(
            {
                "title": "Cluster token is set but inference is open to the LAN",
                "fix": "Set swarm.require_token_for_inference = true (Settings → "
                "Require cluster token) so /v1/* needs the token too",
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
    service.pool.refresh_peer_health(force=True)
    for b in enabled:
        if b.local:
            service.pool.is_healthy(b, force_refresh=True)
    healthy = [b for b in enabled if service.pool.is_healthy(b)]
    if not healthy:
        issues.append(
            {
                "title": "No healthy inference backends",
                "fix": "Start Ollama, LM Studio, or vLLM, then run Discover",
            }
        )

    for b in enabled:
        if b.health.http_status in (401, 403) and not b.api_key:
            # Empty for `custom`, `peer:*` and cloud ids, which must fall
            # through to the generic message rather than be told to set a
            # CUSTOM_API_KEY nothing reads. That miss is why this map was
            # never derivable from `provider.upper()_API_KEY` alone.
            hint = api_key_env_for(b.provider)
            override = backend_override_for_url(cfg, b.base_url)
            if override and (override.api_key or override.api_key_env):
                fix = (
                    f"Set api_key on the Servers tab for {b.base_url} "
                    f"(routing.backends override)"
                )
            elif hint:
                fix = (
                    f"Set {hint}, set api_key on the Servers tab for {b.base_url}, "
                    f"or add api_key under [[routing.backends]]"
                )
            else:
                fix = (
                    f"Set api_key on the Servers tab or under "
                    f"[[routing.backends]] for {b.base_url}"
                )
            issues.append(
                {
                    "title": f"{b.provider} backend requires an API token "
                    f"({b.base_url})",
                    "fix": fix,
                }
            )

    if cfg.cloud.enabled:
        for provider_id, provider_cfg in cfg.cloud.providers.items():
            if not provider_cfg.enabled or provider_cfg.auth != "api_key":
                continue
            spec = get_provider_spec(provider_id)
            if spec is None:
                continue
            has_key = bool(
                provider_cfg.api_key
                or provider_cfg.api_key_env
                or os.environ.get(spec.api_key_env)
            )
            if not has_key:
                issues.append(
                    {
                        "title": f"Cloud provider {spec.display_name} is enabled "
                        "but has no API key",
                        "fix": f"Set {spec.api_key_env} or add an api_key under "
                        f"[cloud.providers.{provider_id}]",
                    }
                )

    # Unknown [cloud.providers.*] ids are preserved on save rather than
    # deleted (models.CloudConfig), so doctor is where they become visible.
    issues.extend(unknown_cloud_provider_issues(cfg))

    # Same two reports the CLI doctor runs, from the same helpers, so the
    # dashboard panel and `netllm doctor` cannot disagree about what is wrong
    # with a config.
    issues.extend(deprecated_key_issues(config_path or default_config_path()))
    issues.extend(schema_version_issues(cfg))

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

    for warning in service.peer_config_warnings():
        notes.append(warning)

    payload: dict[str, Any] = {"ok": not issues, "issues": issues}
    if notes:
        payload["notes"] = notes
    return payload


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


def _source_export(cfg: NetllmConfig) -> list[dict[str, Any]]:
    """Full editable shape of each routing.sources entry, secret blanked.

    Without this, GET /netllm/v1/config (config_summary) never surfaced
    `sources` at all -- an existing, previously-saved source would be
    invisible in the dashboard/Swift draft after a reload even though it
    was correctly persisted (docs/cli-source-routing-plan.md Phase 4b).
    """
    out = []
    for s in cfg.routing.sources:
        dumped = s.model_dump(mode="json")
        # [F-59 class] Allowlist, not a denylist. Phase 2 put
        # extra="allow" on SourceConfig so a newer client's unknown keys
        # are preserved on disk -- but blanking only "secret" would then
        # stream every one of them, credential-shaped or not, to any
        # reader. Extras stay out of the wire view; config_merge keeps
        # them on the write path, so a save does not blank them.
        dumped = {k: v for k, v in dumped.items() if k in type(s).model_fields}
        dumped["secret"] = ""
        out.append(dumped)
    return out


def _cloud_provider_export(cfg: NetllmConfig) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for provider_id, spec in CLOUD_PROVIDERS.items():
        provider_cfg = cfg.cloud.providers.get(provider_id)
        key_set = bool(
            provider_cfg
            and (provider_cfg.api_key or provider_cfg.api_key_env)
            or os.environ.get(spec.api_key_env)
        )
        out[provider_id] = {
            "display_name": spec.display_name,
            "enabled": provider_cfg.enabled if provider_cfg else False,
            "region": provider_cfg.region if provider_cfg else "",
            "api_format": provider_cfg.api_format if provider_cfg else None,
            "auth": provider_cfg.auth if provider_cfg else "api_key",
            # Exported because both surfaces now render them. A control
            # bound to a value the summary never sends reads as empty and
            # POSTs "" back, erasing what was configured -- the same class
            # of defect tests/test_dashboard_ui_wiring.py was written for.
            "api_key_env": provider_cfg.api_key_env if provider_cfg else "",
            "base_url": provider_cfg.base_url if provider_cfg else "",
            "api_key_set": key_set,
            "models": list(provider_cfg.models) if provider_cfg else [],
            "regions": list(spec.endpoints.keys()),
            "auth_modes": list(spec.auth_modes),
            "default_api_format": spec.default_api_format,
            "notes": spec.notes,
        }
    return out


def local_provider_registry_payload() -> list[dict[str, Any]]:
    """Static registry data for every discoverable local provider.

    The local-side twin of `cloud_provider_registry_payload`. It did not
    exist, and its absence was the whole reason the clients hand-mirrored the
    roster: the cloud tab could derive itself from
    `GET /netllm/v1/cloud/providers`, while the discovery tab had nothing to
    fetch, so `dashboard.js`, `SettingsViewModel.swift`, `AppConfig.swift` and
    `SettingsWindowView.swift` each kept their own copy. Those copies had
    already drifted -- the Swift app prefilled vLLM on :1234, which is LM
    Studio's port -- and `.capitalized` rendered "Omlx"/"Lmstudio"/"Vllm".

    Serving the facts is what lets those copies become a degraded-mode
    fallback rather than the source of truth (PROGRAM.md Axis B / Phase 4).
    """
    from netllm_core.local_providers import LOCAL_PROVIDERS

    return [
        {
            "id": spec.id,
            "display_name": spec.display_name,
            "short_label": spec.short_label,
            "default_ports": list(spec.default_ports),
            "platforms": list(spec.platforms),
            "port_env": spec.port_env,
            "api_key_env": spec.api_key_env,
            "host_env": spec.host_env,
            "offline_hint": spec.offline_hint,
        }
        for spec in LOCAL_PROVIDERS.values()
    ]


def cloud_provider_registry_payload() -> list[dict[str, Any]]:
    """Static registry data for every pre-configured cloud provider.

    Single source of truth consumed by the macOS app and web dashboard so
    display metadata (name, notes, regions, auth modes) never has to be
    hand-mirrored client-side — only the *shape* of user-editable fields
    (enabled/region/api_format) is mirrored, per the deep-merge contract
    documented in docs/cloud-providers-plan.md.
    """
    return [
        {
            "id": provider_id,
            "display_name": spec.display_name,
            "notes": spec.notes,
            "regions": list(spec.endpoints.keys()),
            "auth_modes": list(spec.auth_modes),
            "default_api_format": spec.default_api_format,
            "api_key_env": spec.api_key_env,
        }
        for provider_id, spec in CLOUD_PROVIDERS.items()
    ]


def harness_registry_payload(cfg: NetllmConfig) -> list[dict[str, Any]]:
    """Known-harness registry merged with configured routing.sources state
    and live PATH detection (docs/cli-source-routing-plan.md Phase 4c).

    Purely computed on request -- touches no state, mutates no config.
    `detected` never influences `enabled`: a source can be legitimately
    enabled here while its CLI lives only on a peer machine or a remote
    client (netllm's swarm/local_spillover model), so detection only
    changes display, never routing.

    `icon_url` follows a fixed convention -- `/ui/icons/harnesses/<id>.svg`,
    served from the static mount -- so every KNOWN_HARNESSES entry needs a
    matching file (see static/icons/harnesses/README.md for provenance);
    there is no per-entry override in the registry.
    """
    configured_by_known_id = {s.known_id: s for s in cfg.routing.sources if s.known_id}
    out = []
    for known in KNOWN_HARNESSES:
        source = configured_by_known_id.get(known.id)
        out.append(
            {
                "id": known.id,
                "display_name": known.display_name,
                "configured": source is not None,
                "enabled": source.enabled if source else False,
                "detected": detect_harness(known),
                "install_hint": known.install_hint,
                "docs_url": known.docs_url,
                "icon_url": f"/ui/icons/harnesses/{known.id}.svg",
            }
        )
    return out


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
            "max_concurrency": cfg.agent.max_concurrency,
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
            "peer_stale_after_s": cfg.swarm.peer_stale_after_s,
            "rediscover_interval_s": cfg.swarm.rediscover_interval_s,
            "peers": list(cfg.swarm.peers),
            "cluster_token_set": bool(token),
            "require_token_for_inference": cfg.swarm.require_token_for_inference,
        },
        "routing": {
            "default_strategy": cfg.routing.default_strategy,
            "allow_remote": cfg.routing.allow_remote,
            "spillover_max_local_in_flight": (
                cfg.routing.spillover_max_local_in_flight
            ),
            "max_in_flight_per_backend": cfg.routing.max_in_flight_per_backend,
            "follow_gateway": cfg.routing.follow_gateway,
            "health_ttl_s": cfg.routing.health_ttl_s,
            "offline_retry_s": cfg.routing.offline_retry_s,
            "max_backend_failures": cfg.routing.max_backend_failures,
            # F-22's two knobs: promoted to config in bb3eae0 and never
            # exported, so no surface could show or edit them and the
            # 120 s read timeout stayed effectively source-only.
            "upstream_connect_timeout_s": cfg.routing.upstream_connect_timeout_s,
            "upstream_read_timeout_s": cfg.routing.upstream_read_timeout_s,
            "lan_defaults_applied": cfg.routing.lan_defaults_applied,
            "model_aliases": dict(cfg.routing.model_aliases),
            "model_pools": {
                name: pool.model_dump(mode="json")
                for name, pool in cfg.routing.model_pools.items()
            },
            "backends": _backend_override_export(cfg),
            "backend_count": len(cfg.routing.backends),
            "policies": [p.model_dump(mode="json") for p in cfg.routing.policies],
            "policy_count": len(cfg.routing.policies),
            "sources": _source_export(cfg),
            "source_count": len(cfg.routing.sources),
        },
        # Every UiConfig field, not a hand-picked three. The dashboard renders
        # this section straight from the schema, so a field the schema
        # declares but this export omits rendered against `undefined`: the six
        # menubar_* toggles showed CHECKED against a stored False, and
        # touching the model_favorites editor POSTed [] -- which, because
        # lists are full-replace, wiped favourites set from the macOS app.
        # Derived from model_fields so a new UiConfig field cannot be
        # forgotten here; log_dir keeps its resolved-path substitution.
        "ui": {
            **cfg.ui.model_dump(mode="json"),
            "log_dir": cfg.ui.log_dir or str(cfg.resolved_log_dir()),
        },
        "cloud": {
            "enabled": cfg.cloud.enabled,
            "fallback": cfg.cloud.fallback,
            "fallback_enabled": cfg.cloud.fallback_enabled,
            "providers": _cloud_provider_export(cfg),
        },
    }


def apply_config_patch(cfg: NetllmConfig, patch: dict[str, Any]) -> NetllmConfig:
    """Merge dashboard-editable config sections, apply the shared write-path
    guards, and validate.

    Merge mechanics live in netllm_core.config_merge and the guards in
    netllm_core.config_guards -- both shared with the CLI/macOS save path
    (netllm_cli.config_json.import_config) so the two writers can no longer
    diverge on what they enforce. See docs/config-guards-audit.md and
    docs/architecture/07-findings-register.md F-02.
    """
    from netllm_discovery.lan import own_agent_urls

    updated = config_merge.apply_config_patch(cfg, patch)
    try:
        config_guards.apply_config_guards(
            updated, own_agent_urls=own_agent_urls(updated.agent.listen)
        )
    except config_guards.ConfigGuardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return updated


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
    from netllm_discovery.lan import (
        default_subnet_cidrs,
        own_agent_urls,
        subnet_scan_agents,
    )

    cidrs = list(cfg.swarm.subnet_cidrs) or default_subnet_cidrs()
    if not cidrs:
        return {"ok": True, "peers": [], "warnings": ["No subnet CIDRs to scan"]}

    token = (cfg.swarm.cluster_token or "").strip()
    port = listen_port(cfg.agent.listen)

    found = await subnet_scan_agents(
        cidrs,
        port=port,
        cluster_token=token,
    )
    own = own_agent_urls(cfg.agent.listen)
    for peer in found:
        url = str(peer.get("listen_url", "")).rstrip("/")
        peer["self"] = peer.get("agent_id", "") == cfg.agent.agent_id or url in own
    warnings: list[str] = []
    if save and found and config_path is not None:
        existing = {p.rstrip("/") for p in cfg.swarm.peers}
        added = 0
        skipped_self = 0
        for peer in found:
            url = str(peer.get("listen_url", "")).rstrip("/")
            if not url or url in existing:
                continue
            if peer.get("self") or url in own:
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
