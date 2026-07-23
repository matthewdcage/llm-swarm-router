"""CLI/harness source identity — resolution precedence, hot-apply, and the
elevated-capability secret gate (docs/cli-source-routing-plan.md Phase 1)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.service import AgentService
from netllm_core.models import NetllmConfig, SourceConfig, SourceMatch, save_config
from netllm_core.source_identity import resolve_source


def _cfg_with_sources(*sources: SourceConfig) -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.routing.sources = list(sources)
    return cfg


def test_header_wins_over_key_and_user_agent() -> None:
    sources = [
        SourceConfig(id="claude-code"),
        SourceConfig(id="codex", match=SourceMatch(user_agent_contains=["codex-cli"])),
    ]
    headers = {
        "x-netllm-source": "claude-code",
        "authorization": "Bearer netllm-codex",
        "user-agent": "codex-cli/1.0",
    }
    resolved = resolve_source(headers=headers, sources=sources)
    assert resolved.id == "claude-code"
    assert resolved.resolved_via == "header"


def test_unknown_header_falls_through_to_key() -> None:
    sources = [SourceConfig(id="codex")]
    headers = {
        "x-netllm-source": "not-registered",
        "authorization": "Bearer netllm-codex",
    }
    resolved = resolve_source(headers=headers, sources=sources)
    assert resolved.id == "codex"
    assert resolved.resolved_via == "key"


def test_key_wins_over_user_agent() -> None:
    sources = [
        SourceConfig(id="codex"),
        SourceConfig(id="cursor", match=SourceMatch(user_agent_contains=["cursor"])),
    ]
    headers = {
        "authorization": "Bearer netllm-codex",
        "user-agent": "cursor/2.0",
    }
    resolved = resolve_source(headers=headers, sources=sources)
    assert resolved.id == "codex"
    assert resolved.resolved_via == "key"


def test_user_agent_heuristic_fallback() -> None:
    match = SourceMatch(user_agent_contains=["Cursor"])
    sources = [SourceConfig(id="cursor", match=match)]
    headers = {"user-agent": "Cursor/1.2.3 (Macintosh)"}
    resolved = resolve_source(headers=headers, sources=sources)
    assert resolved.id == "cursor"
    assert resolved.resolved_via == "user_agent"


def test_netllm_local_always_resolves_to_default() -> None:
    sources = [SourceConfig(id="claude-code")]
    headers = {"authorization": "Bearer netllm-local"}
    resolved = resolve_source(headers=headers, sources=sources)
    assert resolved.id == "default"
    assert resolved.resolved_via == "default"


def test_no_signal_resolves_to_default() -> None:
    sources = [SourceConfig(id="claude-code")]
    resolved = resolve_source(headers={}, sources=sources)
    assert resolved.id == "default"


def test_disabled_source_is_not_matched() -> None:
    sources = [SourceConfig(id="claude-code", enabled=False)]
    headers = {"x-netllm-source": "claude-code"}
    resolved = resolve_source(headers=headers, sources=sources)
    assert resolved.id == "default"


def test_secret_required_when_configured() -> None:
    """Once a source has a secret, no bare signal (header, key without
    the secret, or User-Agent) can claim it -- only a key carrying the
    correct secret does. This is what makes the elevated-capability
    config gate (see test_elevated_source_*) actually mean something:
    without it, an unauthenticated caller could win an elevated source's
    identity just by sending its id."""
    sources = [
        SourceConfig(
            id="buzz", secret="s3cret", match=SourceMatch(user_agent_contains=["buzz"])
        )
    ]

    # Bare key (no secret part): falls back to default, not "buzz".
    resolved = resolve_source(
        headers={"authorization": "Bearer netllm-buzz"}, sources=sources
    )
    assert resolved.id == "default"

    # Header alone: also insufficient.
    resolved = resolve_source(headers={"x-netllm-source": "buzz"}, sources=sources)
    assert resolved.id == "default"

    # User-Agent heuristic: also insufficient for a secret-protected source.
    resolved = resolve_source(headers={"user-agent": "buzz/1.0"}, sources=sources)
    assert resolved.id == "default"

    # Correct secret: attributed and authenticated.
    resolved = resolve_source(
        headers={"authorization": "Bearer netllm-buzz.s3cret"}, sources=sources
    )
    assert resolved.id == "buzz"
    assert resolved.authenticated is True


def test_secret_mismatch_falls_back_to_default_not_rejected() -> None:
    """Attributive by default: a wrong secret never 401s — it just fails
    to grant that identity. See SourceConfig docstring."""
    sources = [SourceConfig(id="buzz", secret="s3cret")]
    resolved = resolve_source(
        headers={"authorization": "Bearer netllm-buzz.wrong"}, sources=sources
    )
    assert resolved.id == "default"
    assert resolved.authenticated is False


def test_both_surfaces_attribute_identically() -> None:
    """Identity resolution takes only headers, so the OpenAI and
    Anthropic proxy paths (which each lower-case + pass their own
    headers dict) attribute the same caller the same way."""
    sources = [SourceConfig(id="claude-code")]
    openai_headers = {"authorization": "Bearer netllm-claude-code"}
    anthropic_headers = {"x-api-key": "netllm-claude-code"}
    assert (
        resolve_source(headers=openai_headers, sources=sources).id
        == resolve_source(headers=anthropic_headers, sources=sources).id
        == "claude-code"
    )


def test_service_attribute_source_counts_and_hot_applies() -> None:
    cfg = NetllmConfig()
    service = AgentService(cfg)

    resolved = service._attribute_source({"authorization": "Bearer netllm-local"})
    assert resolved.id == "default"
    assert service._source_counts == {"default": 1}

    merged = NetllmConfig()
    merged.routing.sources = [SourceConfig(id="buzz")]
    service.apply_config(merged)

    resolved = service._attribute_source({"authorization": "Bearer netllm-buzz"})
    assert resolved.id == "buzz"
    assert service._source_counts == {"default": 1, "buzz": 1}
    assert service.status_payload()["source_requests"] == {"default": 1, "buzz": 1}


def test_elevated_source_requires_secret_on_lan_bind() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.agent.listen = "0.0.0.0:11400"
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [
                            {"id": "buzz", "allow_cloud": True},
                        ]
                    }
                },
            )
            assert resp.status_code == 400
            assert "buzz" in resp.json()["detail"]


def test_elevated_source_accepted_with_secret_on_lan_bind() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.agent.listen = "0.0.0.0:11400"
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [
                            {"id": "buzz", "allow_cloud": True, "secret": "s3cret"},
                        ]
                    }
                },
            )
            assert resp.status_code == 200


def test_elevated_source_accepted_on_loopback_without_secret() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        assert cfg.agent.listen == "127.0.0.1:11400"
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [
                            {"id": "buzz", "allow_cloud": True},
                        ]
                    }
                },
            )
            assert resp.status_code == 200


def test_source_secret_is_write_only_on_patch_round_trip() -> None:
    """Omitting `secret` on a later patch must not blank out a
    previously stored one (same write-only convention as backends/cloud
    provider keys)."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        cfg.agent.listen = "0.0.0.0:11400"
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [
                            {
                                "id": "buzz",
                                "allow_cloud": True,
                                "secret": "s3cret",
                            }
                        ]
                    }
                },
            )
            assert resp.status_code == 200

            # Second patch updates description only; secret must survive.
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [
                            {
                                "id": "buzz",
                                "allow_cloud": True,
                                "description": "buzz-agent fleet",
                            }
                        ]
                    }
                },
            )
            assert resp.status_code == 200
            from netllm_core.models import load_config

            reloaded = load_config(cfg_path)
            assert reloaded.routing.sources[0].secret == "s3cret"
            assert reloaded.routing.sources[0].description == "buzz-agent fleet"


