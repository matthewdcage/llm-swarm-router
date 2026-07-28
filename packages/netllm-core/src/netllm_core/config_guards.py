"""Post-merge guards every config write path must apply.

Merge *mechanics* live in `netllm_core.config_merge`; this module holds the
checks that decide whether a merged config may be persisted at all, and the
normalisations that must happen on the way to disk.

These guards used to live in `netllm_agent.admin` and therefore ran only on
`POST /netllm/v1/admin/config` (the web dashboard). The other writer --
`netllm config import`, which is the macOS Settings **Save** button, a
subprocess call rather than HTTP -- merged and saved without them. An
elevated `routing.sources` entry with no secret could be persisted on a
LAN-bound agent through the macOS app while the dashboard correctly refused
it (docs/architecture/07-findings-register.md F-02).

`netllm-core` must not import FastAPI, so the elevated-source check raises
`ConfigGuardError`; each caller maps it to its own failure mode (HTTP 400 for
the dashboard, a printed error and non-zero exit for the CLI).
"""

from __future__ import annotations

from collections.abc import Iterable

from netllm_core.models import NetllmConfig, ensure_lan_mesh_defaults, is_lan_listen


class ConfigGuardError(ValueError):
    """A merged config failed a guard and must not be persisted."""


def validate_elevated_sources(cfg: NetllmConfig) -> None:
    """A source granting cloud access or an above-default concurrency cap
    must be secret-backed once the agent is reachable beyond loopback.

    Bounds identity spoofing (attributive-by-default; see the SourceConfig
    docstring) to "cheaper local routing" — never cloud-key or budget
    exposure — without requiring every source to carry a secret.
    """
    if not is_lan_listen(cfg.agent.listen):
        return
    default_cap = cfg.routing.max_in_flight_per_backend
    for source in cfg.routing.sources:
        if not source.is_elevated(default_max_concurrency=default_cap):
            continue
        if source.resolve_secret():
            continue
        raise ConfigGuardError(
            f"routing.sources '{source.id}' grants elevated capability "
            "(allow_cloud, cloud_providers, or a max_concurrency above "
            "routing.max_in_flight_per_backend) and must set secret or "
            "secret_env while agent.listen accepts non-loopback connections"
        )


def drop_own_swarm_peers(cfg: NetllmConfig, own_urls: Iterable[str]) -> list[str]:
    """Remove `swarm.peers` entries that point back at this agent.

    `own_urls` is supplied by the caller (`netllm_discovery.lan.own_agent_urls`)
    because resolving them needs the discovery layer, which core must not
    depend on. Returns the rejected URLs so callers can warn about them.
    """
    own = {url.rstrip("/") for url in own_urls if url}
    if not own:
        return []
    kept: list[str] = []
    rejected: list[str] = []
    for peer in cfg.swarm.peers:
        if str(peer).rstrip("/") in own:
            rejected.append(str(peer).rstrip("/"))
            continue
        kept.append(peer)
    if rejected:
        cfg.swarm.peers = kept
    return rejected


def apply_config_guards(
    cfg: NetllmConfig,
    *,
    own_agent_urls: Iterable[str] = (),
) -> list[str]:
    """Run every write-path guard against a freshly merged config.

    Mutates `cfg` in place (self-peer removal, LAN mesh defaults) and returns
    the rejected peer URLs. Raises `ConfigGuardError` when the config must not
    be persisted.
    """
    rejected = drop_own_swarm_peers(cfg, own_agent_urls)
    ensure_lan_mesh_defaults(cfg)
    validate_elevated_sources(cfg)
    return rejected
