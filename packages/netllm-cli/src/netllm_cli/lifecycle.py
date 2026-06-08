"""Background agent lifecycle (app control socket, Homebrew services)."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

from netllm_cli.install_detect import (
    get_app_bundle_cli_path,
    is_app_bundle,
    is_homebrew,
)

_APP_NAMES = ("llm-swarm-router.app", "netllm-mac.app")


def app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "netllm"


def control_socket_path() -> Path:
    return app_support_dir() / "control.sock"


def app_bundle_path() -> Path:
    cli_path = get_app_bundle_cli_path()
    try:
        return cli_path.parents[2]
    except IndexError:
        for name in _APP_NAMES:
            candidate = Path("/Applications") / name
            if candidate.is_dir():
                return candidate
        return Path("/Applications") / _APP_NAMES[0]


def open_macos_app() -> None:
    app_path = app_bundle_path()
    subprocess.run(
        ["/usr/bin/open", "-gj", str(app_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def send_app_control(command: str, timeout: float = 2.0) -> dict:
    sock_path = control_socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(sock_path))
        sock.sendall(json.dumps({"command": command}).encode("utf-8") + b"\n")
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        raw = b"".join(chunks).split(b"\n", 1)[0]
        return json.loads(raw.decode("utf-8"))


def send_app_control_with_launch(command: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    open_macos_app()
    while time.monotonic() < deadline:
        try:
            return send_app_control(command)
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Could not reach netllm-mac control socket: {last_error}")


def wait_app_control_state(states: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = send_app_control("status")
        if last.get("state") in states:
            return last
        time.sleep(0.5)
    return last


def run_brew_services(command: str) -> int:
    brew = shutil.which("brew")
    if not brew:
        print("Homebrew is not available on PATH.")
        return 1
    result = subprocess.run([brew, "services", command, "netllm"])
    return result.returncode


def lifecycle_command(
    command: str,
    *,
    timeout: float = 60.0,
    no_wait: bool = False,
) -> int:
    """Run start/stop/restart for the current installation channel."""
    if is_app_bundle() or control_socket_path().exists():
        try:
            if command == "stop":
                try:
                    response = send_app_control(command)
                except OSError:
                    print("netllm agent stopped")
                    return 0
            else:
                response = send_app_control_with_launch(command, timeout=timeout)
            if not response.get("ok"):
                print(response.get("message") or f"netllm {command} failed")
                return 1

            if command in {"start", "restart"} and not no_wait:
                response = wait_app_control_state({"running", "unresponsive"}, timeout)
                if response.get("state") not in {"running", "unresponsive"}:
                    print(
                        f"netllm agent is {response.get('state', 'unknown')} "
                        f"after {int(timeout)}s."
                    )
                    return 1

            if command == "stop":
                print("netllm agent stopped")
            elif command == "start":
                print(
                    f"netllm agent {response.get('state')} "
                    f"on port {response.get('port')}"
                )
            elif command == "restart":
                print(f"netllm agent restarted on port {response.get('port')}")
            return 0
        except Exception as exc:
            print(f"Failed to control netllm-mac: {exc}")
            return 1

    if is_homebrew():
        mapping = {"start": "start", "stop": "stop", "restart": "restart"}
        return run_brew_services(mapping[command])

    if command == "start":
        print(
            "Background start is available for the macOS app and Homebrew installs."
        )
        print("For this install, run foreground agent mode with: netllm serve")
    else:
        print("Background stop/restart requires the macOS app or Homebrew service.")
    return 1
