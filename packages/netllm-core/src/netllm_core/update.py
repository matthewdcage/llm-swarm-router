"""GitHub release checking, asset selection, and update verification."""

from __future__ import annotations

import hashlib
import platform
import re
import sys
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import httpx

from netllm_core.install_detect import (
    can_applications_auto_install,
    get_install_method,
)
from netllm_core.sdk_versions import sdk_versions_payload
from netllm_core.version import get_version

GITHUB_REPO = "matthewdcage/llm-swarm-router"
GITHUB_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"netllm/{get_version()}"

_RELEASE_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_TTL_SECONDS = 900


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    download_url: str


@dataclass(frozen=True)
class GitHubReleaseInfo:
    version: str
    prerelease: bool
    html_url: str
    assets: tuple[ReleaseAsset, ...]


def compare_versions(current: str, latest: str) -> int:
    """Return -1 if current < latest, 0 if equal, 1 if current > latest."""

    def _parts(value: str) -> list[int]:
        nums = [int(x) for x in re.findall(r"\d+", value)]
        return nums or [0]

    left, right = _parts(current), _parts(latest)
    length = max(len(left), len(right))
    left.extend([0] * (length - len(left)))
    right.extend([0] * (length - len(right)))
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _parse_release(data: dict[str, Any]) -> GitHubReleaseInfo | None:
    tag = str(data.get("tag_name") or "")
    if not tag:
        return None
    version = tag[1:] if tag.startswith("v") else tag
    assets: list[ReleaseAsset] = []
    for raw in data.get("assets") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        url = str(raw.get("browser_download_url") or "")
        if not name or not url:
            continue
        size = int(raw.get("size") or 0)
        assets.append(ReleaseAsset(name=name, size=size, download_url=url))
    return GitHubReleaseInfo(
        version=version,
        prerelease=bool(data.get("prerelease")),
        html_url=str(data.get("html_url") or ""),
        assets=tuple(assets),
    )


async def fetch_latest_release(
    client: httpx.AsyncClient | None = None,
    *,
    force: bool = False,
) -> GitHubReleaseInfo | None:
    now = time.time()
    if (
        not force
        and _RELEASE_CACHE["payload"] is not None
        and now < float(_RELEASE_CACHE["expires_at"])
    ):
        return _RELEASE_CACHE["payload"]

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        response = await client.get(
            GITHUB_LATEST_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        if response.status_code != 200:
            return None
        release = _parse_release(response.json())
        if release is None or release.prerelease:
            return None
        _RELEASE_CACHE["payload"] = release
        _RELEASE_CACHE["expires_at"] = now + _CACHE_TTL_SECONDS
        return release
    finally:
        if owns_client:
            await client.aclose()


def _asset_by_name(assets: tuple[ReleaseAsset, ...], name: str) -> ReleaseAsset | None:
    for asset in assets:
        if asset.name == name:
            return asset
    return None


def _asset_by_glob(assets: tuple[ReleaseAsset, ...], pattern: str) -> ReleaseAsset | None:
    for asset in assets:
        if fnmatch(asset.name, pattern):
            return asset
    return None


def _linux_package_asset(
    assets: tuple[ReleaseAsset, ...], version: str
) -> ReleaseAsset | None:
    deb = _asset_by_name(assets, f"netllm_{version}_amd64.deb")
    if deb is not None:
        return deb
    return _asset_by_glob(assets, "netllm-*.rpm") or _asset_by_glob(assets, "*.rpm")


def select_asset(
    release: GitHubReleaseInfo,
    install_method: str | None = None,
) -> dict[str, Any]:
    method = install_method or get_install_method()
    version = release.version

    if method == "homebrew":
        return {
            "download_url": None,
            "asset_name": None,
            "asset_size": None,
            "upgrade_hint": "brew upgrade netllm",
        }

    if method == "source":
        return {
            "download_url": release.html_url,
            "asset_name": None,
            "asset_size": None,
            "upgrade_hint": "git pull && uv sync",
        }

    asset: ReleaseAsset | None = None
    if method == "app" or sys.platform == "darwin":
        asset = _asset_by_name(assets=release.assets, name="llm-swarm-router.dmg")
    elif method == "windows-service" or sys.platform == "win32":
        asset = _asset_by_name(
            assets=release.assets,
            name=f"netllm-{version}-windows-x64.zip",
        ) or _asset_by_glob(release.assets, "netllm-*-windows-x64.zip")
    elif method == "linux-systemd" or sys.platform.startswith("linux"):
        asset = _linux_package_asset(release.assets, version)

    if asset is None:
        return {
            "download_url": release.html_url,
            "asset_name": None,
            "asset_size": None,
            "upgrade_hint": None,
        }

    return {
        "download_url": asset.download_url,
        "asset_name": asset.name,
        "asset_size": asset.size,
        "upgrade_hint": None,
    }


def find_sha256_sidecar(
    assets: tuple[ReleaseAsset, ...], asset_name: str | None
) -> str | None:
    if not asset_name:
        return None
    sidecar = _asset_by_name(assets, f"{asset_name}.sha256")
    if sidecar is not None:
        return sidecar.download_url
    sums = _asset_by_name(assets, "SHA256SUMS")
    if sums is not None:
        return sums.download_url
    return None


def parse_sha256_sidecar_text(text: str, asset_name: str | None = None) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1]
        if asset_name is None or name == asset_name:
            return digest.lower()
    return None


