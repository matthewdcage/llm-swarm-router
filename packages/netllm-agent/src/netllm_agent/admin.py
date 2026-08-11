"""Loopback-gated admin helpers for the local web dashboard."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from netllm_core import config_guards, config_merge
from netllm_core.backend_credentials import (
    backend_override_for_url,
    ignored_url_conflicts,
)
from netllm_core.cloud_providers import CLOUD_PROVIDERS, get_provider_spec
from netllm_core.config_report import (
    deprecated_key_issues,
    schema_version_issues,
    unknown_cloud_provider_issues,
)
from netllm_core.doctor_checks import (
    doctor_check,
    doctor_report,
    extend_or_pass,
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


# --- structured doctor (UI-6) ------------------------------------------------
#
# Row shape, severities, action kinds and the issues/notes derivation all live
# in `netllm_core.doctor_checks` so this payload and `netllm doctor --json`
# cannot drift apart -- read that module's docstring for the contract.

#: Every check id `doctor_payload` can emit, in emission order. Stable strings:
#: they are the join key for a client's fix button and the thing a support
#: bundle is diffed on. A check that fans out over several subjects emits one
#: row per subject with the same `id` and a distinct `subject`.
#:
#: `agent.port_conflict` is deliberately NOT here. `netllm serve` acquires the
#: singleton lock in the same process that runs the app
#: (`serve_lifecycle._acquire_serve_lock`), so an agent answering this route is
#: always the lock holder and the check could only ever report a tautology.
#: The detection that means something runs in the CLI, which probes the port
#: it is about to bind -- and §5 of docs/ui-redesign-feature-spec.md puts the
#: remediation there too, because stopping an arbitrary pid is not a power the
#: web admin surface should acquire for one button.
DOCTOR_CHECK_IDS = (
    "swarm.open_lan_no_token",
    "swarm.token_but_open_inference",
    "agent.gateway_advertise",
    "backends.healthy",
    "backends.auth_required",
    "cloud.provider_key",
    "cloud.provider_verified",
    "cloud.unknown_provider",
    "config.deprecated_key",
    "config.schema_version",
    "swarm.mdns_available",
    "swarm.peer_config",
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

    Returns `{ok, checks, issues, notes}`. See the block comment above
    `DOCTOR_CHECK_IDS` for the derivation contract between the three lists.
    """
    checks: list[dict[str, Any]] = []

    open_lan = is_lan_listen(cfg.agent.listen) and not cfg.swarm.cluster_token
    checks.append(
        doctor_check(
            "swarm.open_lan_no_token",
            ok=not open_lan,
            severity="warn",
            title=(
                "LAN swarm is open (no cluster token)"
                if open_lan
                else "LAN exposure is gated by a cluster token"
            ),
            # For a warn-severity row `detail` IS the legacy note string, so
            # `notes` derives byte-identically. Do not reword without moving
            # the wording assertion in tests/test_doctor_structured.py.
            detail=(
                "LAN swarm is open (no cluster token). Enable Require cluster token "
                "in Settings on untrusted networks."
                if open_lan
                else f"agent.listen is {cfg.agent.listen}"
            ),
            fix=(
                "Set swarm.cluster_token (Settings → Require cluster token) on "
                "untrusted networks"
                if open_lan
                else ""
            ),
            action={"kind": "navigate", "label": "Open Network", "target": "network"}
            if open_lan
            else None,
        )
    )

    # The token secures gossip and remote admin, but /v1/* stays open to
    # the LAN until this second flag is set — an easy and consequential
    # thing to get wrong (F-14). New `init --swarm --secure` runs set it;
    # configs written before that need telling rather than rewriting.
    inference_open = (
        is_lan_listen(cfg.agent.listen)
        and bool(cfg.swarm.cluster_token)
        and not cfg.swarm.require_token_for_inference
    )
    checks.append(
        doctor_check(
            "swarm.token_but_open_inference",
            ok=not inference_open,
            title=(
                "Cluster token is set but inference is open to the LAN"
                if inference_open
                else "Inference is gated the same way gossip is"
            ),
            detail=(
                f"agent.listen is {cfg.agent.listen} and a cluster token is set, "
                "but swarm.require_token_for_inference is false, so /v1/* is "
                "reachable without it."
                if inference_open
                else f"agent.listen is {cfg.agent.listen}"
            ),
            fix=(
                "Set swarm.require_token_for_inference = true (Settings → "
                "Require cluster token) so /v1/* needs the token too"
                if inference_open
                else ""
            ),
            action={
                "kind": "config_patch",
                "label": "Require the token for inference",
                "endpoint": "/netllm/v1/admin/config",
                "method": "POST",
                "params": {"swarm": {"require_token_for_inference": True}},
            }
            if inference_open
            else None,
        )
    )

    gateway_silent = cfg.agent.role == "gateway" and not cfg.agent.advertise
    checks.append(
        doctor_check(
            "agent.gateway_advertise",
            ok=not gateway_silent,
            title=(
                "Gateway not advertising"
                if gateway_silent
                else f"Role {cfg.agent.role} advertises correctly"
            ),
            detail=(
                "agent.role is gateway but agent.advertise is false, so workers "
                "cannot discover it."
                if gateway_silent
                else f"agent.role={cfg.agent.role}, "
                f"agent.advertise={cfg.agent.advertise}"
            ),
            fix=(
                "Set agent.advertise = true so workers can find the gateway"
                if gateway_silent
                else ""
            ),
            action={
                "kind": "config_patch",
                "label": "Advertise this gateway",
                "endpoint": "/netllm/v1/admin/config",
                "method": "POST",
                "params": {"agent": {"advertise": True}},
            }
            if gateway_silent
            else None,
        )
    )

    enabled = [b for b in service.pool.backends if b.enabled]
    service.pool.refresh_peer_health(force=True)
    for b in enabled:
        if b.local:
            service.pool.is_healthy(b, force_refresh=True)
    healthy = [b for b in enabled if service.pool.is_healthy(b)]
    checks.append(
        doctor_check(
            "backends.healthy",
            ok=bool(healthy),
            title=(
                f"{len(healthy)} of {len(enabled)} enabled backends healthy"
                if healthy
                else "No healthy inference backends"
            ),
            detail=(
                ", ".join(b.base_url for b in healthy)
                if healthy
                else f"{len(enabled)} enabled backend(s), none answering."
            ),
            fix=(
                "" if healthy else "Start Ollama, LM Studio, or vLLM, then run Discover"
            ),
            action=None
            if healthy
            else {
                "kind": "admin_post",
                "label": "Run discovery",
                "endpoint": "/netllm/v1/admin/discover",
                "method": "POST",
                "params": {},
            },
        )
    )

    needs_key = [
        b for b in enabled if b.health.http_status in (401, 403) and not b.api_key
    ]
    for b in needs_key:
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
        if override is None:
            # A *discovered* 401 is often not a netllm backend at all -- an
            # unrelated service squatting a provider's default port. Naming
            # the denylist here is the difference between "supply a key you
            # do not have" and "this is not yours; stop offering it".
            fix += (
                f". If it is not a backend of yours, ignore it: "
                f"netllm ignore add {b.base_url}"
            )
        checks.append(
            doctor_check(
                "backends.auth_required",
                ok=False,
                title=f"{b.provider} backend requires an API token ({b.base_url})",
                detail=f"HTTP {b.health.http_status} from {b.base_url} with no key.",
                fix=fix,
                subject=b.base_url,
                action={
                    "kind": "navigate",
                    "label": "Open Backends",
                    "target": "backends",
                },
            )
        )
    if not needs_key:
        checks.append(
            doctor_check(
                "backends.auth_required",
                ok=True,
                title="No backend is rejecting requests for a missing key",
                detail=f"{len(enabled)} enabled backend(s) checked.",
            )
        )

    cloud_missing_key: list[tuple[str, Any]] = []
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
                cloud_missing_key.append((provider_id, spec))
    for provider_id, spec in cloud_missing_key:
        checks.append(
            doctor_check(
                "cloud.provider_key",
                ok=False,
                title=f"Cloud provider {spec.display_name} is enabled but has no "
                "API key",
                detail=f"[cloud.providers.{provider_id}] is enabled with "
                f"auth = api_key and neither api_key, api_key_env nor "
                f"${spec.api_key_env} resolves.",
                fix=f"Set {spec.api_key_env} or add an api_key under "
                f"[cloud.providers.{provider_id}]",
                subject=provider_id,
                action={"kind": "navigate", "label": "Open Cloud", "target": "cloud"},
            )
        )
    if not cloud_missing_key:
        checks.append(
            doctor_check(
                "cloud.provider_key",
                ok=True,
                title="Every enabled cloud provider has a key",
                detail=(
                    "cloud.enabled is false"
                    if not cfg.cloud.enabled
                    else f"{len(cfg.cloud.providers)} provider entr"
                    f"{'y' if len(cfg.cloud.providers) == 1 else 'ies'} checked."
                ),
            )
        )

    # Enabled and keyed, but never actually checked against the provider —
    # the state a config written before UI-7a lands in, and the one the write
    # gate deliberately grandfathers rather than switching off. Grandfathering
    # is the right call for a provider that has been serving requests, and
    # exactly wrong if it never worked, and doctor is the only surface that
    # can tell the user which of the two they have.
    from netllm_core.cloud_verification import verification_state

    cloud_unverified: list[tuple[str, Any, dict[str, Any]]] = []
    if cfg.cloud.enabled:
        for provider_id, provider_cfg in cfg.cloud.providers.items():
            if not provider_cfg.enabled:
                continue
            spec = get_provider_spec(provider_id)
            if spec is None or any(pid == provider_id for pid, _ in cloud_missing_key):
                continue
            state = verification_state(provider_cfg, spec)
            if not state["ok"]:
                cloud_unverified.append((provider_id, spec, state))
    for provider_id, spec, state in cloud_unverified:
        checks.append(
            doctor_check(
                "cloud.provider_verified",
                ok=False,
                title=f"Cloud provider {spec.display_name} is enabled but its "
                "credential is not verified",
                detail=state["blocker"],
                fix=f"Run `netllm cloud verify {provider_id}`, or press Verify "
                "key on the dashboard's Cloud page",
                subject=provider_id,
                action={"kind": "navigate", "label": "Open Cloud", "target": "cloud"},
            )
        )
    if not cloud_unverified:
        checks.append(
            doctor_check(
                "cloud.provider_verified",
                ok=True,
                title="Every enabled cloud provider has a verified credential",
                detail=(
                    "cloud.enabled is false"
                    if not cfg.cloud.enabled
                    else "Each enabled provider's key passed a check against "
                    "the provider itself."
                ),
            )
        )

    # Unknown [cloud.providers.*] ids are preserved on save rather than
    # deleted (models.CloudConfig), so doctor is where they become visible.
    extend_or_pass(
        checks,
        "cloud.unknown_provider",
        unknown_cloud_provider_issues(cfg),
        ok_title="Every [cloud.providers.*] id is recognised",
        ok_detail="No inert provider sections in this config.",
    )

    # Same two reports the CLI doctor runs, from the same helpers, so the
    # dashboard panel and `netllm doctor` cannot disagree about what is wrong
    # with a config.
    extend_or_pass(
        checks,
        "config.deprecated_key",
        deprecated_key_issues(config_path or default_config_path()),
        ok_title="No deprecated config keys",
        ok_detail="Nothing in this config.toml is on the deprecation clock.",
    )
    extend_or_pass(
        checks,
        "config.schema_version",
        schema_version_issues(cfg),
        ok_title=f"config.toml generation {cfg.schema_version} is understood",
        ok_detail="This build can apply every migration the file needs.",
    )

    mdns_wanted = cfg.swarm.mdns and cfg.agent.advertise
    mdns_ok = True
    if mdns_wanted:
        try:
            import zeroconf  # noqa: F401
        except ImportError:
            mdns_ok = False
    checks.append(
        doctor_check(
            "swarm.mdns_available",
            ok=mdns_ok,
            title=(
                "mDNS enabled but zeroconf unavailable"
                if not mdns_ok
                else "mDNS advertising is available"
                if mdns_wanted
                else "mDNS advertising is off"
            ),
            detail=(
                "swarm.mdns and agent.advertise are on but the zeroconf package "
                "is not importable."
                if not mdns_ok
                else f"swarm.mdns={cfg.swarm.mdns}, "
                f"agent.advertise={cfg.agent.advertise}"
            ),
            fix=(
                "Reinstall netllm (uv sync) or use static swarm.peers"
                if not mdns_ok
                else ""
            ),
            action={
                "kind": "admin_post",
                "label": "Scan LAN for peers",
                "endpoint": "/netllm/v1/admin/peers-scan",
                "method": "POST",
                "params": {},
            }
            if not mdns_ok
            else None,
        )
    )

    peer_warnings = list(service.peer_config_warnings())
    for warning in peer_warnings:
        checks.append(
            doctor_check(
                "swarm.peer_config",
                ok=False,
                severity="warn",
                title="Peer configuration warning",
                # warn-severity `detail` is the legacy note string verbatim.
                detail=warning,
                action={"kind": "navigate", "label": "Open Peers", "target": "peers"},
            )
        )
    if not peer_warnings:
        checks.append(
            doctor_check(
                "swarm.peer_config",
                ok=True,
                title="Peer configuration is consistent",
                detail="No peer reported a conflicting view of this mesh.",
            )
        )

    return doctor_report(checks)


