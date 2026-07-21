"""Static registry of pre-configured cloud providers.

Code-owned reference data (base URLs, auth modes, default model catalogs) —
not user config. Facts sourced from each vendor's official docs as of
2026-07-22; see docs/cloud-providers-plan.md for citations and caveats.

Regenerating this data (new model IDs, new regions) is a code change, not a
config migration: nothing here is persisted into config.toml.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CloudProviderId = Literal["moonshot", "zai", "openai", "anthropic", "openrouter"]
AuthMode = Literal["api_key", "oauth_pkce", "plan_token"]


@dataclass(frozen=True)
class CloudEndpoint:
    """Base URLs offered by a provider for one region/profile."""

    openai_base_url: str | None = None
    anthropic_base_url: str | None = None


@dataclass(frozen=True)
class CloudProviderSpec:
    id: CloudProviderId
    display_name: str
    # region/profile key -> endpoint set. First key is the default.
    endpoints: dict[str, CloudEndpoint]
    auth_modes: tuple[AuthMode, ...]
    api_key_env: str
    default_api_format: Literal["openai", "anthropic"]
    models_endpoint: bool
    static_models: tuple[str, ...] = ()
    notes: str = ""

    def default_region(self) -> str:
        return next(iter(self.endpoints))

    def endpoint(self, region: str | None = None) -> CloudEndpoint:
        return self.endpoints.get(region or "", self.endpoints[self.default_region()])


CLOUD_PROVIDERS: dict[CloudProviderId, CloudProviderSpec] = {
    "moonshot": CloudProviderSpec(
        id="moonshot",
        display_name="Moonshot AI (Kimi)",
        endpoints={
            "global": CloudEndpoint(
                openai_base_url="https://api.moonshot.ai/v1",
                anthropic_base_url="https://api.moonshot.ai/anthropic",
            ),
            "cn": CloudEndpoint(
                openai_base_url="https://api.moonshot.cn/v1",
                anthropic_base_url="https://api.moonshot.cn/anthropic",
            ),
        },
        auth_modes=("api_key",),
        api_key_env="MOONSHOT_API_KEY",
        default_api_format="openai",
        models_endpoint=True,
        static_models=("kimi-k3", "kimi-k2.7-code", "kimi-k2.6"),
        notes=(
            "Pay-as-you-go API keys only; no OAuth/plan auth. "
            "kimi-k2-* previews, kimi-latest, and moonshot-v1-* are "
            "discontinued/sunsetting — prefer live GET /v1/models."
        ),
    ),
    "zai": CloudProviderSpec(
        id="zai",
        display_name="Z.ai (Zhipu GLM)",
        endpoints={
            "api": CloudEndpoint(
                openai_base_url="https://api.z.ai/api/paas/v4",
            ),
            "coding_plan": CloudEndpoint(
                openai_base_url="https://api.z.ai/api/coding/paas/v4",
                anthropic_base_url="https://api.z.ai/api/anthropic",
            ),
            "cn": CloudEndpoint(
                openai_base_url="https://open.bigmodel.cn/api/paas/v4",
                anthropic_base_url="https://open.bigmodel.cn/api/anthropic",
            ),
        },
        auth_modes=("api_key",),
        api_key_env="ZAI_API_KEY",
        default_api_format="openai",
        models_endpoint=False,
        static_models=("glm-5.2", "glm-5-turbo", "glm-5.1", "glm-4.7", "glm-5v-turbo"),
        notes=(
            "No GET /models endpoint — catalog is hardcoded. GLM Coding Plan "
            "keys are contractually restricted to an approved-tools list per "
            "Z.ai's usage policy; using them from a generic router may be "
            "outside that policy. The 'api' profile (pay-as-you-go) is safest "
            "for third-party tools."
        ),
    ),
    "openai": CloudProviderSpec(
        id="openai",
        display_name="OpenAI",
        endpoints={
            "global": CloudEndpoint(openai_base_url="https://api.openai.com/v1"),
        },
        auth_modes=("api_key",),
        api_key_env="OPENAI_API_KEY",
        default_api_format="openai",
        models_endpoint=True,
        static_models=("gpt-5.6", "gpt-5.3-codex"),
        notes=(
            "API key only. 'Sign in with ChatGPT' plan OAuth is documented "
            "only for OpenAI's own clients (Codex CLI, ChatGPT apps) — there "
            "is no public OAuth client for third-party tools."
        ),
    ),
    "anthropic": CloudProviderSpec(
        id="anthropic",
        display_name="Anthropic",
        endpoints={
            "global": CloudEndpoint(anthropic_base_url="https://api.anthropic.com"),
        },
        auth_modes=("api_key", "plan_token"),
        api_key_env="ANTHROPIC_API_KEY",
        default_api_format="anthropic",
        models_endpoint=True,
        static_models=("claude-opus-4-7", "claude-sonnet-4-6"),
        notes=(
            "Official third-party auth is a Console API key (x-api-key). "
            "The plan_token mode (Bearer token from `claude setup-token`) is "
            "documented by Anthropic only for Claude Code CI use, not general "
            "third-party API clients — treat as unofficial, opt-in only."
        ),
    ),
    "openrouter": CloudProviderSpec(
        id="openrouter",
        display_name="OpenRouter",
        endpoints={
            "global": CloudEndpoint(
                openai_base_url="https://openrouter.ai/api/v1",
                anthropic_base_url="https://openrouter.ai/api",
            ),
        },
        auth_modes=("api_key", "oauth_pkce"),
        api_key_env="OPENROUTER_API_KEY",
        default_api_format="openai",
        models_endpoint=True,
        static_models=(),
        notes=(
            "The only provider here with an officially sanctioned "
            "third-party OAuth flow (PKCE, localhost callbacks supported "
            "for CLI tools)."
        ),
    ),
}


def get_provider_spec(provider_id: str) -> CloudProviderSpec | None:
    return CLOUD_PROVIDERS.get(provider_id)  # type: ignore[arg-type]


def all_provider_ids() -> tuple[CloudProviderId, ...]:
    return tuple(CLOUD_PROVIDERS.keys())


__all__ = [
    "AuthMode",
    "CLOUD_PROVIDERS",
    "CloudEndpoint",
    "CloudProviderId",
    "CloudProviderSpec",
    "all_provider_ids",
    "get_provider_spec",
]