def test_source_scenarios_and_prefer_provider_persist_on_save() -> None:
    """Regression: the per-field copy-over list in apply_config_patch's
    sources merge silently dropped `scenarios` and `prefer_provider` --
    editing either via the dashboard would appear to work in the browser
    draft but never actually persist. See docs/cli-source-routing-plan.md
    Phase 4b."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [
                            {
                                "id": "buzz",
                                "prefer_provider": "ollama",
                                "scenarios": {
                                    "background": {"model": "qwen3:4b"}
                                },
                            }
                        ]
                    }
                },
            )
            assert resp.status_code == 200
            from netllm_core.models import load_config

            reloaded = load_config(cfg_path)
            source = reloaded.routing.sources[0]
            assert source.prefer_provider == "ollama"
            assert source.scenarios["background"].model == "qwen3:4b"

            # A second, unrelated save must not drop either.
            resp = client.post(
                "/netllm/v1/admin/config",
                json={
                    "routing": {
                        "sources": [{"id": "buzz", "description": "updated"}]
                    }
                },
            )
            assert resp.status_code == 200
            reloaded = load_config(cfg_path)
            source = reloaded.routing.sources[0]
            assert source.prefer_provider == "ollama"
            assert source.scenarios["background"].model == "qwen3:4b"
            assert source.description == "updated"
