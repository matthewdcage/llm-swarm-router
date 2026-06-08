"""Tests for install channel detection."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from netllm_cli.install_detect import (
    get_install_method,
    get_user_cli_shim_path,
    is_app_bundle,
    is_homebrew,
    is_source,
)


def test_is_source_by_default() -> None:
    with patch.dict("os.environ", {}, clear=False):
        with patch("netllm_cli.install_detect.is_app_bundle", return_value=False):
            with patch("netllm_cli.install_detect.is_homebrew", return_value=False):
                assert is_source() is True
                assert get_install_method() == "source"


def test_is_app_bundle_env() -> None:
    env = {"NETLLM_BUNDLE_PATH": "/Applications/netllm-mac.app"}
    with patch.dict("os.environ", env):
        assert is_app_bundle() is True
        assert get_install_method() == "app"


def test_is_app_bundle_path_marker() -> None:
    fake = Path(
        "/Applications/netllm-mac.app/Contents/Resources/"
        "netllm_packages/netllm_cli/install_detect.py"
    )
    with patch.object(Path, "resolve", return_value=fake):
        assert is_app_bundle() is True


def test_is_homebrew_prefix() -> None:
    with patch.object(sys, "prefix", "/opt/homebrew/Cellar/netllm/0.2.0/libexec"):
        assert is_homebrew() is True
        assert get_install_method() == "homebrew"


def test_user_cli_shim_xdg() -> None:
    with patch.dict("os.environ", {"XDG_CONFIG_HOME": "/tmp/xdg"}):
        assert get_user_cli_shim_path() == Path("/tmp/xdg/netllm/bin/netllm")
