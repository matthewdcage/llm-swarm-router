"""`discovery.ignored_urls` — the backend denylist.

The defect it closes, from a real machine: vLLM's default ports are
`(8000, 8001)`. An unrelated service was listening on `127.0.0.1:8000` and
answering `401`, which `health.probe_openai_compat` correctly classifies as
*reachable*, so every scan registered it and the Backends page showed a
permanent "needs a key" card for something that was never a netllm backend.

The only prior way to silence it was to pin it as a `[[routing.backends]]`
override and disable that — converting a *discovered* endpoint into a
hand-authored config row, which `docs/ui-redesign-feature-spec.md` §5 lists as
a design error for exactly this reason.

The precedence rule these tests pin: **an explicit `[[routing.backends]]` row
wins.** An ignore entry naming a configured backend is stored but inert, and
never removes a backend the user configured on purpose.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.backend_credentials import (
    add_ignored_url,
    ignored_url_conflicts,
    ignored_url_keys,
    is_url_ignored,
    remove_ignored_url,
)
from netllm_core.models import BackendOverride, NetllmConfig, save_config
from netllm_discovery.local import candidate_urls_for_provider, scan_local_providers

#: vLLM's first default port — the one the squatter was on.
SQUATTER = "http://127.0.0.1:8000/v1"
#: The user's real, configured backend. It must never disappear.
CONFIGURED = "http://127.0.0.1:8013/v1"


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """:8000 answers 401 (reachable, no key); :8013 serves a model.

    Both stay up for the whole test: an ignored endpoint must vanish because
    the config says so, not because the socket closed.
    """

    async def fake_probe(
        base_url: str,
        client: httpx.AsyncClient,
        *,
        api_key: str | None = None,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        url = base_url.rstrip("/")
        if url == SQUATTER.rstrip("/"):
            # health.py: 401/403 is "online" — reachable but unauthenticated.
            return {
                "status": "online",
                "http_status": 401,
                "model_count": 0,
                "models": [],
                "detail": "Unauthorized",
            }
        if url == CONFIGURED.rstrip("/"):
            return {
                "status": "online",
                "http_status": 200,
                "model_count": 1,
                "models": ["qwen3-coder"],
            }
        return {
            "status": "offline",
            "http_status": None,
            "model_count": 0,
            "models": [],
        }

    monkeypatch.setattr("netllm_discovery.local.probe_openai_compat", fake_probe)
    yield


def _config() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.discovery.providers = ["vllm"]
    cfg.routing.backends = [
        BackendOverride(base_url=CONFIGURED, provider="vllm", enabled=True, local=True)
    ]
    return cfg


def _status_urls(client: TestClient) -> list[str]:
    body = client.get("/netllm/v1/status?scan=1").json()
    return sorted(b["base_url"] for b in body.get("backends", []))


# --- the end-to-end claim -------------------------------------------------


def test_ignoring_a_401_default_port_removes_it_from_status(
    tmp_path: Path, probes: None
) -> None:
    """Before: the squatter is a backend. After: it is not, and :8013 stays."""
    cfg_path = tmp_path / "config.toml"
    cfg = _config()
    save_config(cfg, cfg_path)

    with TestClient(create_app(cfg, config_path=cfg_path)) as client:
        assert _status_urls(client) == sorted([SQUATTER, CONFIGURED])

        resp = client.post(
            "/netllm/v1/admin/config",
            # The un-normalised spelling a user would paste from a browser.
            json={"discovery": {"ignored_urls": ["http://127.0.0.1:8000"]}},
        )
        assert resp.status_code == 200, resp.text

        client.post("/netllm/v1/admin/discover")
        assert _status_urls(client) == [CONFIGURED]


def test_ignoring_never_touches_the_other_discovery_config(
    tmp_path: Path, probes: None
) -> None:
    """`routing.backends` and `discovery.provider_urls` are left alone.

    Ignoring must be one reversible line, not a rewrite of the endpoint's
    identity somewhere else — that is the whole complaint against the
    pin-then-disable workaround.
    """
    cfg_path = tmp_path / "config.toml"
    cfg = _config()
    cfg.discovery.provider_urls = {"vllm": [SQUATTER, CONFIGURED]}
    save_config(cfg, cfg_path)

    with TestClient(create_app(cfg, config_path=cfg_path)) as client:
        resp = client.post(
            "/netllm/v1/admin/config",
            json={"discovery": {"ignored_urls": [SQUATTER]}},
        )
        assert resp.status_code == 200, resp.text
        client.post("/netllm/v1/admin/discover")
        assert _status_urls(client) == [CONFIGURED]

    from netllm_core.models import load_config

    saved = load_config(cfg_path)
    assert saved.discovery.provider_urls["vllm"] == [SQUATTER, CONFIGURED]
    assert [b.base_url for b in saved.routing.backends] == [CONFIGURED]
    assert saved.discovery.ignored_urls == [SQUATTER]

    # And removing the entry brings it straight back — no other edit needed.
    saved.discovery.ignored_urls = []
    save_config(saved, cfg_path)
    with TestClient(create_app(saved, config_path=cfg_path)) as client:
        assert _status_urls(client) == sorted([SQUATTER, CONFIGURED])


def test_an_explicitly_configured_backend_survives_being_ignored(
    tmp_path: Path, probes: None
) -> None:
    """The user's :8013 must never disappear because of a denylist entry.

    Silently deleting a backend the user configured by hand would be a
    data-loss-grade surprise; the explicit row wins and the save says so.
    """
    cfg_path = tmp_path / "config.toml"
    cfg = _config()
    save_config(cfg, cfg_path)

    with TestClient(create_app(cfg, config_path=cfg_path)) as client:
        resp = client.post(
            "/netllm/v1/admin/config",
            json={"discovery": {"ignored_urls": ["http://127.0.0.1:8013"]}},
        )
        assert resp.status_code == 200, resp.text
        # Saved, but reported as overruled rather than applied.
        warnings = " ".join(resp.json().get("warnings", []))
        assert CONFIGURED in warnings and "explicit backend wins" in warnings

        client.post("/netllm/v1/admin/discover")
        assert CONFIGURED in _status_urls(client)


# --- normalisation --------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    ["http://127.0.0.1:8000", "http://127.0.0.1:8000/", "http://127.0.0.1:8000/v1"],
)
def test_all_three_url_spellings_are_one_entry(spelling: str) -> None:
    """The three forms a human types must match the one canonical key."""
    cfg = NetllmConfig()
    cfg.discovery.ignored_urls = [spelling]
    assert ignored_url_keys(cfg) == {SQUATTER}
    for other in (
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:8000/v1",
    ):
        assert is_url_ignored(cfg, other), f"{spelling!r} did not match {other!r}"
    # Not a different port, and not a different host.
    assert not is_url_ignored(cfg, "http://127.0.0.1:8001/v1")
    assert not is_url_ignored(cfg, "http://localhost:8000/v1")


def test_add_and_remove_are_normalising_and_idempotent() -> None:
    cfg = NetllmConfig()
    assert add_ignored_url(cfg, "http://127.0.0.1:8000/")
    assert cfg.discovery.ignored_urls == [SQUATTER]
    # A second spelling of the same endpoint is not a second entry.
    assert not add_ignored_url(cfg, "http://127.0.0.1:8000/v1")
    assert cfg.discovery.ignored_urls == [SQUATTER]
    assert remove_ignored_url(cfg, "http://127.0.0.1:8000")
    assert cfg.discovery.ignored_urls == []
    assert not remove_ignored_url(cfg, SQUATTER)
    # Junk is rejected rather than stored as an entry that matches nothing.
    assert not add_ignored_url(cfg, "   ")


# --- the precedence rule, at the unit level -------------------------------


def test_an_ignore_entry_naming_a_pinned_backend_is_inert() -> None:
    cfg = _config()
    cfg.discovery.ignored_urls = [CONFIGURED, SQUATTER]
    assert ignored_url_keys(cfg) == {SQUATTER}
    assert not is_url_ignored(cfg, CONFIGURED)
    assert ignored_url_conflicts(cfg) == [CONFIGURED]


def test_a_disabled_override_still_wins_the_conflict() -> None:
    """`enabled = false` is still an explicit statement about that URL.

    Treating a switched-off override as "not configured" would let an ignore
    entry change what re-enabling it means, days later and invisibly.
    """
    cfg = _config()
    cfg.routing.backends[0].enabled = False
    cfg.discovery.ignored_urls = [CONFIGURED]
    assert ignored_url_keys(cfg) == set()
    assert ignored_url_conflicts(cfg) == [CONFIGURED]


# --- the scanner ----------------------------------------------------------


def test_ignored_urls_are_dropped_from_the_candidate_list() -> None:
    """Filtered whichever rung they entered on: pin, env hint or port scan."""
    cfg = NetllmConfig()
    cfg.discovery.provider_urls = {"vllm": [SQUATTER]}
    assert SQUATTER in candidate_urls_for_provider("vllm", cfg)
    cfg.discovery.ignored_urls = [SQUATTER]
    candidates = candidate_urls_for_provider("vllm", cfg)
    assert SQUATTER not in candidates
    # The other default port is untouched.
    assert "http://127.0.0.1:8001/v1" in candidates


@pytest.mark.asyncio
async def test_an_ignored_custom_endpoint_is_never_probed(probes: None) -> None:
    cfg = NetllmConfig()
    cfg.discovery.providers = []
    cfg.discovery.custom_endpoints = [SQUATTER, CONFIGURED]
    results = await scan_local_providers(cfg)
    assert sorted(r["base_url"] for r in results) == sorted([SQUATTER, CONFIGURED])

    cfg.discovery.ignored_urls = ["http://127.0.0.1:8000"]
    results = await scan_local_providers(cfg)
    assert [r["base_url"] for r in results] == [CONFIGURED]


# --- the CLI --------------------------------------------------------------


def test_cli_ignore_add_list_remove_round_trip(tmp_path: Path) -> None:
    from netllm_cli.main import app
    from netllm_core.models import load_config
    from typer.testing import CliRunner

    cfg_path = tmp_path / "config.toml"
    save_config(_config(), cfg_path)
    runner = CliRunner()

    result = runner.invoke(
        app, ["ignore", "add", "http://127.0.0.1:8000", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(cfg_path).discovery.ignored_urls == [SQUATTER]

    result = runner.invoke(app, ["ignore", "list", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "8000" in result.output

    # A conflict is reported, not silently applied.
    result = runner.invoke(
        app, ["ignore", "add", CONFIGURED, "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "routing.backends" in result.output

    result = runner.invoke(
        app, ["ignore", "remove", "http://127.0.0.1:8000/", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(cfg_path).discovery.ignored_urls == [CONFIGURED]

    # Removing something that was never ignored fails loudly.
    result = runner.invoke(
        app, ["ignore", "remove", "http://127.0.0.1:9999", "--config", str(cfg_path)]
    )
    assert result.exit_code == 1


# --- the surfaces ---------------------------------------------------------


def test_config_summary_exports_the_ignore_list() -> None:
    """The dashboard renders `discovery` from this export; an omitted field
    renders against `undefined` and is POSTed back as such on save."""
    from netllm_agent.admin import config_summary

    cfg = NetllmConfig()
    cfg.discovery.ignored_urls = [SQUATTER]
    assert config_summary(cfg)["discovery"]["ignored_urls"] == [SQUATTER]


def test_both_editing_surfaces_carry_the_ignore_list() -> None:
    """Belt and braces beside the Axis D parity kit, which fails by name.

    Named here as well because this field's whole point is being editable
    *without* hand-editing TOML: a removable control on both surfaces is the
    feature, not an incidental parity obligation.
    """
    repo = Path(__file__).resolve().parents[1]
    web = (
        repo / "packages/netllm-agent/src/netllm_agent/static/pages/network.js"
    ).read_text(encoding="utf-8")
    swift = (
        repo / "apps/netllm-mac/Sources/AppView/SettingsWindowView.swift"
    ).read_text(encoding="utf-8")
    backends = (
        repo / "packages/netllm-agent/src/netllm_agent/static/pages/backends.js"
    ).read_text(encoding="utf-8")
    assert "networkIgnoredEndpointsSection" in web
    # The dashboard binds the draft by property access, Swift by quoted key —
    # the two shapes `kit_config_surfaces._names` accepts as evidence.
    assert ".ignored_urls" in web
    assert '"ignored_urls"' in swift
    assert "backendIgnoreButton" in backends
