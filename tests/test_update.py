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
    with patch("netllm_core.update.get_install_method", return_value="homebrew"):
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
    with patch("netllm_core.update.get_install_method", return_value="app"):
        info = select_asset(release)
    assert info["asset_name"] == "llm-swarm-router.dmg"
    assert info["download_url"] == "https://example/dmg"


def test_find_sha256_sidecar() -> None:
    assets = (
        ReleaseAsset("llm-swarm-router.dmg", 1, "https://x/dmg"),
        ReleaseAsset("llm-swarm-router.dmg.sha256", 1, "https://x/hash"),
    )
    assert find_sha256_sidecar(assets, "llm-swarm-router.dmg") == "https://x/hash"


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
