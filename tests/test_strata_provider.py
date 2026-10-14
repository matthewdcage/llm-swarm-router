"""Strata as a first-class local provider (Niko1221/Strata)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args

import pytest
from netllm_core.local_providers import LOCAL_PROVIDERS, api_key_env_for
from netllm_core.models import (
    Backend,
    BackendOverride,
    ProviderId,
    infer_api_format,
)
from netllm_core.platform import default_discovery_providers

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "strata"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_strata_fixture_contracts() -> None:
    loaded = _fixture("health-loaded.json")
    unloaded = _fixture("health-unloaded.json")
    assert loaded["loaded"] is True
    assert unloaded["loaded"] is False
    models = _fixture("models-unloaded.json")
    assert models["data"][0]["id"] == "strata"


def test_strata_is_a_provider_id() -> None:
    assert "strata" in get_args(ProviderId)
    assert "strata" in LOCAL_PROVIDERS


def test_backend_accepts_strata_as_openai_compatible() -> None:
    backend = Backend(id="st", base_url="http://127.0.0.1:8080/v1", provider="strata")
    assert backend.provider == "strata"
    assert backend.api_format == "openai"
    assert infer_api_format("strata") == "openai"
    assert (
        BackendOverride(base_url="http://127.0.0.1:8080/v1", provider="strata").provider
        == "strata"
    )


def test_strata_registry_defaults() -> None:
    spec = LOCAL_PROVIDERS["strata"]
    assert spec.default_ports == (8080,)
    assert spec.short_label == "Strata"
    assert api_key_env_for("strata") == "STRATA_API_KEY"
    assert "linux" in spec.platforms
    assert "win32" in spec.platforms
    assert "darwin" not in spec.platforms


def test_strata_discovered_by_default_on_linux_not_darwin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert "strata" in default_discovery_providers()
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "strata" not in default_discovery_providers()
