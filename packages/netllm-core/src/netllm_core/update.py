"""GitHub release checking, asset selection, and update verification."""

from __future__ import annotations

import hashlib
import re
import sys
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

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


# Prerelease ordering. Every rank is BELOW `_FINAL_RANK`, which is what makes
# `0.5.0rc1 < 0.5.0` true. Mirrored in
# apps/netllm-mac/Sources/Config/VersionOrdering.swift; the two rosters are
# asserted equal by tests/test_version_ordering.py so they cannot drift.
_PRERELEASE_RANK: dict[str, int] = {
    "dev": -4,
    "alpha": -3,
    "a": -3,
    "beta": -2,
    "b": -2,
    "rc": -1,
    "c": -1,
    "pre": -1,
    "preview": -1,
}
_FINAL_RANK = 0

# Release segment, then an optional prerelease label with an optional number.
# Longest alternatives first so "alpha" is not eaten by "a". Matched with
# `match`, not `fullmatch`: an unrecognised trailing tag (`0.2.2.1.ci`, a local
# `+build`) is ignored rather than reordering anything.
_VERSION_RE = re.compile(
    r"\s*v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-._]?(?P<label>preview|alpha|beta|dev|pre|rc|a|b|c)(?![A-Za-z])"
    r"[-._]?(?P<num>\d+)?)?",
    re.IGNORECASE,
)


def _version_key(value: str) -> tuple[list[int], int, int]:
    """(release components, prerelease rank, prerelease number).

    This used to be `[int(x) for x in re.findall(r"\\d+", value)]` — every
    digit run in the string, compared as a list. That read `0.5.0rc1` as
    `[0, 5, 0, 1]`, which is both *newer* than `0.5.0` and *exactly equal* to
    the real build `0.5.0.1`. It was masked because `fetch_latest_release`
    filters prereleases out of the update check, so the only exposed caller was
    the one that matters here: `service/status.py` deciding which machine in
    the mesh is behind. An operator running a release candidate was told to
    downgrade, and a peer on `0.5.0.1` looked identical to one on `0.5.0rc1`.

    Unparseable input (a peer that reports `""` or garbage — this runs on data
    another machine on the LAN controls) yields the zero version rather than
    raising. Callers treat "same version" as "nothing to warn about", which is
    the correct inert outcome for a version we cannot read.
    """
    match = _VERSION_RE.match(value or "")
    if match is None:
        return ([0], _FINAL_RANK, 0)
    release = [int(part) for part in match.group("release").split(".")]
    label = match.group("label")
    if label is None:
        return (release, _FINAL_RANK, 0)
    return (release, _PRERELEASE_RANK[label.lower()], int(match.group("num") or 0))


def compare_versions(current: str, latest: str) -> int:
    """Return -1 if current < latest, 0 if equal, 1 if current > latest.

    Ordering is pinned by `tests/contract/version-ordering.json`, the corpus
    shared with the macOS app's comparator. Add a case there, not here.
    """
    left, left_rank, left_num = _version_key(current)
    right, right_rank, right_num = _version_key(latest)
    length = max(len(left), len(right))
    left = left + [0] * (length - len(left))
    right = right + [0] * (length - len(right))
    lhs = (left, left_rank, left_num)
    rhs = (right, right_rank, right_num)
    if lhs < rhs:
        return -1
    if lhs > rhs:
        return 1
    return 0


def is_version_like(value: str) -> bool:
    """True when `value` carries a release number this comparator can read.

    `_version_key` deliberately degrades anything unreadable to the zero
    version so it never raises. That is right for ordering and wrong for
    *reporting*: `compare_versions("0.5.0", "not-a-version")` is 1, so a peer
    sending junk looked like a machine on version 0.0.0 and produced a
    confident "more than two minors of skew" warning about a version nobody is
    running. Callers that turn a comparison into advice must ask this first.
    """
    return _VERSION_RE.match(value or "") is not None


SkewLevel = Literal["supported", "degraded", "unsupported"]


@dataclass(frozen=True)
class MeshSkew:
    """How far apart two machines in one mesh are, and what to say about it.

    The compatibility promise (docs/mesh-upgrade.md, PROGRAM.md §4) is
    **N-1 minor fully supported, N-2 degraded, beyond that an error**. It was
    prose in one planning document and nothing computed it, so every version
    difference produced the same "update it" whether the peer was one patch
    behind or two majors.

    Deliberately advisory. Nothing refuses a peer on skew: a mesh that
    partitions itself the moment someone starts an upgrade is worse than one
    that tells you it is mid-upgrade. The peer's version is also data another
    machine on the LAN controls, so it may only produce text, never a decision
    about what this node does with its own config.
    """

    level: SkewLevel
    minors: int
    advice: str


_SKEW_ADVICE: dict[SkewLevel, str] = {
    "supported": ("update it when convenient — one minor of skew is fully supported"),
    "degraded": (
        "update it — two minors of skew is supported only in degraded mode "
        "and features on the newer side may be unavailable mesh-wide"
    ),
    "unsupported": (
        "update it now — more than two minors of skew is outside the "
        "compatibility promise and the mesh is not expected to work"
    ),
}


def mesh_skew(left: str, right: str) -> MeshSkew:
    """Classify the distance between two netllm versions in one mesh."""
    (left_release, _, _) = _version_key(left)
    (right_release, _, _) = _version_key(right)
    left_major, left_minor = (left_release + [0, 0])[:2]
    right_major, right_minor = (right_release + [0, 0])[:2]

    if left_major != right_major:
        # A major bump is by definition more than a minor of skew; there is no
        # meaningful minor distance across it.
        return MeshSkew("unsupported", -1, _SKEW_ADVICE["unsupported"])

    minors = abs(left_minor - right_minor)
    if minors <= 1:
        level: SkewLevel = "supported"
    elif minors == 2:
        level = "degraded"
    else:
        level = "unsupported"
    return MeshSkew(level, minors, _SKEW_ADVICE[level])


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


def _asset_by_glob(
    assets: tuple[ReleaseAsset, ...], pattern: str
) -> ReleaseAsset | None:
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
