"""Linux systemd lifecycle."""

from __future__ import annotations

import shutil
import subprocess


def run_systemctl(command: str, *, user: bool = True) -> int:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        print("systemctl is not available on PATH.")
        return 1
    args = [systemctl]
    if user:
        args.append("--user")
    args.extend([command, "netllm"])
    result = subprocess.run(args, check=False)
    return result.returncode


def lifecycle_command(command: str) -> int:
    mapping = {"start": "start", "stop": "stop", "restart": "restart"}
    code = run_systemctl(mapping[command])
    if code == 0:
        if command == "start":
            print("netllm agent started (systemd user service)")
        elif command == "stop":
            print("netllm agent stopped")
        else:
            print("netllm agent restarted")
    return code
