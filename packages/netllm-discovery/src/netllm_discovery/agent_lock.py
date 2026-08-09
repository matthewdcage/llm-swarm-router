"""Cross-platform agent singleton lock (flock + JSON payload)."""

from __future__ import annotations

import atexit
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from netllm_core.models import NetllmConfig

from netllm_discovery.process_util import pid_alive

_LOCK: AgentLock | None = None


@dataclass(frozen=True)
class AgentLockInfo:
    pid: int
    agent_id: str
    listen: str
    started_at: str
    version: str


@dataclass(frozen=True)
class AlreadyRunning:
    """Another live process holds the agent lock."""

    info: AgentLockInfo
    path: Path


class AgentLock:
    """Held for the lifetime of the foreground agent process."""

    def __init__(self, path: Path, fd: int, handle: IO[Any]) -> None:
        self.path = path
        self._fd = fd
        self._handle = handle
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _unlock_fd(self._fd)
        try:
            self._handle.close()
        except OSError:
            pass
        global _LOCK
        if _LOCK is self:
            _LOCK = None

    def __enter__(self) -> AgentLock:
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def agent_lock_path(config: NetllmConfig) -> Path:
    """Path to the singleton lock file for this install."""
    return config.resolved_log_dir().parent / "agent.lock"


def read_lock_info(path: Path) -> AgentLockInfo | None:
    """Best-effort read of lock payload (may be stale if holder crashed)."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    return AgentLockInfo(
        pid=pid,
        agent_id=str(data.get("agent_id", "")),
        listen=str(data.get("listen", "")),
        started_at=str(data.get("started_at", "")),
        version=str(data.get("version", "")),
    )


def acquire_agent_lock(config: NetllmConfig) -> AgentLock | AlreadyRunning:
    """Acquire the singleton lock or report an already-running agent."""
    global _LOCK
    if _LOCK is not None and not _LOCK._released:
        return _LOCK

    path = agent_lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "r+", encoding="utf-8")
    fd = handle.fileno()

    if not _try_lock(fd):
        existing = read_lock_info(path)
        if existing is not None and pid_alive(existing.pid):
            handle.close()
            return AlreadyRunning(info=existing, path=path)
        if not _try_lock(fd):
            handle.close()
            stale = existing or AgentLockInfo(
                pid=0,
                agent_id="",
                listen="",
                started_at="",
                version="",
            )
            return AlreadyRunning(info=stale, path=path)

    info = _lock_payload(config)
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(info.__dict__, separators=(",", ":")))
    handle.flush()
    os.fsync(fd)

    lock = AgentLock(path=path, fd=fd, handle=handle)
    _LOCK = lock
    atexit.register(lock.release)
    return lock


def _lock_payload(config: NetllmConfig) -> AgentLockInfo:
    from netllm_core.version import get_version

    return AgentLockInfo(
        pid=os.getpid(),
        agent_id=config.agent.agent_id,
        listen=config.agent.listen,
        started_at=datetime.now(UTC).isoformat(),
        version=get_version(),
    )


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
