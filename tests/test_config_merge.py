"""netllm_core.config_merge -- the merge logic shared by the CLI/macOS
save path (netllm_cli.config_json.import_config) and the dashboard's
POST /netllm/v1/admin/config (netllm_agent.admin.apply_config_patch).

Regression coverage for docs/config-guards-audit.md: a dict entry omitted
from a patch must actually delete (model_pools, model_aliases,
discovery.provider_urls), while a write-only secret omitted from a patch
must be preserved, not wiped (routing.sources, cloud.providers)."""

from __future__ import annotations

from netllm_core.config_merge import (
    _merge_backends,
    _merge_policies,
    apply_config_patch,
)
from netllm_core.models import (
    BackendOverride,
    CloudProviderConfig,
    ModelPool,
    NetllmConfig,
    RoutingPolicy,
    SourceConfig,
)


def test_model_pools_entry_omitted_from_patch_is_deleted() -> None:
    cfg = NetllmConfig()
    cfg.routing.model_pools = {
        "keep": ModelPool(hosts=["h1"], models=["m1"]),
        "drop": ModelPool(hosts=["h2"], models=["m2"]),
    }
    patch = {
        "routing": {
            "model_pools": {
                "keep": {"enabled": True, "hosts": ["h1"], "models": ["m1"]}
            }
        }
    }
    updated = apply_config_patch(cfg, patch)
    assert set(updated.routing.model_pools) == {"keep"}


def test_model_aliases_entry_omitted_from_patch_is_deleted() -> None:
    cfg = NetllmConfig()
    cfg.routing.model_aliases = {"keep": ["m1"], "drop": ["m2"]}
    patch = {"routing": {"model_aliases": {"keep": ["m1"]}}}
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.model_aliases == {"keep": ["m1"]}


def test_provider_urls_entry_omitted_from_patch_is_deleted() -> None:
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {
        "omlx": ["http://127.0.0.1:8080/v1"],
        "ollama": ["http://127.0.0.1:11434/v1"],
    }
    patch = {"discovery": {"provider_urls": {"omlx": ["http://127.0.0.1:8080/v1"]}}}
    updated = apply_config_patch(cfg, patch)
    assert updated.discovery.provider_urls == {"omlx": ["http://127.0.0.1:8080/v1"]}


def test_model_pools_full_replace_does_not_disturb_other_routing_fields() -> None:
    cfg = NetllmConfig()
    cfg.routing.max_in_flight_per_backend = 7
    cfg.routing.model_pools = {"drop": ModelPool(hosts=["h"], models=["m"])}
    patch = {"routing": {"model_pools": {}}}
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.model_pools == {}
    assert updated.routing.max_in_flight_per_backend == 7


def test_source_secret_preserved_when_patch_omits_it() -> None:
    cfg = NetllmConfig()
    cfg.routing.sources = [SourceConfig(id="codex", secret="s3cr3t")]
    patch = {"routing": {"sources": [{"id": "codex", "enabled": False, "secret": ""}]}}
    updated = apply_config_patch(cfg, patch)
    assert len(updated.routing.sources) == 1
    assert updated.routing.sources[0].secret == "s3cr3t"
    assert updated.routing.sources[0].enabled is False


def test_source_known_id_set_on_new_row() -> None:
    cfg = NetllmConfig()
    patch = {
        "routing": {"sources": [{"id": "codex", "enabled": True, "known_id": "codex"}]}
    }
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.sources[0].known_id == "codex"


def test_source_known_id_preserved_when_patch_omits_it() -> None:
    cfg = NetllmConfig()
    cfg.routing.sources = [SourceConfig(id="codex", known_id="codex")]
    patch = {"routing": {"sources": [{"id": "codex", "enabled": False}]}}
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.sources[0].known_id == "codex"
    assert updated.routing.sources[0].enabled is False


def test_source_omitted_from_patch_is_deleted() -> None:
    cfg = NetllmConfig()
    cfg.routing.sources = [
        SourceConfig(id="keep"),
        SourceConfig(id="drop", secret="whatever"),
    ]
    patch = {"routing": {"sources": [{"id": "keep"}]}}
    updated = apply_config_patch(cfg, patch)
    assert [s.id for s in updated.routing.sources] == ["keep"]


def test_cloud_provider_api_key_preserved_when_patch_omits_it() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, api_key="mk-secret"
    )
    patch = {"cloud": {"providers": {"moonshot": {"enabled": False}}}}
    updated = apply_config_patch(cfg, patch)
    assert updated.cloud.providers["moonshot"].api_key == "mk-secret"
    assert updated.cloud.providers["moonshot"].enabled is False


