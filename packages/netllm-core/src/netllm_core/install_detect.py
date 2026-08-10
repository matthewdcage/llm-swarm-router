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

_APP_BUNDLE_MARKER = ".app/Contents/"


def _path_str(path: Path) -> str:
    """Normalize path for cross-platform substring checks."""
    return str(path).replace("\\", "/")


def _bundle_root_path() -> Path | None:
    env_bundle = os.environ.get("NETLLM_BUNDLE_PATH")
    if env_bundle:
        return Path(env_bundle)
    here = Path(__file__).resolve()
    path = _path_str(here)
    idx = path.find(_APP_BUNDLE_MARKER)
    if idx == -1:
        return None
    return Path(path[: idx + len(".app")])


def is_app_bundle() -> bool:
    """Return True if running inside the macOS .app bundle."""
    if os.environ.get("NETLLM_BUNDLE_PATH"):
        return True
    here = Path(__file__).resolve()
    return _APP_BUNDLE_MARKER in _path_str(here)


def get_app_bundle_cli_path() -> Path:
    """Return the app-bundle CLI path for the currently running bundle."""
    root = _bundle_root_path()
    if root is not None:
        return root / "Contents" / "MacOS" / _APP_BUNDLE_CLI_NAME

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


def is_editable_install() -> bool:
    """True when this package is imported from a source checkout.

    A `uv tool install --editable` (or `pip install -e`) leaves the modules in
    the working tree and only drops a `.pth` into site-packages, so `__file__`
    resolves to `<repo>/packages/netllm-core/src/netllm_core/...` rather than
    into site-packages. That distinction decides whether an OS package is a
    sane upgrade: installing a .deb over a developer's editable checkout
    replaces the CLI while leaving the checkout as the imported code, which is
    a genuinely confusing half-upgrade. Such an install upgrades with git.
    """
    here = _path_str(Path(__file__).resolve())
    if "/site-packages/" in here or "/dist-packages/" in here:
        return False
    # The layout marker, not a repo-name match: this must hold for any clone
    # path, and must not fire for a wheel that happens to unpack elsewhere.
    return "/packages/netllm-core/src/" in here


def get_install_method() -> str:
    """Return install channel for **lifecycle dispatch** (start/stop/restart).

    Deliberately ignores `is_editable_install()`: if a systemd unit or Windows
    service manages the agent, that is how it must be started and stopped even
    when the code being run is a working tree. Upgrades are a different
    question — see `get_upgrade_channel()`.
    """
    if is_app_bundle():
        return "app"
    if is_homebrew():
        return "homebrew"
    if is_linux_systemd():
        return "linux-systemd"
    if is_windows_service():
        return "windows-service"
    return "source"


def get_upgrade_channel() -> str:
    """Return the channel that should service an *upgrade*.

    Same answer as `get_install_method()` except for an editable checkout
    managed by an OS service: the service says how the agent runs, but the
    imported code is the working tree, so an OS package would replace the CLI
    and leave the checkout as the code actually loaded — a half-upgrade. Those
    upgrade with git.
    """
    method = get_install_method()
    if method in ("linux-systemd", "windows-service") and is_editable_install():
        return "source"
    return method


def can_applications_auto_install() -> bool:
    """True when the running bundle is installed under /Applications/."""
    root = _bundle_root_path()
    if root is None:
        return False
    path = _path_str(root)
    if not path.startswith("/Applications/"):
        return False
    return root.name in _APP_NAMES


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
