"""Shared config-patch merge logic for both save paths: `netllm config
import` (netllm_cli.config_json -- the macOS app's Save button, a
subprocess call, not HTTP) and `POST /netllm/v1/admin/config` (the web
dashboard). Before this module existed, each path hand-rolled its own
recursive deep-merge independently and had drifted: the dashboard grew
explicit rebuild-with-secret-preservation logic for
backends/policies/sources/cloud.providers that the CLI path never got, and
neither path ever handled deleting a `routing.model_pools`/`model_aliases`
entry (a key simply absent from the patch is indistinguishable from "leave
it alone" under plain recursive dict-merge). See docs/config-guards-audit.md
for the full current-vs-ideal rationale this module closes.

Three merge behaviors, chosen per field:
  1. Scalars and lists: the patch value always fully replaces (a list is
     never "merged", so omitting an entry already deletes it correctly --
     this was already true before this module existed).
  2. `_FULL_REPLACE_DICT_PATHS`: dicts whose owning UI always sends the
     complete current dict on Save and that hold no write-only sub-fields
     (routing.model_pools, routing.model_aliases, discovery.provider_urls)
     -- also fully replace, so a key omitted from the patch is a deletion.
  3. Everything else under agent/discovery/swarm/routing/ui/cloud: a plain
     recursive dict-merge that preserves any key the patch omits -- this is
     load-bearing for discovery/swarm/ui, which are intentionally raw
     pass-through dicts on the Swift side so a Python field with no Swift
     model yet still round-trips untouched. routing.backends/policies
     already behave correctly under this (they're lists); routing.sources
     and cloud.providers need the additional identity-keyed rebuild below
     to preserve their write-only secret fields when a patch omits them.
  4. Sections and keys this build has no model for: merged through by the
     same case-3 rule and re-emitted on save. `NetllmConfig` and every
     model under it allow extras (netllm_core.models.ConfigModel), so a
     `[future_section]` written by a newer agent survives an older agent's
     Save instead of being deleted -- and a newer *client* patching an
     older agent gets its new keys stored rather than filtered out.
"""

from __future__ import annotations

from typing import Any

from netllm_core.models import (
    BackendOverride,
    NetllmConfig,
    RoutingPolicy,
)

# The six editable sections. Derived, not restated: a seventh section added
# to NetllmConfig must not need a second edit here to become savable.
_CONFIG_SECTIONS = frozenset(NetllmConfig.model_fields)

# (top-level section, key within it) pairs handled by case 2 above.
_FULL_REPLACE_DICT_PATHS: tuple[tuple[str, str], ...] = (
    ("routing", "model_pools"),
    ("routing", "model_aliases"),
    ("discovery", "provider_urls"),
)

# Case 3's twin: dict-typed section fields that deliberately deep-merge, so
# a key omitted from a patch is preserved rather than deleted. Declared
# rather than inferred because it is the same genuine semantic choice as
# _FULL_REPLACE_DICT_PATHS -- `cloud.providers` holds write-only api_keys
# and is rebuilt identity-keyed below, so full replace would blank a
# provider the sending UI happened not to render.
# `tests/test_config_forward_compat.py` asserts every dict field on a
# section model appears in exactly one of the two rosters, so a new one
# cannot inherit a default by accident (the bug class 0c4489d was filed for).
_DEEP_MERGE_DICT_PATHS: tuple[tuple[str, str], ...] = (("cloud", "providers"),)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Case 3 above: recursive dict-merge, patch keys win, omitted keys
    are preserved. Exposed for the catch-all sections; callers needing
    case 1/2 behavior should go through apply_config_patch instead."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# Fields the client must never set directly on a [[routing.backends]] row.
# `api_key` is write-only (handled separately: empty keeps the stored value);
# `cloud_provider` is server-materialized from [cloud.providers.<id>] and is
# marked read_only in the schema document, so a patch echoing it back must
# not be able to retag a hand-authored row.
_BACKEND_CLIENT_SET_EXCLUDED = frozenset({"api_key", "cloud_provider"})
# api_key_env has no editor on any surface today; it is preserved from the
# prior row rather than accepted from a patch (unchanged behavior).
_BACKEND_PRESERVE_ONLY = frozenset({"api_key_env"})