async def fetch_sha256_for_asset(
    client: httpx.AsyncClient,
    assets: tuple[ReleaseAsset, ...],
    asset_name: str | None,
) -> str | None:
    url = find_sha256_sidecar(assets, asset_name)
    if not url:
        return None
    response = await client.get(url, headers={"User-Agent": USER_AGENT})
    if response.status_code != 200:
        return None
    if url.endswith("SHA256SUMS"):
        return parse_sha256_sidecar_text(response.text, asset_name)
    digest = response.text.strip().split()[0]
    return digest.lower() if digest else None


def verify_sha256(path: Path, expected_hex: str) -> bool:
    digest = hashlib.sha256(path.read_bytes()).hexdigest().lower()
    return digest == expected_hex.lower()


def cleanup_cache(cache_dir: Path, *, keep_paths: list[Path] | None = None) -> None:
    if not cache_dir.is_dir():
        return
    keep = {p.resolve() for p in keep_paths or []}
    for path in cache_dir.glob("*"):
        if path.resolve() in keep:
            continue
        if path.suffix in {".dmg", ".download", ".zip", ".deb", ".rpm"}:
            path.unlink(missing_ok=True)


async def build_update_check_payload(
    client: httpx.AsyncClient | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    current = get_version()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        release = await fetch_latest_release(client, force=force)
        if release is None:
            return {
                "current": current,
                "latest": current,
                "update_available": False,
                "prerelease": False,
                "release_notes_url": f"https://github.com/{GITHUB_REPO}/releases/latest",
                "download_url": None,
                "asset_name": None,
                "asset_size": None,
                "sha256": None,
                "upgrade_hint": None,
                "can_auto_install": False,
                "error": "Unable to fetch latest stable release",
            }

        asset_info = select_asset(release)
        sha256: str | None = None
        asset_name = asset_info.get("asset_name")
        if isinstance(asset_name, str):
            sha256 = await fetch_sha256_for_asset(client, release.assets, asset_name)

        update_available = compare_versions(current, release.version) < 0
        can_auto = (
            get_install_method() == "app"
            and can_applications_auto_install()
            and asset_info.get("download_url") is not None
        )

        return {
            "current": current,
            "latest": release.version,
            "update_available": update_available,
            "prerelease": release.prerelease,
            "release_notes_url": release.html_url,
            "download_url": asset_info.get("download_url"),
            "asset_name": asset_info.get("asset_name"),
            "asset_size": asset_info.get("asset_size"),
            "sha256": sha256,
            "upgrade_hint": asset_info.get("upgrade_hint"),
            "can_auto_install": can_auto,
            "error": None,
        }
    finally:
        if owns_client:
            await client.aclose()


def version_payload() -> dict[str, Any]:
    return {
        "version": get_version(),
        "build": None,
        "platform": sys.platform,
        "install_method": get_install_method(),
        "sdk_versions": sdk_versions_payload(),
    }
