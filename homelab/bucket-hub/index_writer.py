"""Append-only download index (schema v1). Shared by Pi Hub and the worker."""
from __future__ import annotations

import fcntl
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger("bucket-hub.index_writer")

SCHEMA_VERSION = 1

CORE_FIELDS = (
    "schema_version",
    "entry_id",
    "kind",
    "source_id",
    "status",
    "source",
    "title",
    "description",
    "hub_tags",
    "topics",
    "size_bytes",
    "downloaded_at",
    "drive_id",
    "local_path",
    "rel_path",
    "source_url",
    "hub_job_id",
    "recurring_id",
    "context",
    "user_note",
    "ai_summary",
)

KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "torrent": (
        "release_name",
        "media_type",
        "show_title",
        "season_numbers",
        "year",
        "indexer",
        "infohash",
        "files_count",
        "video_files",
        "download_dir",
        "library_path",
        "media_pipeline",
    ),
}

_SECRET_SUBSTRINGS = (
    "apikey=",
    "jackett_apikey",
    "token=",
    "Bearer ",
)
_SECRET_PREFIX_RE = re.compile(
    r"(?:ghp_|github_pat_|hf_)[A-Za-z0-9]{20,}"
)
_VIDEO_SUFFIXES = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv")


class IndexSecretError(ValueError):
    """Raised when an index entry would store a secret."""


def redact_source_url(url: str | None) -> str | None:
    if not url:
        return url
    text = str(url)
    parsed = urlparse(text)
    if parsed.query:
        pairs = []
        for key, val in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() in {"apikey", "jackett_apikey", "token"}:
                pairs.append((key, "***"))
            else:
                pairs.append((key, val))
        text = urlunparse(parsed._replace(query=urlencode(pairs)))
    text = re.sub(r"(?i)(jackett_apikey|apikey)=([^&]+)", r"\1=***", text)
    return text


