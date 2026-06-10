"""Cross-platform paths and defaults."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def is_darwin() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def default_hostname() -> str:
    return socket.gethostname()


_local_admin_hosts: frozenset[str] | None = None


def local_admin_client_hosts() -> frozenset[str]:
    """Hosts that may call loopback-gated admin routes from this machine."""
    global _local_admin_hosts
    if _local_admin_hosts is not None:
        return _local_admin_hosts
    hosts: set[str] = {"127.0.0.1", "::1", "localhost", "testclient"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            hosts.add(info[4][0].lower())
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            hosts.add(sock.getsockname()[0].lower())
    except OSError:
        pass
    _local_admin_hosts = frozenset(hosts)
    return _local_admin_hosts


def default_log_dir() -> Path:
    if is_darwin():
        return Path.home() / "Library" / "Application Support" / "netllm" / "logs"
    if is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "netllm" / "logs"
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "netllm" / "logs"
    return Path.home() / ".local" / "state" / "netllm" / "logs"


def default_discovery_providers() -> list[str]:
    if is_darwin():
        return ["omlx", "ollama", "lmstudio", "vllm"]
    return ["ollama", "lmstudio", "vllm"]
