"""Tests for agent singleton lock."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from netllm_core.models import NetllmConfig
from netllm_discovery import agent_lock as lock_mod
from netllm_discovery.agent_lock import (
    AgentLock,
    AlreadyRunning,
    acquire_agent_lock,
    agent_lock_path,
    read_lock_info,
)


@pytest.fixture
def lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state" / "netllm"
    state.mkdir(parents=True)
    logs = state / "logs"
    logs.mkdir()

    cfg = NetllmConfig()
    monkeypatch.setattr(cfg, "resolved_log_dir", lambda: logs)
    monkeypatch.setattr(
        lock_mod,
        "agent_lock_path",
        lambda _config: state / "agent.lock",
    )
    yield state
    if lock_mod._LOCK is not None and not lock_mod._LOCK._released:
        lock_mod._LOCK.release()
    lock_mod._LOCK = None


def test_agent_lock_path_under_state_dir(lock_dir: Path) -> None:
    cfg = NetllmConfig()
    with patch.object(cfg, "resolved_log_dir", return_value=lock_dir / "logs"):
        assert agent_lock_path(cfg) == lock_dir / "agent.lock"


def test_acquire_lock_free(lock_dir: Path) -> None:
    cfg = NetllmConfig()
    result = acquire_agent_lock(cfg)
    assert isinstance(result, AgentLock)
    assert result.path == lock_dir / "agent.lock"
    info = read_lock_info(result.path)
    assert info is not None
    assert info.pid == os.getpid()
    assert info.agent_id == cfg.agent.agent_id
    result.release()


def test_read_lock_info_missing(lock_dir: Path) -> None:
    assert read_lock_info(lock_dir / "missing.lock") is None


def test_stale_lock_reclaimed(lock_dir: Path) -> None:
    cfg = NetllmConfig()
    path = lock_dir / "agent.lock"
    path.write_text(
        json.dumps(
            {
                "pid": 999999,
                "agent_id": "stale",
                "listen": "127.0.0.1:11400",
                "started_at": "2020-01-01T00:00:00+00:00",
                "version": "0.0.0",
            }
        ),
        encoding="utf-8",
    )
    with patch("netllm_discovery.agent_lock.pid_alive", return_value=False):
        result = acquire_agent_lock(cfg)
    assert isinstance(result, AgentLock)
    info = read_lock_info(path)
    assert info is not None
    assert info.pid == os.getpid()
    result.release()


def test_already_running_when_holder_alive(lock_dir: Path) -> None:
    cfg = NetllmConfig()
    path = lock_dir / "agent.lock"
    path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "agent_id": "other",
                "listen": "127.0.0.1:11400",
                "started_at": "2020-01-01T00:00:00+00:00",
                "version": "0.0.0",
            }
        ),
        encoding="utf-8",
    )
    lock_mod._LOCK = None
    with (
        patch("netllm_discovery.agent_lock.pid_alive", return_value=True),
        patch("netllm_discovery.agent_lock._try_lock", return_value=False),
    ):
        result = acquire_agent_lock(cfg)
    assert isinstance(result, AlreadyRunning)
    assert result.info.pid == 4242


def test_serve_exits_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer
    from netllm_cli.commands import serve_lifecycle

    cfg = NetllmConfig()
    held = AlreadyRunning(
        info=lock_mod.AgentLockInfo(
            pid=4242,
            agent_id="abc",
            listen="127.0.0.1:11400",
            started_at="2020-01-01T00:00:00+00:00",
            version="0.5.0.0",
        ),
        path=Path("/tmp/agent.lock"),
    )
    monkeypatch.setattr(
        serve_lifecycle,
        "_config_path_option",
        lambda _path: Path("/tmp/config.toml"),
    )
    monkeypatch.setattr(
        serve_lifecycle,
        "_require_config",
        lambda _path: cfg,
    )
    monkeypatch.setattr(
        "netllm_discovery.agent_lock.acquire_agent_lock",
        lambda _cfg: held,
    )
    monkeypatch.setattr(
        "netllm_core.config.ensure_lan_mesh_defaults",
        lambda _cfg: False,
    )

    with pytest.raises(typer.Exit) as exc:
        serve_lifecycle.serve(
            config=None,
            host=None,
            port=None,
            replace=False,
            quiet=True,
        )
    assert exc.value.exit_code == 0
