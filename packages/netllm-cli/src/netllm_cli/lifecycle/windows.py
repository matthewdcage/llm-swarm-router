"""Windows service lifecycle."""

from __future__ import annotations

import subprocess

from netllm_cli.install_detect import windows_service_name


def _run_sc(subcommand: str) -> int:
    service = windows_service_name()
    try:
        result = subprocess.run(
            ["sc", subcommand, service],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("Windows Service Control (sc.exe) is not available.")
        return 1
    if result.returncode != 0 and result.stderr:
        print(result.stderr.strip())
    return result.returncode


def lifecycle_command(command: str) -> int:
    mapping = {"start": "start", "stop": "stop", "restart": None}
    if command == "restart":
        stop_code = _run_sc("stop")
        if stop_code != 0:
            return stop_code
        code = _run_sc("start")
    else:
        code = _run_sc(mapping[command])
    if code == 0:
        if command == "start":
            print(f"netllm agent started ({windows_service_name()} service)")
        elif command == "stop":
            print("netllm agent stopped")
        else:
            print(f"netllm agent restarted ({windows_service_name()} service)")
    return code