def _merge_backends(cfg: NetllmConfig, entries: list[Any]) -> list[dict[str, Any]]:
    """Rebuild [[routing.backends]] from a patch, preserving stored secrets.

    Built by starting from the prior row's full dump (or the model's own
    defaults for a new row) and copying the patch's values over it, rather
    than hand-listing the fields to carry forward. A field added to
    `BackendOverride` is therefore preserved by default instead of being
    silently reset on every save -- the failure mode that dropped
    `max_concurrency` and `cloud_provider` (see
    docs/architecture/07-findings-register.md F-01). `tests/test_config_merge.py`
    asserts the merged dict still covers every model field.
    """
    merged_backends: list[dict[str, Any]] = []
    existing_by_url = {b.base_url: b for b in cfg.routing.backends}
    known_fields = set(BackendOverride.model_fields)
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        base_url = str(entry.get("base_url", "")).strip()
        if not base_url:
            continue
        prior = existing_by_url.get(base_url)
        merged: dict[str, Any] = (
            prior.model_dump(mode="json")
            if prior is not None
            else BackendOverride(base_url=base_url).model_dump(mode="json")
        )
        merged["base_url"] = base_url
        for field, value in entry.items():
            if field not in known_fields:
                continue
            if field in _BACKEND_CLIENT_SET_EXCLUDED:
                continue
            if field in _BACKEND_PRESERVE_ONLY:
                continue
            merged[field] = value
        # Write-only: an empty/omitted key keeps the previously stored one.
        if entry.get("api_key"):
            merged["api_key"] = str(entry["api_key"])
        merged_backends.append(merged)
    return merged_backends


def _merge_policies(entries: list[Any]) -> list[dict[str, Any]]:
    """Rebuild [[routing.policies]] from a patch.

    Policies have no stable identity key (they are positional and matched in
    order), so there is no prior row to merge onto -- each entry is rebuilt
    from the model's defaults plus whatever the patch sends. Copying by
    `model_fields` rather than a hand-listed tuple is what keeps a new field
    from being dropped; `RoutingPolicy.source` was lost that way, silently
    widening a source-scoped policy to every caller (F-01).
    """
    merged_policies: list[dict[str, Any]] = []
    known_fields = set(RoutingPolicy.model_fields)
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name and not entry.get("model_prefix") and not entry.get("api_format"):
            continue
        merged: dict[str, Any] = RoutingPolicy().model_dump(mode="json")
        for field, value in entry.items():
            if field not in known_fields:
                continue
            merged[field] = value
        merged["name"] = name
        merged_policies.append(merged)
    return merged_policies


# Fields a patch may set on a [[routing.sources]] row. `id` is the identity
# key (copied separately) and `secret` is write-only (an empty/omitted value
# keeps the stored one), so the roster is every other SourceConfig field --
# asserted as such in tests/test_config_forward_compat.py. Hand-written
# rather than derived so that adding a field is a deliberate decision about
# whether clients may set it; the parity test is what makes forgetting loud
# (F-01: a field missing here is silently unsavable on every surface).
_MERGE_SOURCE_FIELDS: tuple[str, ...] = (
    "known_id",
    "enabled",
    "description",
    "secret_env",
    "strategy",
    "local_only",
    "allow_cloud",
    "prefer_provider",
    "cloud_providers",
    "max_concurrency",
    "model_rewrites",
    "scenarios",
    "match",
)

# Same contract for [cloud.providers.<id>]: every CloudProviderConfig field
# except the write-only api_key.
_MERGE_CLOUD_PROVIDER_FIELDS: tuple[str, ...] = (
    "enabled",
    "region",
    "api_format",
    "auth",
    "api_key_env",
    "models",
    "base_url",
)


