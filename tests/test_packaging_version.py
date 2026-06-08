"""Packaging version mapping regression tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RPM_VERSION_SH = ROOT / "packaging" / "linux" / "rpm-version.sh"


def _rpm_version(semver: str) -> str:
    cmd = f'source "{RPM_VERSION_SH}"; netllm_rpm_version "$1"'
    result = subprocess.run(
        ["bash", "-c", cmd, "_", semver],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_dev_prerelease_uses_dots_not_tilde() -> None:
    assert _rpm_version("0.0.0-dev") == "0.0.0.dev"


def test_rpm_version_never_contains_tilde() -> None:
    for semver in ("0.0.0-dev", "0.2.2.1", "0.2.2.1.ci", "1.0.0-rc.1"):
        mapped = _rpm_version(semver)
        assert "~" not in mapped, (
            f"unsafe tilde in RPM path for {semver!r} -> {mapped!r}"
        )


def test_tarball_path_does_not_tilde_expand_dev_prerelease() -> None:
    """Reproduce CI bug: ~ in paths became /home/runnerdev under bash."""
    script = """
    source "$1"
    VERSION=0.0.0-dev
    RPM_VERSION="$(netllm_rpm_version "$VERSION")"
    TOPDIR=/tmp/netllm-rpm-test
    path="${TOPDIR}/SOURCES/netllm-${RPM_VERSION}.tar.gz"
    printf '%s' "$path"
    """
    result = subprocess.run(
        ["bash", "-c", script, "_", str(RPM_VERSION_SH)],
        capture_output=True,
        text=True,
        check=True,
    )
    path = result.stdout
    assert "~" not in path
    assert "/home/" not in path
    assert path.endswith("netllm-0.0.0.dev.tar.gz")
