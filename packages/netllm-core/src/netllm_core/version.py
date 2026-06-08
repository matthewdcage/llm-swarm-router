"""Single source of truth for the netllm package version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as pkg_version

_FALLBACK_VERSION = "0.2.3.2"


def get_version() -> str:
    """Return installed netllm meta-package version."""
    try:
        return pkg_version("netllm")
    except PackageNotFoundError:
        try:
            return pkg_version("netllm-core")
        except PackageNotFoundError:
            return _FALLBACK_VERSION
