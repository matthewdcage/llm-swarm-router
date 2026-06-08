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
