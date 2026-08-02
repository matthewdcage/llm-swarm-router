"""Follow-up audit batch E regressions: F-43, F-44, F-46.

See docs/architecture/09-follow-up-audit-2026-07-31.md.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import netllm_cli.main as cli_main
import pytest
from netllm_core.models import (
    NetllmConfig,
    SourceConfig,
    is_lan_listen,
    load_config,
    save_config,
)
from typer.testing import CliRunner

runner = CliRunner()


def _cfg_path(tmp_path: Path, cfg: NetllmConfig | None = None) -> Path:
    path = tmp_path / "config.toml"
    save_config(cfg or NetllmConfig(), path)
    return path


# --- F-43: sources_toggle must not reach into netllm_agent / fastapi -------


def test_f43_sources_toggle_does_not_import_agent_or_fastapi() -> None:
    """The CLI config write path goes through netllm_core (config_merge +
    config_guards), like `netllm config import` — not through the agent's
    HTTP admin layer. Importing netllm_agent.admin drags FastAPI into a
    plain CLI command and re-breaks the layering the F-02 fix established."""
    src = inspect.getsource(cli_main.sources_toggle)
    assert "netllm_agent" not in src
    assert "fastapi" not in src


def test_f43_sources_toggle_round_trip_preserves_behavior(tmp_path: Path) -> None:
    """Toggle round-trip on a temp config: register, disable, re-enable —
    exact user-facing behavior (messages, exit codes) preserved."""
    cfg_path = _cfg_path(tmp_path)

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Enabled" in result.output and "codex" in result.output
    cfg = load_config(cfg_path)
    assert cfg.routing.sources[0].id == "codex"
    assert cfg.routing.sources[0].known_id == "codex"
    assert cfg.routing.sources[0].enabled is True

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Disabled" in result.output
    assert load_config(cfg_path).routing.sources[0].enabled is False

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Enabled" in result.output
    assert load_config(cfg_path).routing.sources[0].enabled is True


def test_f43_sources_toggle_guard_rejection_still_exits_1(tmp_path: Path) -> None:
    """The shared config_guards still gate the write: an elevated source
    without a secret on a LAN listen is rejected, exit code 1, disk untouched."""
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    assert is_lan_listen(cfg.agent.listen)
    cfg.routing.sources = [
        SourceConfig(id="codex", known_id="codex", allow_cloud=True, secret="")
    ]
    cfg_path = _cfg_path(tmp_path, cfg)

    result = runner.invoke(
        cli_main.app, ["sources", "toggle", "codex", "--config", str(cfg_path)]
    )
    assert result.exit_code == 1
    assert "Could not toggle source" in result.output
    assert load_config(cfg_path).routing.sources[0].enabled is True


# --- F-44: dead async probe_anthropic_compat (pre-F-07 billable probe) ------


def test_f44_dead_async_billable_anthropic_probe_removed() -> None:
    """The async twin of probe_anthropic_compat_sync had zero callers and
    still POSTed a billable 1-token Messages request against a hardcoded
    model id — the exact bug F-07 fixed in the sync path. It must be gone
    (or, if ever revived, mirror the free GET /v1/models-first sync logic)."""
    import netllm_core.health as health

    assert not hasattr(health, "probe_anthropic_compat"), (
        "async probe_anthropic_compat still exists; it embodies the pre-F-07 "
        "billable-probe bug"
    )
    src = Path(health.__file__).read_text()
    assert "async def probe_anthropic_compat" not in src


# --- F-46: cloud fallback direction UX trap ---------------------------------


def test_f46_local_first_alias_maps_to_stored_cloud(tmp_path: Path) -> None:
    """`local-first` says what the user means; it stores the compat value
    `cloud` (cloud is the fallback tier)."""
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "local-first", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(cfg_path).cloud.fallback == "cloud"


def test_f46_cloud_first_alias_maps_to_stored_local(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "cloud-first", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(cfg_path).cloud.fallback == "local"


def test_f46_fallback_local_prints_cloud_first_warning(tmp_path: Path) -> None:
    """fallback=local names the fallback *tier* but reads like the priority.
    Every change must print one explicit line stating the resulting order."""
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "local", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "cloud tried FIRST" in result.output
    assert "local mesh is the fallback" in result.output


def test_f46_fallback_cloud_prints_local_first_explanation(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "cloud", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "local mesh tried FIRST" in result.output
    assert "cloud is the fallback" in result.output


def test_f46_fallback_none_and_off_print_no_fallback_explanation(
    tmp_path: Path,
) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "none", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "no automatic cloud fallback" in result.output

    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "off", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "no automatic cloud fallback" in result.output


def test_f46_fallback_on_prints_resulting_order(tmp_path: Path) -> None:
    """Re-enabling the toggle must restate the direction currently stored."""
    cfg = NetllmConfig()
    cfg.cloud.fallback = "local"
    cfg.cloud.fallback_enabled = False
    cfg_path = _cfg_path(tmp_path, cfg)
    result = runner.invoke(
        cli_main.app, ["cloud", "fallback", "on", "--config", str(cfg_path)]
    )
    assert result.exit_code == 0, result.output
    assert "cloud tried FIRST" in result.output


def test_f46_stored_values_unchanged_for_plain_modes(tmp_path: Path) -> None:
    """Compat: the stored config values keep their existing names."""
    cfg_path = _cfg_path(tmp_path)
    for mode, stored in (("cloud", "cloud"), ("local", "local"), ("none", "none")):
        result = runner.invoke(
            cli_main.app, ["cloud", "fallback", mode, "--config", str(cfg_path)]
        )
        assert result.exit_code == 0, result.output
        assert load_config(cfg_path).cloud.fallback == stored


def test_f46_doctor_notes_cloud_first_when_fallback_is_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _one_online(cfg: NetllmConfig) -> list[dict[str, Any]]:
        return [{"name": "ollama", "status": "online", "models": ["m"]}]

    monkeypatch.setattr(cli_main, "scan_local_providers", _one_online)
    monkeypatch.setattr("netllm_discovery.runtime.check_listen_port", lambda _cfg: None)

    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.cloud.fallback = "local"
    cfg_path = _cfg_path(tmp_path, cfg)

    result = runner.invoke(
        cli_main.app, ["doctor", "--json", "--config", str(cfg_path)]
    )
    payload = json.loads(result.stdout)
    assert any("cloud" in n and "FIRST" in n for n in payload.get("notes", [])), payload

    # And no such note when the default local-first order is configured.
    cfg.cloud.fallback = "cloud"
    save_config(cfg, cfg_path)
    result = runner.invoke(
        cli_main.app, ["doctor", "--json", "--config", str(cfg_path)]
    )
    payload = json.loads(result.stdout)
    assert not any("FIRST" in n for n in payload.get("notes", []))
