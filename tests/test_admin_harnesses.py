"""Coverage for admin.harness_registry_payload and GET /netllm/v1/harnesses."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from netllm_agent.admin import harness_registry_payload
from netllm_agent.app import create_app
from netllm_core.known_harnesses import KNOWN_HARNESSES
from netllm_core.models import NetllmConfig, SourceConfig

_ICONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "netllm-agent"
    / "src"
    / "netllm_agent"
    / "static"
    / "icons"
    / "harnesses"
)


def _cfg() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    return cfg


def test_registry_payload_empty_sources_all_unconfigured() -> None:
    cfg = _cfg()
    with patch("netllm_agent.admin.detect_harness", return_value=False):
        rows = harness_registry_payload(cfg)
    assert len(rows) == len(KNOWN_HARNESSES)
    assert {r["id"] for r in rows} == {h.id for h in KNOWN_HARNESSES}
    for row in rows:
        assert row["configured"] is False
        assert row["enabled"] is False
        assert row["detected"] is False
        assert row["icon_url"] == f"/ui/icons/harnesses/{row['id']}.svg"


def test_every_known_harness_has_an_icon_file_on_disk() -> None:
    """Regression guard: harness_registry_payload's icon_url is a fixed
    convention (/ui/icons/harnesses/<id>.svg), not a per-entry field -- a
    new KNOWN_HARNESSES entry with no matching file would silently 404 in
    the dashboard/macOS badge instead of failing a test."""
    for known in KNOWN_HARNESSES:
        icon_path = _ICONS_DIR / f"{known.id}.svg"
        assert icon_path.is_file(), f"missing icon for {known.id}: {icon_path}"


def test_registry_payload_reflects_configured_and_enabled_source() -> None:
    cfg = _cfg()
    cfg.routing.sources = [SourceConfig(id="my-codex", known_id="codex", enabled=True)]
    with patch("netllm_agent.admin.detect_harness", return_value=False):
        rows = harness_registry_payload(cfg)
    codex_row = next(r for r in rows if r["id"] == "codex")
    assert codex_row["configured"] is True
    assert codex_row["enabled"] is True


def test_detected_is_independent_of_configured_and_enabled() -> None:
    """Detection never influences enabled -- a source can be enabled on this
    agent while its CLI lives on a peer machine (netllm's swarm model)."""
    cfg = _cfg()
    cfg.routing.sources = [SourceConfig(id="codex", known_id="codex", enabled=True)]
    with patch("netllm_agent.admin.detect_harness", return_value=True):
        rows = harness_registry_payload(cfg)
    codex_row = next(r for r in rows if r["id"] == "codex")
    assert codex_row["enabled"] is True
    assert codex_row["detected"] is True

    with patch("netllm_agent.admin.detect_harness", return_value=False):
        rows = harness_registry_payload(cfg)
    codex_row = next(r for r in rows if r["id"] == "codex")
    assert codex_row["enabled"] is True  # unchanged by detection
    assert codex_row["detected"] is False


def test_harnesses_endpoint_serves_registry() -> None:
    cfg = _cfg()
    app = create_app(cfg)
    with patch("netllm_agent.admin.detect_harness", return_value=False):
        with TestClient(app) as client:
            resp = client.get("/netllm/v1/harnesses")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["harnesses"]}
    assert ids == {h.id for h in KNOWN_HARNESSES}


def test_harness_icons_served_from_static_mount() -> None:
    cfg = _cfg()
    app = create_app(cfg)
    with TestClient(app) as client:
        for known in KNOWN_HARNESSES:
            resp = client.get(f"/ui/icons/harnesses/{known.id}.svg")
            assert resp.status_code == 200, known.id
            assert "svg" in resp.headers.get("content-type", "")


def test_config_and_status_payloads_unaffected_by_this_endpoint() -> None:
    """GET /netllm/v1/harnesses is additive -- existing endpoint shapes must
    not change, so older dashboard/macOS builds keep working unmodified."""
    cfg = _cfg()
    app = create_app(cfg)
    with patch("netllm_agent.admin.detect_harness", return_value=False):
        with TestClient(app) as client:
            config_before = client.get("/netllm/v1/config").json()
            client.get("/netllm/v1/harnesses")
            config_after = client.get("/netllm/v1/config").json()
            status_resp = client.get("/netllm/v1/status")
    assert config_before == config_after
    assert status_resp.status_code == 200
    assert "harnesses" not in status_resp.json()
    assert "harnesses" not in config_after
    assert "routing" in config_after
    assert "sources" in config_after["routing"]
