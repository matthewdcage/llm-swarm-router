"""Installation method detection (app bundle, Homebrew, source)."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_APP_BUNDLE_CLI_NAME = "netllm-cli"
_PATH_CLI = "netllm"
_USER_CLI_SHIM = Path(".config") / "netllm" / "bin" / "netllm"
_APP_NAMES = ("llm-swarm-router.app", "netllm-mac.app")
_WINDOWS_SERVICE_NAME = "NetllmAgent"

_SYSTEMD_UNIT_PATHS = (
    Path("/etc/systemd/system/netllm.service"),
    Path("/usr/lib/systemd/system/netllm.service"),
    Path("/usr/lib/systemd/user/netllm.service"),
    Path.home() / ".config/systemd/user/netllm.service",
)


def is_app_bundle() -> bool:
    """Return True if running inside the macOS .app bundle."""
    if os.environ.get("NETLLM_BUNDLE_PATH"):
        return True
    here = Path(__file__).resolve()
    return ".app/Contents/" in str(here)


def get_app_bundle_cli_path() -> Path:
    """Return the app-bundle CLI path for the currently running bundle."""
    env_bundle = os.environ.get("NETLLM_BUNDLE_PATH")
    if env_bundle:
        return Path(env_bundle) / "Contents" / "MacOS" / _APP_BUNDLE_CLI_NAME

    here = Path(__file__).resolve()
    marker = ".app/Contents/"
    path = str(here)
    idx = path.find(marker)
    if idx == -1:
        for name in _APP_NAMES:
            app = Path("/Applications") / name
            cli = app / "Contents" / "MacOS" / _APP_BUNDLE_CLI_NAME
            if cli.is_file():
                return cli
        return (
            Path("/Applications")
            / _APP_NAMES[0]
            / "Contents"
            / "MacOS"
            / _APP_BUNDLE_CLI_NAME
        )
    app_root = Path(path[: idx + len(".app")])
    return app_root / "Contents" / "MacOS" / _APP_BUNDLE_CLI_NAME


def get_user_cli_shim_path() -> Path:
    """Return the user PATH shim installed by the macOS app."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "netllm" / "bin" / "netllm"
    return Path.home() / _USER_CLI_SHIM


def _is_executable(path: Path) -> bool:
    return path.exists() and os.access(path, os.X_OK)


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _is_app_managed_cli(path: Path) -> bool:
    """Return True when path points at the app-managed shim or wrapper."""
    if not _is_executable(path):
        return False
    user_shim = get_user_cli_shim_path()
    if _is_executable(user_shim) and _same_resolved_path(path, user_shim):
        return True
    app_cli = get_app_bundle_cli_path()
    return _is_executable(app_cli) and _same_resolved_path(path, app_cli)


def _path_resolves_to_app_managed_cli() -> bool:
    resolved = shutil.which(_PATH_CLI)
    return bool(resolved) and _is_app_managed_cli(Path(resolved))


def is_homebrew() -> bool:
    """Return True if running inside a Homebrew-installed virtualenv."""
    prefix = sys.prefix
    return "/Cellar/" in prefix or "/homebrew/" in prefix


def is_linux_systemd() -> bool:
    """Return True when a packaged systemd unit for netllm is present."""
    if sys.platform not in ("linux", "linux2"):
        return False
    return any(path.is_file() for path in _SYSTEMD_UNIT_PATHS)


def is_windows_service() -> bool:
    """Return True when the netllm Windows service is registered."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["sc", "query", _WINDOWS_SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and "SERVICE_NAME" in out.stdout.upper()


def is_source() -> bool:
    """Return True for editable / pip / uv source installs."""
    return (
        not is_app_bundle()
        and not is_homebrew()
        and not is_linux_systemd()
        and not is_windows_service()
    )


def get_install_method() -> str:
    """Return install channel for lifecycle dispatch."""
    if is_app_bundle():
        return "app"
    if is_homebrew():
        return "homebrew"
    if is_linux_systemd():
        return "linux-systemd"
    if is_windows_service():
        return "windows-service"
    return "source"


def windows_service_name() -> str:
    return _WINDOWS_SERVICE_NAME


def get_cli_prefix() -> str:
    """Return the correct CLI command prefix for the current installation."""
    if is_app_bundle():
        if _path_resolves_to_app_managed_cli():
            return _PATH_CLI
        return str(get_app_bundle_cli_path())
    return _PATH_CLI


def get_cli_command_prefix() -> str:
    """Return a shell-safe CLI command prefix for display/copy-paste."""
    return shlex.quote(get_cli_prefix())


def is_menubar_supervised() -> bool:
    """True when the macOS menubar app supervises the agent process."""
    return os.environ.get("NETLLM_SUPERVISED") == "menubar"


def skip_global_path_doctor_check() -> bool:
    """App bundle uses embedded CLI — global uv-tool PATH is optional."""
    if is_app_bundle() or is_menubar_supervised():
        return True
    resolved = shutil.which(_PATH_CLI)
    if resolved and _is_app_managed_cli(Path(resolved)):
        return True
    return False
