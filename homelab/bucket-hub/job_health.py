"""Detect stuck Hub download jobs and build agent-review payloads."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

METADATA_STUCK_MINUTES = int(os.environ.get("HUB_METADATA_STUCK_MINUTES", "45"))
NO_PROGRESS_MINUTES = int(os.environ.get("HUB_NO_PROGRESS_STUCK_MINUTES", "90"))
STALE_COMPLETE_MINUTES = int(os.environ.get("HUB_STALE_COMPLETE_MINUTES", "20"))

DOC_REFS = [
    "homelab/bucket-hub/docs/HUB-DOWNLOAD-DESTINATIONS.md",
    "homelab/bucket-hub/docs/JOB-HEALTH-AND-AGENT-REVIEW.md",
]


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _age_minutes(since: datetime | None, now: datetime) -> float | None:
    if since is None:
        return None
    return (now - since).total_seconds() / 60.0


def _int_field(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _is_aria2ish(job: dict[str, Any]) -> bool:
    if job.get("worker") == "aria2":
        return True
    kind = str(job.get("kind") or "").lower()
    return kind in ("magnet", "http")


def _metadata_phase(job: dict[str, Any]) -> bool:
    prog = job.get("progress") or {}
    phase = str(prog.get("phase") or "").lower()
    if phase == "metadata" or prog.get("metadata_only"):
        return True
    if _int_field(prog.get("total")) == 0 and job.get("aria2_gid"):
        return True
    return False


def evaluate_job_health(
    job: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return an agent_review dict if the job looks stuck; else None."""
    if not job:
        return None
    status = str(job.get("status") or "")
    if status not in ("running", "claimed"):
        return None
    if not _is_aria2ish(job):
        return None

    now = now or datetime.now(timezone.utc)
    prog = job.get("progress") or {}
    started = _parse_iso(job.get("started_at")) or _parse_iso(job.get("created_at"))
    age_m = _age_minutes(started, now)
    if age_m is None:
        return None

    completed = _int_field(prog.get("completed"))
    total = _int_field(prog.get("total"))
    seeders = _int_field(prog.get("seeders"))
    connections = _int_field(prog.get("connections"))
    title = str(job.get("title") or job.get("id") or "job")

    if _metadata_phase(job):
        if age_m >= METADATA_STUCK_MINUTES and connections == 0 and seeders == 0:
            return _review(
                code="metadata_stuck",
                title="Metadata stall (no peers)",
                summary=(
                    f"“{title}” has been fetching torrent metadata for "
                    f"{int(age_m)}+ minutes with 0 seeders and 0 connections."
                ),
                evidence={
                    "age_minutes": round(age_m, 1),
                    "phase": "metadata",
                    "seeders": seeders,
                    "connections": connections,
                    "aria2_gid": job.get("aria2_gid"),
                    "url_kind": job.get("kind"),
                },
                playbook=_metadata_playbook(job),
            )

    if total > 0 and completed >= total and age_m >= STALE_COMPLETE_MINUTES:
        return _review(
            code="stale_running_complete",
            title="Download complete but job still running",
            summary=(
                f"“{title}” reports {completed}/{total} bytes but status is still "
                f"“{status}” for {int(age_m)}+ minutes."
            ),
            evidence={
                "completed": completed,
                "total": total,
                "age_minutes": round(age_m, 1),
                "media_pipeline": job.get("media_pipeline"),
            },
            playbook=_complete_stuck_playbook(job),
        )

    watch = job.get("progress_watch") or {}
    stall_since = _parse_iso(watch.get("stall_since"))
    if (
        total > 0
        and completed > 0
        and completed < total
        and stall_since is not None
    ):
        stall_m = _age_minutes(stall_since, now)
        if stall_m is not None and stall_m >= NO_PROGRESS_MINUTES:
            return _review(
                code="no_progress",
                title="Download stalled (no byte progress)",
                summary=(
                    f"“{title}” has made no progress for {int(stall_m)}+ minutes "
                    f"({completed}/{total} bytes)."
                ),
                evidence={
                    "stall_minutes": round(stall_m, 1),
                    "completed": completed,
                    "total": total,
                    "seeders": seeders,
                    "connections": connections,
                },
                playbook=_stall_playbook(job),
            )

    pipeline = job.get("media_pipeline") or {}
    if pipeline.get("sonarr") == "add_failed" and status == "running":
        return _review(
            code="sonarr_import_failed",
            title="Sonarr import did not run",
            summary=(
                f"“{title}” finished downloading but Sonarr ensure/scan failed "
                "(worker could not reach Sonarr API on the host)."
            ),
            evidence={"media_pipeline": pipeline},
            playbook=_sonarr_playbook(),
        )

    return None


