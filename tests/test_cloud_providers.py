"""Tests for the cloud provider registry and [cloud] config contract."""

from __future__ import annotations

from pathlib import Path

from netllm_core.cloud_providers import (
    CLOUD_PROVIDERS,
    all_provider_ids,
    get_provider_spec,
)
from netllm_core.models import (
    Backend,
    BackendOverride,
    CloudConfig,
    CloudProviderConfig,
    NetllmConfig,
    load_config,
    save_config,
)


def test_registry_has_all_five_providers() -> None:
    ids = set(all_provider_ids())
    assert ids == {"moonshot", "zai", "openai", "anthropic", "openrouter"}
    for provider_id in ids:
        spec = get_provider_spec(provider_id)
        assert spec is not None
        assert spec.endpoints, f"{provider_id} has no endpoints"
        for endpoint in spec.endpoints.values():
            assert endpoint.openai_base_url or endpoint.anthropic_base_url
        assert spec.api_key_env
        assert "api_key" in spec.auth_modes


def test_get_provider_spec_unknown_returns_none() -> None:
    assert get_provider_spec("does-not-exist") is None


def test_openrouter_supports_oauth_pkce() -> None:
    spec = CLOUD_PROVIDERS["openrouter"]
    assert "oauth_pkce" in spec.auth_modes


def test_anthropic_supports_plan_token_opt_in() -> None:
    spec = CLOUD_PROVIDERS["anthropic"]
    assert "plan_token" in spec.auth_modes


def test_zai_has_no_models_endpoint_but_static_catalog() -> None:
    spec = CLOUD_PROVIDERS["zai"]
    assert spec.models_endpoint is False
    assert spec.static_models


def test_default_cloud_config_preserves_current_behavior() -> None:
    """Absent [cloud] section must not change runtime behavior:

    enabled=True (today's env-key-triggered inject still fires),
    fallback="cloud" (today's implicit local-then-cloud order),
    no providers pre-enabled.
    """
    cfg = NetllmConfig()
    assert cfg.cloud.enabled is True
    assert cfg.cloud.fallback == "cloud"
    assert cfg.cloud.fallback_enabled is True
    assert cfg.cloud.cloud_defaults_applied is False
    assert cfg.cloud.providers == {}


def test_cloud_config_round_trip(tmp_path: Path) -> None:
    cfg = NetllmConfig()
    cfg.cloud.enabled = True
    cfg.cloud.fallback = "local"
    cfg.cloud.providers["moonshot"] = CloudProviderConfig(
        enabled=True, region="global", api_format="openai"
    )
    cfg.cloud.providers["openrouter"] = CloudProviderConfig(
        enabled=True, auth="oauth_pkce", api_key="sk-or-test"
    )
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(path)

    assert loaded.cloud.fallback == "local"
    assert loaded.cloud.providers["moonshot"].enabled is True
    assert loaded.cloud.providers["moonshot"].region == "global"
    assert loaded.cloud.providers["openrouter"].auth == "oauth_pkce"
    assert loaded.cloud.providers["openrouter"].api_key == "sk-or-test"


def test_old_config_without_cloud_section_loads_unchanged(tmp_path: Path) -> None:
    """A config.toml written by a pre-cloud-feature release must load with
    identical routing/discovery/swarm/ui values and cloud defaults."""
    path = tmp_path / "config.toml"
    path.write_text(
        """
[agent]
listen = "127.0.0.1:11400"
role = "peer"

[routing]
default_strategy = "local_spillover"
allow_remote = true

[[routing.backends]]
base_url = "https://api.anthropic.com"
provider = "anthropic"
api_format = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"
enabled = true
local = false
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.agent.listen == "127.0.0.1:11400"
    assert cfg.routing.default_strategy == "local_spillover"
    assert len(cfg.routing.backends) == 1
    assert cfg.routing.backends[0].cloud_provider == ""
    # New [cloud] section defaults in cleanly.
    assert cfg.cloud.enabled is True
    assert cfg.cloud.providers == {}


def test_backend_override_cloud_provider_tag_defaults_empty() -> None:
    override = BackendOverride(base_url="http://127.0.0.1:1234/v1")
    assert override.cloud_provider == ""


def test_backend_resolve_api_key_uses_cloud_provider_registry_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-test-123")
    backend = Backend(
        id="moonshot-cloud",
        base_url="https://api.moonshot.ai/v1",
        provider="custom",
        cloud_provider="moonshot",
        local=False,
    )
    assert backend.resolve_api_key() == "mk-test-123"


def test_backend_inline_api_key_wins_over_cloud_provider_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "mk-env")
    backend = Backend(
        id="moonshot-cloud",
        base_url="https://api.moonshot.ai/v1",
        cloud_provider="moonshot",
        api_key="mk-inline",
        local=False,
    )
    assert backend.resolve_api_key() == "mk-inline"


def test_cloud_provider_config_defaults_disabled() -> None:
    provider_cfg = CloudProviderConfig()
    assert provider_cfg.enabled is False
    assert provider_cfg.auth == "api_key"


def test_cloud_config_provider_helper_returns_default_when_missing() -> None:
    cloud_cfg = CloudConfig()
    provider_cfg = cloud_cfg.provider("zai")
    assert provider_cfg.enabled is False