def secret_scan(entry: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
            return
        if isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
            return
        if value is None:
            return
        text = str(value)
        cleaned = re.sub(r"(?i)(jackett_apikey|apikey|token)=\*\*\*", "", text)
        low = cleaned.lower()
        for needle in _SECRET_SUBSTRINGS:
            if needle.lower() in low:
                raise IndexSecretError(f"index entry contains secret marker {needle!r}")
        if _SECRET_PREFIX_RE.search(cleaned):
            raise IndexSecretError("index entry contains a token-shaped secret")

    walk(entry)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_entry(kind: str, **fields: Any) -> dict[str, Any]:
    kind_s = str(kind or "unknown")
    entry: dict[str, Any] = {k: None for k in CORE_FIELDS}
    entry["schema_version"] = SCHEMA_VERSION
    entry["kind"] = kind_s
    entry["hub_tags"] = []
    entry["topics"] = []
    entry["status"] = "done"
    entry["source"] = "manual"
    entry["ai_summary"] = None
    entry["user_note"] = None
    for key in KIND_FIELDS.get(kind_s, ()):
        entry.setdefault(key, None)
    for key, val in fields.items():
        entry[key] = val
    if entry.get("source_url"):
        entry["source_url"] = redact_source_url(str(entry["source_url"]))
    if not entry.get("entry_id"):
        sid = entry.get("source_id") or "unknown"
        hid = entry.get("hub_job_id") or "unknown"
        entry["entry_id"] = f"{kind_s}:{sid}:{hid}"
    if not entry.get("downloaded_at"):
        entry["downloaded_at"] = _now()
    secret_scan(entry)
    return entry


def entry_from_job(job: dict[str, Any]) -> dict[str, Any]:
    kind = str(job.get("kind") or "").strip().lower() or "unknown"
    status = "failed" if job.get("status") == "error" else "done"
    notes = job.get("notes") or ""
    who = job.get("requested_by") or ""
    context_bits = [p for p in (notes, who) if p]
    return build_entry(
        kind,
        source_id=str(job.get("id") or "unknown"),
        hub_job_id=str(job.get("id") or ""),
        status=status,
        source=str(job.get("source") or "manual"),
        title=job.get("title"),
        description=job.get("notes"),
        hub_tags=list(job.get("tags") or []),
        drive_id=job.get("drive_id"),
        local_path=job.get("destination_path"),
        rel_path=job.get("download_dir") or job.get("folder_name"),
        source_url=job.get("url"),
        context=" · ".join(context_bits) if context_bits else None,
    )


def _media_type(job: dict[str, Any]) -> str:
    tags = {str(t).lower() for t in (job.get("tags") or [])}
    meta = job.get("torrent_meta") or {}
    if "tv" in tags or meta.get("show_title_guess") or meta.get("release_kind") in {
        "season",
        "episode",
        "full_show",
    }:
        return "tv"
    if "movie" in tags or "movies" in tags:
        return "movie"
    return "other"


def _video_files(files: list[str]) -> list[str]:
    out: list[str] = []
    for rel in files:
        name = str(rel)
        if name.lower().endswith(_VIDEO_SUFFIXES):
            out.append(name)
        if len(out) >= 200:
            break
    return out


def build_torrent_entry(
    job: dict[str, Any],
    download_dir: str | None,
    files: list[str] | None,
    media_pipeline: dict[str, Any] | None,
) -> dict[str, Any]:
    meta = job.get("torrent_meta") or {}
    files = list(files or [])
    title = str(job.get("title") or "")
    show = meta.get("show_title_guess") or None
    seasons = meta.get("season_numbers") or []
    notes = job.get("notes") or ""
    who = job.get("requested_by") or ""
    context_bits = [p for p in (notes, who) if p]
    library_path = None
    sonarr = (media_pipeline or {}).get("sonarr")
    if sonarr in ("added", "exists") and show:
        library_path = f"tv/{show}"
        if seasons:
            library_path += f"/Season {int(seasons[0])}"
    source_id = str(meta.get("infohash") or job.get("id") or "unknown")
    rel = download_dir
    local = None
    if download_dir:
        if download_dir.startswith("/"):
            local = download_dir
            if "/torrents/" in download_dir:
                rel = "torrents/" + download_dir.split("/torrents/", 1)[1]
            elif download_dir.rstrip("/").endswith("/torrents"):
                rel = "torrents"
        else:
            rel = download_dir
            local = f"/mnt/external/movies/{download_dir.lstrip('/')}"
    return build_entry(
        "torrent",
        source_id=source_id,
        hub_job_id=str(job.get("id") or ""),
        status="done",
        source=str(job.get("source") or "manual"),
        title=title,
        hub_tags=list(job.get("tags") or []),
        drive_id=job.get("drive_id"),
        local_path=local,
        rel_path=rel,
        source_url=job.get("url"),
        context=" · ".join(context_bits) if context_bits else None,
        release_name=title,
        media_type=_media_type(job),
        show_title=show,
        season_numbers=list(seasons) if seasons else None,
        year=meta.get("year"),
        indexer=meta.get("tracker") or meta.get("indexer"),
        infohash=meta.get("infohash"),
        files_count=len(files),
        video_files=_video_files(files),
        download_dir=rel,
        library_path=library_path,
        media_pipeline=media_pipeline,
        size_bytes=job.get("size_bytes"),
    )


def append_entry(path: str | Path, entry: dict[str, Any]) -> None:
    secret_scan(entry)
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with dest.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(line)
        fh.flush()
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def load_entries(path: str | Path) -> dict[str, dict[str, Any]]:
    dest = Path(path)
    out: dict[str, dict[str, Any]] = {}
    if not dest.is_file():
        return out
    with dest.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                log.warning("skipping corrupt index line %s in %s", i, dest)
                continue
            if not isinstance(row, dict):
                log.warning("skipping non-object index line %s in %s", i, dest)
                continue
            eid = str(row.get("entry_id") or f"line-{i}")
            out[eid] = row
    return out
