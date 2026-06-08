"""Installation method detection — re-export from netllm_core."""

from netllm_core.install_detect import (
    can_applications_auto_install,
    get_app_bundle_cli_path,
    get_cli_command_prefix,
    get_cli_prefix,
    get_install_method,
    get_user_cli_shim_path,
    is_app_bundle,
    is_homebrew,
    is_linux_systemd,
    is_menubar_supervised,
    is_source,
    is_windows_service,
    skip_global_path_doctor_check,
    windows_service_name,
)

__all__ = [
    "can_applications_auto_install",
    "get_app_bundle_cli_path",
    "get_cli_command_prefix",
    "get_cli_prefix",
    "get_install_method",
    "get_user_cli_shim_path",
    "is_app_bundle",
    "is_homebrew",
    "is_linux_systemd",
    "is_menubar_supervised",
    "is_source",
    "is_windows_service",
    "skip_global_path_doctor_check",
    "windows_service_name",
]
