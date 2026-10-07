"""FreeToken as a first-class local provider.

Regression for a crash loop: a config naming ``provider = "freetoken"`` (a
backend row) and ``prefer_provider = "freetoken"`` (routing sources) was
written while support for the id existed only as an uncommitted local edit.
When that edit was lost, the agent failed config validation on every start
("Input should be 'omlx', 'ollama', ..."). These tests pin the id so a
config like that keeps loading.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest
from netllm_core.local_providers import LOCAL_PROVIDERS, api_key_env_for
from netllm_core.models import (
    Backend,
    BackendHealth,
    BackendOverride,
    NetllmConfig,
    ProviderId,
    RoutingPolicy,
    SourceConfig,
    infer_api_format,
    load_config,
)
from netllm_core.platform import default_discovery_providers
from netllm_core.pool import RouterPool

_MOCK_ONLINE = {"status": "online", "models": ["m"], "model_count": 1}

# Shape of a real operator config: a FreeToken backend plus routing sources
# that prefer it. No secrets; ids are illustrative.
_FREETOKEN_CONFIG = """
[discovery]
providers = ["ollama", "vllm", "freetoken"]

[[routing.backends]]
base_url = "http://127.0.0.1:1919/v1"
provider = "freetoken"
enabled = true
local = true
max_concurrency = 32

[[routing.backends]]
base_url = "http://127.0.0.1:8015/v1"
provider = "vllm"
enabled = false
local = true

[[routing.policies]]
name = "prefer-freetoken"
prefer_provider = "freetoken"

[[routing.sources]]
id = "deepseek"
prefer_provider = "freetoken"

[[routing.sources]]
id = "honcho"
local_only = true
prefer_provider = "freetoken"

[[routing.sources]]
id = "buzz"
prefer_provider = "freetoken"
"""


def test_freetoken_is_a_provider_id() -> None:
    assert "freetoken" in get_args(ProviderId)
    assert "freetoken" in LOCAL_PROVIDERS


def test_backend_accepts_freetoken_as_openai_compatible() -> None:
    backend = Backend(
        id="ft", base_url="http://127.0.0.1:1919/v1", provider="freetoken"
    )
    assert backend.provider == "freetoken"
    assert backend.api_format == "openai"
    assert infer_api_format("freetoken") == "openai"
    assert (
        BackendOverride(
            base_url="http://127.0.0.1:1919/v1", provider="freetoken"
        ).provider
        == "freetoken"
    )


def test_prefer_provider_accepts_freetoken() -> None:
    assert SourceConfig(id="s", prefer_provider="freetoken").prefer_provider == (
        "freetoken"
    )
    assert RoutingPolicy(name="p", prefer_provider="freetoken").prefer_provider == (
        "freetoken"
    )


def test_operator_config_with_freetoken_loads(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(_FREETOKEN_CONFIG, encoding="utf-8")
    cfg = load_config(path)
    assert isinstance(cfg, NetllmConfig)
    backends = {b.base_url: b for b in cfg.routing.backends}
    assert backends["http://127.0.0.1:1919/v1"].provider == "freetoken"
    assert [s.prefer_provider for s in cfg.routing.sources] == ["freetoken"] * 3
    assert cfg.routing.policies[0].prefer_provider == "freetoken"
    assert "freetoken" in cfg.discovery.providers


def test_freetoken_registry_defaults() -> None:
    spec = LOCAL_PROVIDERS["freetoken"]
    assert spec.default_ports == (1919,)
    # The daemon/control API on 1900 is not an inference surface.
    assert 1900 not in spec.default_ports
    assert spec.short_label == "FreeToken"
    assert api_key_env_for("freetoken") == "FREETOKEN_API_KEY"


def test_freetoken_discovered_by_default_on_linux_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert "freetoken" in default_discovery_providers()
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "freetoken" not in default_discovery_providers()


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_prefer_provider_freetoken_selects_freetoken_backend(
    mock_probe: object,
) -> None:
    pool = RouterPool()
    pool.merge_backends(
        [
            Backend(
                id="1",
                base_url="http://127.0.0.1:8000/v1",
                provider="vllm",
                health=BackendHealth(status="online", models=["m"]),
            ),
            Backend(
                id="2",
                base_url="http://127.0.0.1:1919/v1",
                provider="freetoken",
                health=BackendHealth(status="online", models=["m"]),
            ),
        ]
    )
    selected = pool.select_backend("m", "local_first", prefer_provider="freetoken")
    assert selected is not None
    assert selected.provider == "freetoken"
