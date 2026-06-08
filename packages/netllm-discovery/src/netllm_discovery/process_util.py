"""Cross-platform port ownership and process stop helpers."""

from __future__ import annotations

import re
import subprocess
import sys


def _pid_from_lsof(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return int(out.stdout.strip().splitlines()[0])
    except ValueError:
        return None


def _pid_from_ss(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    match = re.search(r"pid=(\d+)", out.stdout)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _pid_from_netstat(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    needle = f":{port}"
    for line in out.stdout.splitlines():
        if "LISTENING" not in line.upper():
            continue
        if needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def port_owner_pid(port: int) -> int | None:
    """Best-effort PID listening on TCP port (cross-platform)."""
    if sys.platform == "win32":
        return _pid_from_netstat(port)
    pid = _pid_from_lsof(port)
    if pid is not None:
        return pid
    if sys.platform == "linux":
        return _pid_from_ss(port)
    return None


def terminate_pid(pid: int) -> bool:
    """Send graceful termination to a process; return True if signal sent."""
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return out.returncode == 0
    try:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False
