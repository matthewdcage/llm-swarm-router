"""Aria2 download destination rules — shared by Hub API (Pi) and ubuntu-worker.

Keep in sync with ``docker-compose.yml`` aria2 volume mounts on ai-server.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from pathlib import Path
from typing import Any

# p3terx/aria2-pro can shadow VOLUME /downloads with container storage — use a
# dedicated bind target on ai-server (see homelab docker-compose aria2 volumes).
ARIA2_TORRENTS_CONTAINER_DIR = "/mnt/torrents"

# Ordered longest-prefix wins when matching (not needed yet; prefixes are disjoint).
ARIA2_DESTINATION_RULES: tuple[dict[str, str], ...] = (
    {
        "id": "media_hdd",
        "label": "Media HDD (/mnt/media)",
        "host_prefix": "/mnt/media",
        "compose": "aria2: /mnt/media:/media",
    },
    {
        "id": "movies_usb",
        "label": "Expansion HDD movies (/mnt/external/movies)",
        "host_prefix": "/mnt/external/movies",
        "compose": f"aria2: /mnt/external/movies/torrents:{ARIA2_TORRENTS_CONTAINER_DIR}",
        "host_subdir": "torrents",
    },
    {
        "id": "tnas_staging",
        "label": "TNAS (staged via Media HDD)",
        "host_prefix": "/mnt/tnas",
        "compose": "aria2: /mnt/media:/media + post-copy from _nas_staging",
        "requires_mount": "/mnt/media",
    },
)


def _match_rule(dest: str) -> dict[str, str] | None:
    text = (dest or "").strip()
    if not text:
        return None
    for rule in ARIA2_DESTINATION_RULES:
        prefix = rule["host_prefix"]
        if text == prefix or text.startswith(prefix + "/"):
            return rule
    return None


def aria2_dir_for(dest: str) -> tuple[str, str | None]:
    """Map host destination_path to aria2 RPC ``dir`` (container path)."""
    rule = _match_rule(dest)
    if not rule:
        raise RuntimeError(f"unsupported destination_path: {dest}")
    prefix = rule["host_prefix"]
    if prefix == "/mnt/media":
        if dest.startswith("/mnt/media/"):
            return "/media/" + dest[len("/mnt/media/") :], None
        return "/media", None
    if prefix == "/mnt/external/movies":
        base = ARIA2_TORRENTS_CONTAINER_DIR
        rel = dest[len(prefix) :].lstrip("/")
        if not rel:
            return base, None
        if rel == "torrents":
            return base, None
        if rel.startswith("torrents/"):
            rel = rel[len("torrents/") :]
        if not rel:
            return base, None
        return f"{base}/{rel}", None
    if prefix == "/mnt/tnas":
        if not Path("/mnt/media").is_mount():
            raise RuntimeError("NAS staging needs Media HDD mounted")
        rel = dest[len("/mnt/tnas/") :]
        return f"/media/_nas_staging/{rel}", dest
    raise RuntimeError(f"unsupported destination_path: {dest}")


MOVIES_TORRENTS_HOST = "/mnt/external/movies/torrents"


def _norm_host_path(dest: str) -> str:
    return posixpath.normpath((dest or "").strip())


def _is_movies_torrents_inbox(dest: str) -> bool:
    text = (dest or "").strip()
    if not text:
        return False
    return text.rstrip("/") == MOVIES_TORRENTS_HOST or text.startswith(
        MOVIES_TORRENTS_HOST + "/"
    )


def aria2_dir_for_job(dest: str) -> tuple[str, str | None]:
    """Single source of truth for aria2 RPC ``dir``.

    Any destination equal to or under the Expansion HDD torrents inbox uses the
    flat container dir ``/mnt/torrents``. Traversal out of that inbox raises
    ``ValueError``. Other dests match ``aria2_dir_for``.
    """
    text = (dest or "").strip()
    if _is_movies_torrents_inbox(text):
        norm = _norm_host_path(text)
        if not (norm == MOVIES_TORRENTS_HOST or norm.startswith(MOVIES_TORRENTS_HOST + "/")):
            raise ValueError(f"destination escapes torrents inbox: {dest}")
        return ARIA2_TORRENTS_CONTAINER_DIR, None
    return aria2_dir_for(text)


def aria2_opts_for_job(job: dict[str, Any]) -> dict[str, str]:
    """Begin / resume / re-add options; ``dir`` is always ``aria2_dir_for_job``."""
    dest = str(job.get("destination_path") or "")
    aria_dir, _post = aria2_dir_for_job(dest) if dest else ("", None)
    return {
        "dir": aria_dir,
        "file-allocation": "none",
        "bt-metadata-only": "false",
    }


def metadata_retry_scan_dir(job: dict[str, Any], aria2_host_dir: Path | str | None) -> Path:
    """Scan the real release folder, never a Hub-invented Show/Season dest."""
    dest = str(job.get("destination_path") or "")
    if aria2_host_dir:
        host = Path(aria2_host_dir)
        if _is_movies_torrents_inbox(dest):
            return host
        return host
    return Path(dest)


def host_dirs_to_ensure(dest: str) -> list[Path]:
    """Directories the worker may mkdir. Nested torrents inbox dests stay flat."""
    text = (dest or "").strip()
    if _is_movies_torrents_inbox(text):
        return [Path(MOVIES_TORRENTS_HOST)]
    if text.startswith("/mnt/tnas"):
        rel = text[len("/mnt/tnas/") :]
        return [Path("/mnt/media/_nas_staging") / rel]
    if text:
        return [Path(text)]
    return []


def validate_aria2_destination(
    dest: str,
    *,
    mount_ok: Callable[[str], bool] | None = None,
) -> str | None:
    """Return a user-facing error, or None if the path is allowed for aria2 jobs."""
    text = (dest or "").strip()
    if not text:
        return "Destination path is empty"
    rule = _match_rule(text)
    if not rule:
        allowed = ", ".join(r["host_prefix"] for r in ARIA2_DESTINATION_RULES)
        return (
            f"Worker cannot download to {text!r}. "
            f"Torrents and direct HTTP downloads only support: {allowed}. "
            "Pick Movies (Expansion HDD), Media HDD, or a TNAS folder — or fix aria2 "
            "compose mounts and download_destinations.py on the worker."
        )
    req = rule.get("requires_mount")
    if req:
        mounted = mount_ok(req) if mount_ok else Path(req).is_mount()
        if not mounted:
            return (
                f"Destination {text!r} needs {req} mounted on ai-server "
                f"({rule.get('compose', '')})."
            )
    return None


def aria2_destination_catalog() -> list[dict[str, str]]:
    """Static catalog for Hub UI and audit docs."""
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "host_prefix": r["host_prefix"],
            "compose": r["compose"],
            **({"requires_mount": r["requires_mount"]} if r.get("requires_mount") else {}),
            **({"host_subdir": r["host_subdir"]} if r.get("host_subdir") else {}),
        }
        for r in ARIA2_DESTINATION_RULES
    ]