def update_progress_watch(
    job: dict[str, Any],
    progress: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Track when completed bytes last changed (for no_progress detection)."""
    now = now or datetime.now(timezone.utc)
    completed = _int_field(progress.get("completed"))
    prev = job.get("progress_watch") or {}
    prev_completed = _int_field(prev.get("completed"))
    if completed != prev_completed:
        return {
            "completed": completed,
            "stall_since": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    stall_since = prev.get("stall_since") or now.isoformat()
    return {
        "completed": completed,
        "stall_since": stall_since,
        "updated_at": now.isoformat(),
    }


def agent_review_markdown(job: dict[str, Any]) -> str:
    """Render a brief for Cursor / overnight agents."""
    ar = job.get("agent_review") or {}
    if not ar:
        return ""
    lines = [
        f"# Hub job agent review: {job.get('id')}",
        "",
        f"**Title:** {job.get('title', '')}",
        f"**Status:** {job.get('status')}",
        f"**Flagged:** {ar.get('flagged_at', '')}",
        f"**Code:** `{ar.get('code', '')}`",
        "",
        ar.get("summary", ""),
        "",
        "## Evidence",
        "```json",
    ]
    import json

    lines.append(json.dumps(ar.get("evidence") or {}, indent=2))
    lines.extend(["```", "", "## Suggested steps"])
    for step in ar.get("playbook") or []:
        lines.append(f"- {step}")
    lines.extend(["", "## Doc refs"])
    for ref in ar.get("doc_refs") or DOC_REFS:
        lines.append(f"- `{ref}`")
    lines.append("")
    return "\n".join(lines)


def _review(
    *,
    code: str,
    title: str,
    summary: str,
    evidence: dict[str, Any],
    playbook: list[str],
) -> dict[str, Any]:
    return {
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "code": code,
        "title": title,
        "summary": summary,
        "evidence": evidence,
        "playbook": playbook,
        "doc_refs": DOC_REFS,
    }


def _metadata_playbook(job: dict[str, Any]) -> list[str]:
    kind = str(job.get("kind") or "")
    steps = [
        "Check Jackett for the same release with seeders and a `/dl/` torrent URL "
        "(not magnet-only).",
        "Cancel this job and re-queue via Jackett HTTP `.torrent` if magnet metadata "
        "never finds peers.",
        "Confirm Gluetun VPN is live (Hub VPN badge) and aria2 has active downloads "
        "on ai-server.",
        "See `docs/HUB-DOWNLOAD-DESTINATIONS.md` (magnet metadata vs `followedBy`).",
    ]
    if kind == "magnet":
        steps.insert(0, "Magnet-only swarm may be dead — prefer Jackett torrent file.")
    return steps


def _stall_playbook(job: dict[str, Any]) -> list[str]:
    return [
        "On ai-server: `aria2.tellStatus` for the job `aria2_gid` (seeders, connections).",
        "If VPN dropped, wait for Gluetun or pause and resume when VPN is live.",
        "If many parallel torrents: pause others to free bandwidth (Hub Pause).",
        "If aria2 RPC times out, check hub-worker logs and restart worker.",
    ]


def _complete_stuck_playbook(job: dict[str, Any]) -> list[str]:
    return [
        "Worker may be waiting on Sonarr/Jellyfin pipeline — check `media_pipeline` on the job.",
        "Run Sonarr **Downloaded Episodes Scan** on the torrent folder under `/data/torrents/`.",
        "Mark job done or retry from Queue → History if aria2 already removed the gid.",
    ]


def _sonarr_playbook() -> list[str]:
    return [
        "Call Sonarr API from inside the Gluetun network (`docker exec sonarr curl …`).",
        "Set `SONARR_URL=http://127.0.0.1:8989` in `hub-worker.env` (not host LAN IP).",
        "After import, trigger Jellyfin library refresh.",
    ]
