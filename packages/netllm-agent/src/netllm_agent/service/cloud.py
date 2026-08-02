"""Cloud provider rows and credentials — cluster ``cloud.py`` (plan §1).

Verbatim from the pre-split module, F-04 comments included: the legacy
env/header key rows are request-scoped and never pooled, while configured
``[cloud.providers.*]`` entries materialize into ephemeral pool rows.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx
from netllm_core.cloud_providers import get_provider_spec
from netllm_core.models import (
    ANTHROPIC_CLOUD_BASE_URL,
    OPENAI_CLOUD_BASE_URL,
    Backend,
    BackendHealth,
)
from netllm_core.source_identity import is_netllm_placeholder_key

__all__ = ["LEGACY_CLOUD_BACKEND_IDS", "CloudMixin"]


# Backend ids produced only by the legacy env/header cloud key path
# (_legacy_openai_cloud_backend / _legacy_anthropic_cloud_backend). These
# rows are request-scoped: they never enter the pool and their upstream
# clients are never cached, because their credential can come from the
# calling request (docs/architecture/07-findings-register.md F-04).
LEGACY_CLOUD_BACKEND_IDS = frozenset({"openai-cloud", "anthropic-cloud"})


class CloudMixin:
    """Cloud credentials, legacy request-scoped rows, provider materialization."""

    @staticmethod
    def _anthropic_api_key(headers: Mapping[str, str]) -> str:
        key = headers.get("x-api-key", "")
        if key and not is_netllm_placeholder_key(key):
            return key
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if env_key and not is_netllm_placeholder_key(env_key):
            return env_key
        return ""

    @staticmethod
    def _openai_api_key(headers: Mapping[str, str]) -> str:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token and not is_netllm_placeholder_key(token):
                return token
        env_key = os.environ.get("OPENAI_API_KEY", "")
        if env_key and not is_netllm_placeholder_key(env_key):
            return env_key
        return ""

    @staticmethod
    def _anthropic_default_headers(headers: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("anthropic-version", "anthropic-beta"):
            if key in headers:
                out[key] = headers[key]
        return out

    def _legacy_openai_cloud_backend(self, api_key: str) -> list[Backend]:
        """Request-scoped OpenAI cloud row for the legacy env/header key path.

        Deliberately NOT merged into the pool. These rows carry a credential
        that may have come from the calling request's Authorization header,
        and the pooled version deduped on backend *existence* rather than on
        the key — so the first caller to present a real key seeded a routable
        row that then served every later request from any client on the LAN,
        billed to that first caller (F-04). A request-scoped row is visible to
        selection for exactly one request and then discarded.

        Configured providers ([cloud.providers.*]) are unaffected: their keys
        are server-owned config, so they stay pooled via
        _materialize_cloud_provider_backends.
        """
        if not self.config.cloud.enabled or not api_key:
            return []
        cloud_url = OPENAI_CLOUD_BASE_URL.rstrip("/")
        if any(
            b.api_format == "openai" and b.base_url.rstrip("/") == cloud_url
            for b in self.pool.backends
        ):
            # A configured provider already covers this endpoint with a
            # server-owned key — prefer it over the caller's.
            return []
        return [
            Backend(
                id="openai-cloud",
                base_url=cloud_url,
                provider="openai",
                api_format="openai",
                api_key=api_key,
                enabled=True,
                local=False,
                agent_id=self.config.agent.agent_id,
                cloud_provider="openai",
            )
        ]

    def _legacy_anthropic_cloud_backend(self, api_key: str) -> list[Backend]:
        """Request-scoped Anthropic cloud row — see
        _legacy_openai_cloud_backend for why this is not pooled."""
        if not self.config.cloud.enabled:
            return []
        if not api_key or is_netllm_placeholder_key(api_key):
            return []
        if any(b.api_format == "anthropic" for b in self.pool.backends):
            return []
        return [
            Backend(
                id="anthropic-cloud",
                base_url=ANTHROPIC_CLOUD_BASE_URL,
                provider="anthropic",
                api_format="anthropic",
                api_key=api_key,
                enabled=True,
                local=False,
                agent_id=self.config.agent.agent_id,
                cloud_provider="anthropic",
            )
        ]

    def _materialize_cloud_provider_backends(self) -> None:
        """Sync [cloud.providers.*] into ephemeral pool rows.

        Additive to the legacy env-key injects above: a provider entry is
        only materialized when both the cloud master switch and the
        provider's own `enabled` flag are on. Disabling either prunes the
        row immediately (no restart needed) via prune_cloud_provider_rows.
        """
        cloud_cfg = self.config.cloud
        if not cloud_cfg.enabled:
            self.pool.prune_cloud_provider_rows(set())
            return
        new_backends: list[Backend] = []
        keep_ids: set[str] = set()
        for provider_id, provider_cfg in cloud_cfg.providers.items():
            if not provider_cfg.enabled:
                continue
            spec = get_provider_spec(provider_id)
            if spec is None:
                continue
            api_format = provider_cfg.api_format or spec.default_api_format
            endpoint = spec.endpoint(provider_cfg.region or None)
            base_url = provider_cfg.base_url or (
                endpoint.anthropic_base_url
                if api_format == "anthropic"
                else endpoint.openai_base_url
            )
            if not base_url:
                # Provider doesn't offer this api_format at this
                # region/profile — skip rather than materialize a dead row.
                continue
            api_key = (
                provider_cfg.api_key
                or (
                    os.environ.get(provider_cfg.api_key_env, "")
                    if provider_cfg.api_key_env
                    else ""
                )
                or os.environ.get(spec.api_key_env, "")
            )
            if not api_key and provider_cfg.auth == "plan_token":
                # Anthropic plan_token mode (`claude setup-token`): the
                # official env var is CLAUDE_CODE_OAUTH_TOKEN, not
                # ANTHROPIC_API_KEY. Unofficial for third-party routers —
                # documented by Anthropic for Claude Code CI only.
                api_key = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            if not api_key and provider_cfg.auth in ("api_key", "plan_token"):
                # Enabled but keyless: keep it out of the routable pool
                # rather than injecting a backend guaranteed to 401. The
                # CLI/admin surfaces (phase 2) will flag this state.
                continue
            backend_id = f"cloud-{provider_id}"
            keep_ids.add(backend_id)
            auth_mode = "bearer" if provider_cfg.auth == "plan_token" else "api_key"
            existing = self.pool.backend_by_id(backend_id)
            if (
                existing is not None
                and existing.base_url == base_url
                and existing.api_format == api_format
                and existing.api_key == api_key
                and existing.auth_mode == auth_mode
            ):
                # Already materialized and unchanged this session — skip
                # the rebuild so accumulated health/probe state (real
                # model catalog for providers with a live /models probe)
                # isn't wiped on every request.
                continue
            models = list(provider_cfg.models) or (
                [] if spec.models_endpoint else list(spec.static_models)
            )
            new_backends.append(
                Backend(
                    id=backend_id,
                    base_url=base_url,
                    provider="custom",
                    api_format=api_format,
                    api_key=api_key,
                    enabled=True,
                    local=False,
                    agent_id=self.config.agent.agent_id,
                    cloud_provider=provider_id,
                    auth_mode=auth_mode,
                    health=BackendHealth(
                        status="unknown",
                        models=models,
                        model_count=len(models),
                    ),
                )
            )
        self.pool.merge_backends(new_backends)
        # The legacy env/header key rows (ids in LEGACY_CLOUD_BACKEND_IDS) are
        # request-scoped now and never enter the pool, so nothing has to be
        # held back from pruning on their behalf: only enabled, configured
        # providers survive here.
        self.pool.prune_cloud_provider_rows(keep_ids)

    async def cloud_provider_models_probe(
        self, provider_id: str
    ) -> dict[str, Any] | None:
        """Full model catalog for one cloud provider, straight from the
        provider's API (GET /netllm/v1/cloud/providers/{id}/models).

        Deliberately ignores the `models` allowlist: the materialized
        backend's health.models IS the allowlist once one is set, so the
        allowlist-editing UI needs this separate probe to show what
        could be enabled. Providers without a live catalog endpoint (or
        an unreachable/keyless probe) fall back to the registry's
        static_models with source "static". Returns None for unknown
        provider ids.
        """
        spec = get_provider_spec(provider_id)
        if spec is None:
            return None
        provider_cfg = self.config.cloud.providers.get(provider_id)
        endpoint = spec.endpoint(provider_cfg.region or None if provider_cfg else None)
        api_key = ""
        if provider_cfg is not None:
            api_key = provider_cfg.api_key or (
                os.environ.get(provider_cfg.api_key_env, "")
                if provider_cfg.api_key_env
                else ""
            )
        api_key = api_key or os.environ.get(spec.api_key_env, "")

        def static_payload(status: str, detail: str | None = None) -> dict[str, Any]:
            return {
                "provider": provider_id,
                "source": "static",
                "status": status,
                "detail": detail,
                "models": list(spec.static_models),
                "configured": list(provider_cfg.models) if provider_cfg else [],
            }

        if not spec.models_endpoint:
            return static_payload("static_catalog")
        if not api_key:
            return static_payload("no_api_key", f"Set {spec.api_key_env} first")

        from netllm_core.health import status_from_exception, status_from_response

        try:
            async with httpx.AsyncClient() as client:
                if endpoint.openai_base_url:
                    resp = await client.get(
                        endpoint.openai_base_url.rstrip("/") + "/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=10.0,
                    )
                elif endpoint.anthropic_base_url:
                    # Anthropic-only providers list models at /v1/models
                    # with x-api-key auth (same {"data": [{"id": …}]}
                    # shape status_from_response parses).
                    resp = await client.get(
                        endpoint.anthropic_base_url.rstrip("/") + "/v1/models",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                        timeout=10.0,
                    )
                else:
                    return static_payload("no_endpoint")
            probe = status_from_response(resp)
        except Exception as exc:  # noqa: BLE001 — probe surface, never raise
            probe = status_from_exception(exc, 10.0)
        models = probe.get("models") or []
        if probe.get("status") != "online" or not models:
            return static_payload(
                str(probe.get("status", "error")), probe.get("detail")
            )
        return {
            "provider": provider_id,
            "source": "live",
            "status": "online",
            "detail": None,
            "models": models,
            "configured": list(provider_cfg.models) if provider_cfg else [],
        }
