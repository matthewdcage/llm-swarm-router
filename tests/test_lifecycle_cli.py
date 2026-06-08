"""Tests for lifecycle command dispatch."""

from __future__ import annotations

from unittest.mock import patch

from netllm_cli.lifecycle import lifecycle_command


def test_lifecycle_source_start_hint() -> None:
    with patch("netllm_cli.lifecycle.control_socket_path") as sock:
        sock.return_value.exists.return_value = False
        with patch("netllm_cli.lifecycle.is_app_bundle", return_value=False):
            with patch("netllm_cli.lifecycle.is_homebrew", return_value=False):
                rc = lifecycle_command("start")
    assert rc == 1


def test_lifecycle_homebrew_delegates() -> None:
    with patch("netllm_cli.lifecycle.control_socket_path") as sock:
        sock.return_value.exists.return_value = False
        with patch("netllm_cli.lifecycle.is_app_bundle", return_value=False):
            with patch("netllm_cli.lifecycle.is_homebrew", return_value=True):
                patcher = "netllm_cli.lifecycle.run_brew_services"
                with patch(patcher, return_value=0) as brew:
                    rc = lifecycle_command("stop")
    assert rc == 0
    brew.assert_called_once_with("stop")


def test_lifecycle_app_socket_start() -> None:
    with patch("netllm_cli.lifecycle.send_app_control_with_launch") as send:
        send.return_value = {"ok": True, "state": "running", "port": 11400}
        with patch("netllm_cli.lifecycle.wait_app_control_state") as wait:
            wait.return_value = {"state": "running", "port": 11400}
            with patch("netllm_cli.lifecycle.control_socket_path") as sock:
                sock.return_value.exists.return_value = True
                rc = lifecycle_command("start", timeout=5.0)
    assert rc == 0
    send.assert_called_once()
