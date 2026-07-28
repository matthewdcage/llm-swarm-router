"""Tests for config load/save."""

from __future__ import annotations

from pathlib import Path

from netllm_core.models import NetllmConfig, load_config, save_config


def test_save_and_load_config(tmp_path: Path) -> None:
    cfg = NetllmConfig()
    cfg.agent.listen = "127.0.0.1:11401"
    cfg.routing.default_strategy = "round_robin"
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.agent.listen == "127.0.0.1:11401"
    assert loaded.routing.default_strategy == "round_robin"


# --- F-05 / LAN defaults one-shot ------------------------------------------


def test_backend_override_max_concurrency_hot_applies_to_live_pool_row() -> None:
    """merge_backends updates existing local rows in place (in-flight requests
    hold the object reference), so a config-sourced knob that isn't copied
    there silently waits for a restart even though the save succeeded."""
    from netllm_core.models import Backend
    from netllm_core.pool import RouterPool

    pool = RouterPool()
    pool.set_backends(
        [Backend(id="b", base_url="http://x:1/v1", provider="vllm", max_concurrency=0)]
    )
    live = pool.backends[0]

    pool.merge_backends(
        [Backend(id="b", base_url="http://x:1/v1", provider="vllm", max_concurrency=4)]
    )

    assert pool.backends[0] is live, "row identity must survive the merge"
    assert live.max_concurrency == 4


def test_lan_mesh_defaults_do_not_re_enable_subnet_scan_after_first_apply() -> None:
    """The one-shot flag now covers subnet_scan too. It used to be re-forced
    on every call, so turning it off in macOS Settings on a LAN-bound agent
    was undone by the very save that turned it off."""
    from netllm_core.models import ensure_lan_mesh_defaults

    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"

    assert ensure_lan_mesh_defaults(cfg) is True
    assert cfg.swarm.subnet_scan is True
    assert cfg.routing.default_strategy == "local_spillover"

    cfg.swarm.subnet_scan = False
    assert ensure_lan_mesh_defaults(cfg) is False
    assert cfg.swarm.subnet_scan is False, "explicit user choice must stick"


def test_upstream_timeouts_are_configurable() -> None:
    """The 120 s read timeout was hardcoded in both SDK adapters, so a long
    generation on slow hardware was cut off with no way to raise it (F-20)."""
    cfg = NetllmConfig()
    assert cfg.routing.upstream_connect_timeout_s == 5.0
    assert cfg.routing.upstream_read_timeout_s == 120.0

    loaded = NetllmConfig.model_validate(
        {"routing": {"upstream_read_timeout_s": 600.0}}
    )
    assert loaded.routing.upstream_read_timeout_s == 600.0
