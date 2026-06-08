"""Resolved vendor SDK versions for display and diagnostics."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any


def get_sdk_version(package: str) -> str | None:
    """Return installed package version, or None if not installed."""
    try:
        return pkg_version(package)
    except PackageNotFoundError:
        return None


def sdk_versions_payload() -> dict[str, Any]:
    """OpenAI/Anthropic SDK versions bundled with the running agent."""
    openai_ver = get_sdk_version("openai")
    anthropic_ver = get_sdk_version("anthropic")
    return {
        "openai": openai_ver,
        "anthropic": anthropic_ver,
    }
