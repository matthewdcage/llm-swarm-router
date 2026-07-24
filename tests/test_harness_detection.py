"""Tests for PATH-only harness detection and its TTL cache."""

from __future__ import annotations

from unittest.mock import patch

import netllm_core.harness_detection as harness_detection
from netllm_core.harness_detection import clear_cache, detect
from netllm_core.known_harnesses import KnownHarness

_HARNESS = KnownHarness(id="fake", display_name="Fake", cli_commands=("fake-cli",))


def setup_function() -> None:
    clear_cache()


def test_detected_when_which_finds_a_command() -> None:
    with patch("shutil.which", return_value="/usr/local/bin/fake-cli") as which:
        assert detect(_HARNESS) is True
    which.assert_called_once_with("fake-cli")


def test_not_detected_when_which_finds_nothing() -> None:
    with patch("shutil.which", return_value=None):
        assert detect(_HARNESS) is False


def test_cache_hit_within_ttl_does_not_reprobe() -> None:
    with patch("shutil.which", return_value="/usr/local/bin/fake-cli") as which:
        assert detect(_HARNESS) is True
        assert detect(_HARNESS) is True
    which.assert_called_once()


def test_cache_expires_after_ttl() -> None:
    with patch("shutil.which", return_value="/usr/local/bin/fake-cli") as which:
        assert detect(_HARNESS) is True
    which.assert_called_once()

    real_monotonic = harness_detection.time.monotonic
    with (
        patch("shutil.which", return_value=None) as which_after,
        patch.object(
            harness_detection.time,
            "monotonic",
            return_value=real_monotonic() + harness_detection._CACHE_TTL_S + 1,
        ),
    ):
        assert detect(_HARNESS) is False
    which_after.assert_called_once()


def test_never_spawns_a_subprocess() -> None:
    with (
        patch("shutil.which", return_value=None),
        patch("subprocess.run") as run,
        patch("subprocess.Popen") as popen,
    ):
        detect(_HARNESS)
    run.assert_not_called()
    popen.assert_not_called()
