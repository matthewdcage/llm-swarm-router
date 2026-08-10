"""Tests for netllm_core.update release checking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from netllm_core.update import (
    GitHubReleaseInfo,
    ReleaseAsset,
    compare_versions,
    fetch_latest_release,
    find_sha256_sidecar,
    parse_sha256_sidecar_text,
    select_asset,
    verify_sha256,
)


def test_compare_versions_numeric() -> None:
    assert compare_versions("0.2.3", "0.2.4") < 0
    assert compare_versions("0.2.3.1", "0.2.3") > 0
    assert compare_versions("1.0", "1.0.0") == 0


def test_select_asset_homebrew() -> None:
    release = GitHubReleaseInfo(
        version="0.2.4",
        prerelease=False,
        html_url="https://example/releases/tag/v0.2.4",
        assets=(),
    )
    with patch("netllm_core.update.get_upgrade_channel", return_value="homebrew"):
        info = select_asset(release)
    assert info["upgrade_hint"] == "brew upgrade netllm"
    assert info["download_url"] is None


def test_select_asset_dmg() -> None:
    release = GitHubReleaseInfo(
        version="0.2.4",
        prerelease=False,
        html_url="https://example/releases/tag/v0.2.4",
        assets=(
            ReleaseAsset(
                name="llm-swarm-router.dmg",
                size=100,
                download_url="https://example/dmg",
            ),
        ),
    )
    with patch("netllm_core.update.get_upgrade_channel", return_value="app"):
        info = select_asset(release)
    assert info["asset_name"] == "llm-swarm-router.dmg"
    assert info["download_url"] == "https://example/dmg"


def _linux_release(asset_name: str) -> GitHubReleaseInfo:
    return GitHubReleaseInfo(
        version="0.5.0.1",
        prerelease=False,
        html_url="https://example/releases/tag/v0.5.0.1",
        assets=(
            ReleaseAsset(name=asset_name, size=100, download_url="https://example/pkg"),
        ),
    )


def test_linux_package_carries_the_command_that_installs_it() -> None:
    """The OS-package channels used to hand over a file and say nothing.

    Homebrew and source installs both return an upgrade_hint; linux-systemd
    returned the asset with `upgrade_hint: None`, so "click Download" was the
    entirety of the upgrade instructions for a .deb.
    """
    with patch("netllm_core.update.get_upgrade_channel", return_value="linux-systemd"):
        info = select_asset(_linux_release("netllm_0.5.0.1_amd64.deb"))
    assert info["asset_name"] == "netllm_0.5.0.1_amd64.deb"
    assert info["upgrade_hint"] == "sudo apt install ./netllm_0.5.0.1_amd64.deb"


def test_the_install_hint_matches_the_package_format() -> None:
    """.deb and .rpm do not share a command; guessing is worse than silence."""
    with patch("netllm_core.update.get_upgrade_channel", return_value="linux-systemd"):
        rpm = select_asset(_linux_release("netllm-0.5.0.1.x86_64.rpm"))
    assert rpm["upgrade_hint"] == "sudo dnf install ./netllm-0.5.0.1.x86_64.rpm"

    with patch(
        "netllm_core.update.get_upgrade_channel", return_value="windows-service"
    ):
        zipped = select_asset(_linux_release("netllm-0.5.0.1-windows-x64.zip"))
    assert zipped["upgrade_hint"] is None


def test_an_editable_checkout_is_not_offered_an_os_package() -> None:
    """Installing a .deb over an editable checkout is a half-upgrade.

    It replaces the CLI while the imported code stays the working tree. But
    the *lifecycle* answer is unchanged: a systemd unit still starts and stops
    the agent. These are two different questions and two different functions —
    conflating them would stop `netllm start` using systemctl for every
    developer running from a checkout.
    """
    from netllm_core import install_detect

    with (
        patch.object(install_detect, "is_app_bundle", return_value=False),
        patch.object(install_detect, "is_homebrew", return_value=False),
        patch.object(install_detect, "is_linux_systemd", return_value=True),
        patch.object(install_detect, "is_editable_install", return_value=True),
    ):
        # Lifecycle still goes through systemd…
        assert install_detect.get_install_method() == "linux-systemd"
        # …but the upgrade is git, not a package.
        assert install_detect.get_upgrade_channel() == "source"

    with (
        patch.object(install_detect, "is_app_bundle", return_value=False),
        patch.object(install_detect, "is_homebrew", return_value=False),
        patch.object(install_detect, "is_linux_systemd", return_value=True),
        patch.object(install_detect, "is_editable_install", return_value=False),
    ):
        assert install_detect.get_upgrade_channel() == "linux-systemd"


def test_find_sha256_sidecar() -> None:
    assets = (
        ReleaseAsset("llm-swarm-router.dmg", 1, "https://x/dmg"),
        ReleaseAsset("llm-swarm-router.dmg.sha256", 1, "https://x/hash"),
    )
    assert find_sha256_sidecar(assets, "llm-swarm-router.dmg") == "https://x/hash"


@pytest.mark.asyncio
async def test_the_sidecar_fetch_follows_github_redirects() -> None:
    """Release-asset URLs 302 to objects.githubusercontent.com.

    httpx does not follow redirects by default (requests does), so this
    returned None for every release that ships a checksum — and because a
    missing sha256 rendered as nothing at all, the failure was invisible for
    as long as it existed. Asserted on the call, not the outcome: a mock that
    returns 200 regardless would pass either way.
    """
    from netllm_core.update import fetch_sha256_for_asset

    assets = (
        ReleaseAsset("netllm_1.0_amd64.deb", 1, "https://x/deb"),
        ReleaseAsset("netllm_1.0_amd64.deb.sha256", 1, "https://x/deb.sha256"),
    )
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.text = "abc123  netllm_1.0_amd64.deb\n"
    client.get.return_value = response

    got = await fetch_sha256_for_asset(client, assets, "netllm_1.0_amd64.deb")

    assert got == "abc123"
    assert client.get.await_args.kwargs["follow_redirects"] is True


def test_parse_sha256_sidecar_text() -> None:
    text = "abc123  llm-swarm-router.dmg\n"
    assert parse_sha256_sidecar_text(text, "llm-swarm-router.dmg") == "abc123"


def test_verify_sha256(tmp_path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello")
    import hashlib

    digest = hashlib.sha256(b"hello").hexdigest()
    assert verify_sha256(path, digest)


@pytest.mark.asyncio
async def test_fetch_latest_release_skips_prerelease() -> None:
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "tag_name": "v0.2.4",
        "prerelease": True,
        "html_url": "https://example",
        "assets": [],
    }
    client.get.return_value = response
    assert await fetch_latest_release(client, force=True) is None
