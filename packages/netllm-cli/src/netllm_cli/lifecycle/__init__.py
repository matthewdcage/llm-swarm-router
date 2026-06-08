"""Background agent lifecycle dispatch."""

from __future__ import annotations

from netllm_cli.install_detect import get_install_method, is_app_bundle

from . import common, darwin, linux, windows
from .darwin import control_socket_path

__all__ = ["control_socket_path", "lifecycle_command"]


def lifecycle_command(
    command: str,
    *,
    timeout: float = 60.0,
    no_wait: bool = False,
) -> int:
    """Run start/stop/restart for the current installation channel."""
    method = get_install_method()
    if method == "linux-systemd":
        return linux.lifecycle_command(command)
    if method == "windows-service":
        return windows.lifecycle_command(command)
    if method == "app" or (is_app_bundle() and control_socket_path().exists()):
        return darwin.lifecycle_command(
            command,
            timeout=timeout,
            no_wait=no_wait,
            use_app=True,
        )
    if method == "homebrew":
        mapping = {"start": "start", "stop": "stop", "restart": "restart"}
        return darwin.run_brew_services(mapping[command])
    return common.source_install_hint(command)[1]