def _backend_override_export(cfg: NetllmConfig) -> list[dict[str, Any]]:
    """Full editable shape of each routing.backends row, api_key blanked.

    Derived from the model rather than hand-listed, which is what the
    hand-listed version got wrong: it exported six of the eight fields, so
    `api_key_env`, `max_concurrency`, `cloud_provider` and (now) `row_id`
    never reached a client and could not be round-tripped back. Combined
    with a merge that keyed rows on `base_url`, editing a URL therefore
    reset `max_concurrency` to 0 along with erasing the key. The twin of
    `_source_export` below, and allowlisted for the same F-59 reason:
    `extra="allow"` means unknown keys live on the model, and streaming them
    to every reader of GET /netllm/v1/config would be a disclosure channel.

    `api_key` is write-only, so it goes out empty and `api_key_set` carries
    the only thing a client legitimately needs to know about it -- the
    omit-preserves contract in config_merge is what lets that empty value
    round-trip harmlessly.
    """
    out: list[dict[str, Any]] = []
    for b in cfg.routing.backends:
        dumped = b.model_dump(mode="json")
        dumped = {k: v for k, v in dumped.items() if k in type(b).model_fields}
        dumped["api_key"] = ""
        dumped["api_key_set"] = bool(b.api_key or b.api_key_env)
        out.append(dumped)
    return out


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
    from netllm_core.cloud_verification import verification_state

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
            # The credential-verification state, computed server-side so the
            # dashboard, the macOS app and the CLI all print the same
            # sentence about the same provider instead of each deriving its
            # own from `api_key_set` -- which is exactly how a page came to
            # show a keyless provider as ready. Carries `can_enable`, the
            # gate's own answer, so no client re-implements the rule.
            "verification": verification_state(provider_cfg, spec),
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
            # Every entry as stored. The dashboard derives "which of these is
            # overruled by a routing.backends row" client-side from the same
            # draft it is editing, so no derived key is exported here -- one
            # would be echoed straight back into the patch by the no-schema
            # fallback in buildSchemaSectionPatch and persisted as an extra.
            "ignored_urls": list(cfg.discovery.ignored_urls),
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