def test_discovery_swarm_ui_catchall_keys_survive_unrelated_patch() -> None:
    """discovery/swarm/ui top-level keys the patch doesn't mention at all
    stay untouched -- these are intentionally raw pass-through dicts for
    forward-compat, unlike the full-replace paths above."""
    cfg = NetllmConfig()
    cfg.swarm.cluster_token = "existing-token"
    patch = {"agent": {"advertise": False}}
    updated = apply_config_patch(cfg, patch)
    assert updated.swarm.cluster_token == "existing-token"
    assert updated.agent.advertise is False


# --- F-01: the merge must never silently drop a model field -----------------
#
# _merge_backends/_merge_policies rebuild each row rather than deep-merging it
# (rows are identified by base_url / position, not by key). Before this guard
# they hand-listed the fields to carry forward, so every field added to the
# model afterwards was reset to its default on any save that touched the
# section: BackendOverride.max_concurrency and .cloud_provider, and
# RoutingPolicy.source. The last one silently widened a source-scoped policy
# to every caller. These two tests fail the moment a new model field is added
# without teaching the merge about it.


def test_merged_backend_covers_every_model_field() -> None:
    cfg = NetllmConfig()
    merged = _merge_backends(cfg, [{"base_url": "http://x:1/v1"}])
    assert set(merged[0]) >= set(BackendOverride.model_fields), (
        "add the new BackendOverride field to _merge_backends (or to its "
        "excluded/preserve-only sets) — otherwise every save resets it"
    )


def test_merged_policy_covers_every_model_field() -> None:
    merged = _merge_policies([{"name": "p"}])
    assert set(merged[0]) >= set(RoutingPolicy.model_fields), (
        "add the new RoutingPolicy field to _merge_policies — otherwise "
        "every save resets it"
    )


def test_backend_api_key_preserved_when_patch_omits_it() -> None:
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(
            base_url="http://127.0.0.1:8000/v1",
            provider="vllm",
            api_key="stored-secret",
        )
    ]
    patch = {
        "routing": {
            "backends": [
                {
                    "base_url": "http://127.0.0.1:8000/v1",
                    "provider": "vllm",
                    "enabled": True,
                }
            ]
        }
    }
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.backends[0].api_key == "stored-secret"


def test_backend_api_key_replaced_when_patch_sets_it() -> None:
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(
            base_url="http://127.0.0.1:8000/v1",
            provider="vllm",
            api_key="old",
        )
    ]
    patch = {
        "routing": {
            "backends": [
                {
                    "base_url": "http://127.0.0.1:8000/v1",
                    "provider": "vllm",
                    "api_key": "new",
                }
            ]
        }
    }
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.backends[0].api_key == "new"


def test_backend_max_concurrency_and_cloud_provider_survive_unrelated_save() -> None:
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(
            base_url="http://x:1/v1",
            provider="vllm",
            max_concurrency=4,
            cloud_provider="openai",
        )
    ]
    patch = {"routing": {"backends": [{"base_url": "http://x:1/v1", "enabled": True}]}}
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.backends[0].max_concurrency == 4
    assert updated.routing.backends[0].cloud_provider == "openai"


def test_policy_source_scope_survives_unrelated_save() -> None:
    """A policy scoped to one routing.sources id must not silently become a
    global policy — dropping `source` widened 'buzz may reach cloud' into
    'everyone may reach cloud'."""
    cfg = NetllmConfig()
    cfg.routing.policies = [
        RoutingPolicy(name="p1", source="buzz", model_prefix="glm", allow_cloud=True)
    ]
    patch = {
        "routing": {
            "policies": [
                {
                    "name": "p1",
                    "model_prefix": "glm",
                    "allow_cloud": True,
                    "source": "buzz",
                }
            ]
        }
    }
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.policies[0].source == "buzz"


def test_backend_cloud_provider_is_not_client_settable() -> None:
    """cloud_provider is server-materialized (read_only in the schema): a
    patch echoing a different value must not retag the row."""
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(base_url="http://x:1/v1", cloud_provider="openai")
    ]
    patch = {
        "routing": {
            "backends": [
                {"base_url": "http://x:1/v1", "cloud_provider": "anthropic"},
            ]
        }
    }
    updated = apply_config_patch(cfg, patch)
    assert updated.routing.backends[0].cloud_provider == "openai"
