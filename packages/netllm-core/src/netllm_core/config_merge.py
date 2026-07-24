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
"""

from __future__ import annotations

from typing import Any

from netllm_core.models import NetllmConfig

_CONFIG_SECTIONS = frozenset({"agent", "discovery", "swarm", "routing", "ui", "cloud"})

# (top-level section, key within it) pairs handled by case 2 above.
_FULL_REPLACE_DICT_PATHS: tuple[tuple[str, str], ...] = (
    ("routing", "model_pools"),
    ("routing", "model_aliases"),
    ("discovery", "provider_urls"),
)


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


def _merge_backends(cfg: NetllmConfig, entries: list[Any]) -> list[dict[str, Any]]:
    merged_backends: list[dict[str, Any]] = []
    existing_by_url = {b.base_url: b for b in cfg.routing.backends}
    for entry in entries or []:
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
            "api_format": entry.get("api_format", prior.api_format if prior else None),
            "api_key": prior.api_key if prior else "",
            "api_key_env": prior.api_key_env if prior else "",
            "enabled": entry.get("enabled", prior.enabled if prior else True),
            "local": entry.get("local", prior.local if prior else True),
        }
        if entry.get("api_key"):
            merged["api_key"] = str(entry["api_key"])
        merged_backends.append(merged)
    return merged_backends


def _merge_policies(entries: list[Any]) -> list[dict[str, Any]]:
    merged_policies: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name and not entry.get("model_prefix") and not entry.get("api_format"):
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
    return merged_policies


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
        for field in (
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
        ):
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
        for field in (
            "enabled",
            "region",
            "api_format",
            "auth",
            "api_key_env",
            "models",
            "base_url",
        ):
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
        if section not in _CONFIG_SECTIONS:
            continue
        if not isinstance(body, dict):
            continue
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