def apply_config_patch(
    cfg: NetllmConfig,
    patch: dict[str, Any],
    warnings: list[str] | None = None,
) -> NetllmConfig:
    """Merge dashboard-editable config sections, apply the shared write-path
    guards, and validate.

    Merge mechanics live in netllm_core.config_merge and the guards in
    netllm_core.config_guards -- both shared with the CLI/macOS save path
    (netllm_cli.config_json.import_config) so the two writers can no longer
    diverge on what they enforce. See docs/config-guards-audit.md and
    docs/architecture/07-findings-register.md F-02.

    `cfg` doubles as the guards' `previous`: config_merge never mutates it,
    so it is still the pre-patch state the cloud verification gate needs to
    tell "the user just switched this provider on" (refuse, unverified) from
    "this config has had it on since before the feature existed" (warn).
    """
    from netllm_discovery.lan import own_agent_urls

    updated = config_merge.apply_config_patch(cfg, patch)
    try:
        config_guards.apply_config_guards(
            updated,
            own_agent_urls=own_agent_urls(updated.agent.listen),
            previous=cfg,
            warnings=warnings,
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
    warnings: list[str] = []
    # Guard warnings first: a provider demoted for having no key is the most
    # important thing this response can say, and it has to be said before the
    # peer/ignore notes rather than appended after them.
    updated = apply_config_patch(cfg, patch, warnings)
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
    # An ignore entry that names a [[routing.backends]] URL is stored but
    # inert -- the explicit row wins (backend_credentials.ignored_url_keys).
    # Saying so is the difference between a documented precedence rule and a
    # user watching an endpoint they told the agent to ignore keep appearing.
    conflicts = ignored_url_conflicts(updated)
    if conflicts:
        warnings.append(
            f"{len(conflicts)} ignored URL(s) are also pinned in "
            "routing.backends and stay routable — the explicit backend wins: "
            + ", ".join(conflicts)
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


# --- structured logs (UI-11) -------------------------------------------------
#
# The agent's file handler writes
# "%(asctime)s %(levelname)s %(name)s: %(message)s"
# (netllm_cli/commands/serve_lifecycle.py). Parsing that belongs here, next to
# the format string, not in the browser: the page used to carry its own copy of
# these two regexes and would silently mis-render the day the format changed.
#
# Two shapes are recognised -- the formatter above, and a bare "LEVEL: message"
# console line (uvicorn's default, which reaches agent.log when the macOS app
# pipes stdout instead of installing the handler). A line matching neither is
# still emitted, with `level`/`logger`/`ts` null and the raw text as `message`:
# a stack-trace continuation is exactly the thing that will not match, and
# dropping it would make the page lie about what the agent logged.
_LOG_STD_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+"
    r"([A-Za-z]{3,9})\s+([\w.-]+):\s?(.*)$",
    re.DOTALL,
)
_LOG_BARE_LEVEL = re.compile(r"^([A-Z]{3,9}):\s+(.*)$", re.DOTALL)

#: Python level names -> the four levels every client filters on. Kept here so
#: the dashboard, the macOS app and any future client agree on the vocabulary
#: instead of each mapping "CRITICAL" for themselves.
LOG_LEVELS = ("error", "warn", "info", "debug")
_LOG_LEVEL_ALIASES = {
    "warning": "warn",
    "warn": "warn",
    "error": "error",
    "critical": "error",
    "fatal": "error",
    "exception": "error",
    "info": "info",
    "debug": "debug",
    "trace": "debug",
}

#: Hard ceiling on how many lines one request may return, unchanged from the
#: pre-UI-11 payload. Server-side parsing of an unbounded log has to stay
#: bounded by the same cap the raw tail always had.
LOGS_MAX_TAIL = 2000


def log_file_path(cfg: NetllmConfig) -> Path:
    """The agent log this host writes. Single definition; the tail, the record
    view and the download all resolve it through here."""
    return cfg.resolved_log_dir() / "agent.log"


def parse_log_line(raw: str, line_no: int) -> dict[str, Any]:
    """One raw log line as a record. Never returns None -- see the note above."""
    line = raw
    std = _LOG_STD_LINE.match(line)
    if std:
        return {
            "line_no": line_no,
            "ts": std.group(1),
            "level": _LOG_LEVEL_ALIASES.get(std.group(2).lower()),
            "level_label": std.group(2),
            "logger": std.group(3),
            "message": std.group(4),
            "raw": line,
        }
    bare = _LOG_BARE_LEVEL.match(line)
    if bare and bare.group(1).lower() in _LOG_LEVEL_ALIASES:
        return {
            "line_no": line_no,
            "ts": None,
            "level": _LOG_LEVEL_ALIASES[bare.group(1).lower()],
            "level_label": bare.group(1),
            "logger": None,
            "message": bare.group(2),
            "raw": line,
        }
    return {
        "line_no": line_no,
        "ts": None,
        "level": None,
        "level_label": None,
        "logger": None,
        "message": line,
        "raw": line,
    }


def count_log_lines(path: Path) -> int:
    """Total lines in *path*, counted without decoding it.

    A byte scan for b"\\n" is memchr-fast, so `total_lines` costs a read of the
    file and no allocation per line. A final line with no trailing newline
    still counts as a line, matching how the window reader splits.
    """
    total = 0
    last = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                total += chunk.count(b"\n")
                last = chunk[-1:]
    except OSError:
        return 0
    if last and last != b"\n":
        total += 1
    return total


def _read_log_window(
    path: Path, *, limit: int, before: int | None, total_lines: int
) -> list[tuple[int, str]]:
    """Up to *limit* lines ending just before 1-based line *before*.

    `before=None` means "the newest page". That case keeps the reverse-block
    read the tail always used, so the common poll never touches the whole
    file; a `before` cursor walks forward in binary and only decodes the
    window, so paging back through a large log stays proportional to the page
    and not to the page's distance from the end.
    """
    end = total_lines if before is None else min(before - 1, total_lines)
    if end <= 0 or limit <= 0:
        return []
    start = max(1, end - limit + 1)
    if before is None:
        lines, _ = tail_log_file(path, limit)
        # tail_log_file splits on every Unicode line boundary; renumber against
        # what it actually returned so line_no stays self-consistent.
        first = max(1, end - len(lines) + 1)
        return [(first + offset, text) for offset, text in enumerate(lines)]
    out: list[tuple[int, str]] = []
    try:
        with path.open("rb") as handle:
            for index, raw in enumerate(handle, start=1):
                if index < start:
                    continue
                if index > end:
                    break
                out.append(
                    (index, raw.rstrip(b"\r\n").decode("utf-8", errors="replace"))
                )
    except OSError:
        return []
    return out


def logs_payload(
    cfg: NetllmConfig, *, tail: int = 200, before: int | None = None
) -> dict[str, Any]:
    """Read-only agent log summary for the local dashboard.

    `tail`, `log_dir`, `log_file`, `exists`, `size_bytes` and `truncated` keep
    exactly the meaning they had before UI-11 -- the macOS app and any older
    dashboard read `tail` as raw formatter text and must keep working. Added
    additively: `records` (the same window parsed server-side), `total_lines`,
    and the cursor fields that make paging survive the 10 s poll.

    `before` is a 1-based line number: the returned window ends at
    `before - 1`. `next_before` is the cursor for the next older page, or
    `None` at the start of the file.
    """
    limit = max(1, min(tail, LOGS_MAX_TAIL))
    log_dir = cfg.resolved_log_dir()
    log_file = log_file_path(cfg)
    exists = log_file.is_file()
    size_bytes = log_file.stat().st_size if exists else 0
    total_lines = count_log_lines(log_file) if exists else 0
    window = (
        _read_log_window(log_file, limit=limit, before=before, total_lines=total_lines)
        if exists
        else []
    )
    first_line_no = window[0][0] if window else None
    last_line_no = window[-1][0] if window else None
    return {
        "log_dir": str(log_dir),
        "log_file": str(log_file),
        "exists": exists,
        "size_bytes": size_bytes,
        "tail": [text for _, text in window],
        # `truncated` has always meant "earlier lines were omitted", which is
        # still exactly what it means when paging: it is about the window, not
        # about the file.
        "truncated": bool(first_line_no and first_line_no > 1),
        "records": [parse_log_line(text, line_no) for line_no, text in window],
        "total_lines": total_lines,
        "first_line_no": first_line_no,
        "last_line_no": last_line_no,
        "next_before": first_line_no if first_line_no and first_line_no > 1 else None,
        "levels": list(LOG_LEVELS),
        # Same route, `download=1`. Named in the payload so a client never has
        # to build the URL, and so the page can hide the button on an agent
        # that predates it.
        "download_url": "/netllm/v1/logs?download=1",
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