def _merge_sources(cfg: NetllmConfig, entries: list[Any]) -> list[dict[str, Any]]:
    merged_sources: list[dict[str, Any]] = []
    existing_by_id = {s.id: s for s in cfg.routing.sources}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("id", "")).strip()
        if not source_id:
            continue
        prior = existing_by_id.get(source_id)
        merged_source: dict[str, Any] = prior.model_dump(mode="json") if prior else {}
        merged_source["id"] = source_id
        for field in _MERGE_SOURCE_FIELDS:
            if field in entry:
                merged_source[field] = entry[field]
        # secret is write-only: an empty/omitted value keeps the
        # previously stored secret instead of blanking it out.
        if entry.get("secret"):
            merged_source["secret"] = str(entry["secret"])
        elif prior is not None:
            merged_source["secret"] = prior.secret
        merged_sources.append(merged_source)
    return merged_sources


def _merge_cloud_providers(
    cfg: NetllmConfig, providers_patch: dict[str, Any]
) -> dict[str, Any]:
    existing_providers = cfg.cloud.providers
    merged_providers: dict[str, Any] = {
        pid: p.model_dump(mode="json") for pid, p in existing_providers.items()
    }
    for provider_id, entry in providers_patch.items():
        if not isinstance(entry, dict):
            continue
        prior = existing_providers.get(provider_id)
        merged_entry: dict[str, Any] = prior.model_dump(mode="json") if prior else {}
        for field in _MERGE_CLOUD_PROVIDER_FIELDS:
            if field in entry:
                merged_entry[field] = entry[field]
        # Keys are write-only: an empty/omitted value keeps the
        # previously stored key instead of blanking it out.
        if entry.get("api_key"):
            merged_entry["api_key"] = str(entry["api_key"])
        elif prior is not None:
            merged_entry["api_key"] = prior.api_key
        merged_providers[provider_id] = merged_entry
    return merged_providers


def apply_config_patch(cfg: NetllmConfig, patch: dict[str, Any]) -> NetllmConfig:
    """Merge a save-path patch (from the CLI/macOS app or the web
    dashboard) over cfg per the three cases in this module's docstring,
    validate, and return the updated config. Does not persist or apply
    any endpoint-specific post-merge checks (LAN mesh defaults, own-peer
    filtering, elevated-source secret enforcement) -- callers apply those
    themselves, since they differ between the CLI and HTTP callers.
    """
    if not patch:
        return cfg

    current = cfg.model_dump(mode="json")

    for section, body in patch.items():
        if not isinstance(body, dict):
            # Top-level scalars have no meaning in this schema (the config
            # is sections all the way down). Anything cfg already carries
            # at top level stays -- it came from `current` above.
            continue
        # Sections outside _CONFIG_SECTIONS are merged through rather than
        # dropped (case 4): NetllmConfig allows extras, so a `[future_section]`
        # a newer client sends is stored instead of being filtered out on the
        # way in and then deleted from disk by the save that follows.
        current[section] = deep_merge(current.get(section, {}), body)

    for top, sub in _FULL_REPLACE_DICT_PATHS:
        top_patch = patch.get(top)
        if (
            isinstance(top_patch, dict)
            and sub in top_patch
            and isinstance(top_patch[sub], dict)
        ):
            current.setdefault(top, {})[sub] = top_patch[sub]

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
            current["routing"]["backends"] = _merge_backends(
                cfg, routing_patch["backends"]
            )
        if "policies" in routing_patch:
            current["routing"]["policies"] = _merge_policies(routing_patch["policies"])
        if "sources" in routing_patch:
            current["routing"]["sources"] = _merge_sources(
                cfg, routing_patch["sources"]
            )

    if "cloud" in patch and isinstance(patch["cloud"], dict):
        cloud_patch = patch["cloud"]
        if "providers" in cloud_patch and isinstance(cloud_patch["providers"], dict):
            current["cloud"]["providers"] = _merge_cloud_providers(
                cfg, cloud_patch["providers"]
            )

    return NetllmConfig.model_validate(current)
