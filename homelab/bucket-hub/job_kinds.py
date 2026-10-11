"""Shared job-kind routing. A missing kind must never be treated as a torrent."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("bucket-hub.job_kinds")

ARIA2_KINDS = frozenset({"http", "magnet"})
VIDEO_KINDS = frozenset({"video"})
VIDEO_WORKERS = frozenset({"metube"})
FUTURE_REPO_KINDS = frozenset(
    {"github", "hf", "github_repo", "huggingface_repo", "github_release"}
)


def is_aria2_job(job: dict[str, Any]) -> bool:
    """True only when worker is aria2, or worker is missing and kind is http/magnet."""
    worker = str(job.get("worker") or "").strip()
    if worker == "aria2":
        return True
    if worker:
        return False
    kind = str(job.get("kind") or "").strip().lower()
    if not kind:
        log.warning("job %s has neither worker nor kind; not treating as aria2", job.get("id"))
        return False
    return kind in ARIA2_KINDS


def dispatch_route(job: dict[str, Any]) -> str:
    """Return ``aria2``, ``video`` or ``process``. Kindless jobs never return aria2."""
    if is_aria2_job(job):
        return "aria2"
    kind = str(job.get("kind") or "").strip().lower()
    worker = str(job.get("worker") or "").strip()
    if kind in VIDEO_KINDS or worker in VIDEO_WORKERS:
        return "video"
    return "process"
