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

from netllm_core.cloud_providers import get_provider_spec
from netllm_core.cloud_verification import (
    GATE_ALLOW,
    GATE_UNVERIFIED,
    gate_decision,
)
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


def enforce_cloud_provider_verification(
    cfg: NetllmConfig,
    previous: NetllmConfig | None = None,
    warnings: list[str] | None = None,
) -> None:
    """A cloud provider may only be enabled once its credential has been
    checked against the provider.

    The dashboard hides the toggle behind the same rule, but UI-only gating
    is theatre: `netllm cloud enable`, `netllm config import` (the macOS
    Save button) and `POST /netllm/v1/admin/config` all write the same field,
    and only this function is on all three paths. Three outcomes:

    1. **Enabled with no credential source at all** — switched back off, on
       every path, new or pre-existing. It needs no network and no record: a
       keyless provider is skipped by
       `_materialize_cloud_provider_backends` and has therefore never once
       served a request, so switching it off takes nothing away and stops a
       page claiming a failover that cannot fire. That is the reported bug,
       stated exactly.

    2. **Newly enabled without a current, passing check** — switched back
       off, with a warning naming the specific blocker, so the user is sent
       to Verify rather than handed a switch that lies. "Current" is
       fingerprint-matched wherever the key is visible: a check that passed
       against a key since replaced does not certify the replacement.

       Off-with-a-warning rather than a hard 400, because this is also the
       bulk path — `netllm config export | netllm config import` moves a
       whole config between machines, and one unverified provider must not
       reject an entire config. It lands disabled and says why, which is the
       honest state on a machine that has never checked it.

    3. **Already enabled and unverified** — left on, warned. An upgrade from
       a build before this feature has `enabled = true` and no record
       anywhere; switching off a provider that has been serving failover for
       months because this release grew a field would be a worse bug than the
       one being fixed. Migration is a warning, never a demotion.

    Ambiguity fails open throughout: only `CONCLUSIVE_FAILURES` (no key,
    401/403, no endpoint for this region) count as a failed check. A refused
    connection, or a provider with no catalogue API, is absence of evidence
    — and a flaky network must not stop someone configuring failover.
    """
    sink = warnings if warnings is not None else []
    for provider_id, provider_cfg in cfg.cloud.providers.items():
        if not provider_cfg.enabled:
            continue
        spec = get_provider_spec(provider_id)
        if spec is None:
            # Unknown to this build: materializes nothing and is reported by
            # config_report.unknown_cloud_provider_issues. Not this guard's
            # business, and guessing at a newer release's provider would be.
            continue
        verdict, reason = gate_decision(provider_cfg, spec)
        if verdict == GATE_ALLOW:
            continue
        was_enabled = bool(
            previous is not None
            and provider_id in previous.cloud.providers
            and previous.cloud.providers[provider_id].enabled
        )
        if verdict == GATE_UNVERIFIED and was_enabled:
            sink.append(
                f"cloud.providers.{provider_id} is enabled but unverified: "
                f"{reason} It was left on — run `netllm cloud verify "
                f"{provider_id}`, or press Verify key on the Cloud page."
            )
            continue
        provider_cfg.enabled = False
        sink.append(
            f"cloud.providers.{provider_id} was not enabled: {reason} "
            f"Verify the credential first (Cloud page: Verify key · CLI: "
            f"`netllm cloud verify {provider_id}`)."
        )


def apply_config_guards(
    cfg: NetllmConfig,
    *,
    own_agent_urls: Iterable[str] = (),
    previous: NetllmConfig | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Run every write-path guard against a freshly merged config.

    Mutates `cfg` in place (self-peer removal, LAN mesh defaults, cloud
    provider demotion) and returns the rejected peer URLs. Raises
    `ConfigGuardError` when the config must not be persisted.

    `previous` is the config as it stood *before* the patch. Only the cloud
    verification gate uses it, and it uses it for one thing: telling a user
    newly switching a provider on (refuse, they can verify) from a config
    that has had it on since before this feature existed (warn, never
    demote). A caller that cannot supply it gets the stricter reading,
    which is the safe default for a caller that does not know its own
    starting point.
    """
    rejected = drop_own_swarm_peers(cfg, own_agent_urls)
    ensure_lan_mesh_defaults(cfg)
    validate_elevated_sources(cfg)
    enforce_cloud_provider_verification(cfg, previous, warnings)
    return rejected
