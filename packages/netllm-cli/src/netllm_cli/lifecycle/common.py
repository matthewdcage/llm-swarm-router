"""Shared lifecycle hints for source installs."""

from __future__ import annotations


def source_install_hint(command: str) -> tuple[str, int]:
    if command == "start":
        print(
            "Background start is available for packaged installs "
            "(macOS app, Homebrew, Linux systemd, Windows service)."
        )
        print("For this install, run foreground agent mode with: netllm serve")
    else:
        print(
            "Background stop/restart requires a packaged install "
            "(macOS app, Homebrew, Linux systemd, or Windows service)."
        )
    return "", 1
