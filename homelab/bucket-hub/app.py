#!/usr/bin/env python3
"""Bucket-HUB download form API (v2)."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

import llm_ctl
import machines_ctl
from download_destinations import aria2_destination_catalog, validate_aria2_destination
from torrent_indexer_catalog import normalize_hub_category
from torrent_indexer_urls import (
    attach_candidates_to_indexers,
    load_candidates_store,
    mirror_urls_to_check,
    parse_reference_url_entries,
    validate_public_http_url,
)

from job_health import (
    agent_review_markdown,
    evaluate_job_health,
    update_progress_watch,
)

JOBS_DIR = Path(os.environ.get("JOBS_DIR", "/data/hub-jobs"))
AGENT_REVIEW_DIR = JOBS_DIR / "agent-review"
JOBS_SUB = JOBS_DIR / "jobs"
LIBRARY_FILE = JOBS_DIR / "library.json"
DEFAULT_LIBRARY = Path(__file__).with_name("library.default.json")
FORM_PATH = Path(__file__).with_name("form.html")
CAPTURE_PATH = Path(__file__).with_name("capture.html")
STORAGE_PATH = Path(__file__).with_name("storage.html")
INDEXERS_PATH = Path(__file__).with_name("indexers.html")
TORRENT_INDEXERS_CATALOG = Path(__file__).with_name("torrent-indexers-catalog.json")
INDEXERS_STATUS_FILE = JOBS_DIR / "torrent-indexers.status.json"
INDEXERS_SCAN_REQUEST_FILE = JOBS_DIR / "torrent-indexers-scan.request"
INDEXERS_CANDIDATES_FILE = JOBS_DIR / "torrent-indexers.candidates.json"
INDEXERS_CANDIDATES_STATUS_FILE = JOBS_DIR / "torrent-indexers.candidates.status.json"
INDEXERS_CANDIDATES_CHECK_REQUEST_FILE = (
    JOBS_DIR / "torrent-indexers-candidates-check.request"
)
CHAT_PATH = Path(__file__).with_name("chat.html")
OVERNIGHT_PATH = Path(__file__).with_name("overnight.html")
OVERNIGHT_JS_PATH = Path(__file__).with_name("hub-overnight.js")
HUB_DESIGN_CSS_PATH = Path(__file__).with_name("hub-design.css")
DEFAULT_OVERNIGHT_SCHEDULE = Path(__file__).with_name("overnight-schedule.default.json")
OVERNIGHT_SCHEDULE_FILE = JOBS_DIR / "overnight-schedule.json"
OVERNIGHT_STATE_FILE = JOBS_DIR / "overnight-run-state.json"
OVERNIGHT_RUNS_DIR = JOBS_DIR / "overnight-runs"
VENDOR_DIR = Path(__file__).with_name("vendor")
REQUEST_TEMPLATE = Path(__file__).with_name("request.html")
SUBMITTERS_DEFAULT = Path(__file__).with_name("submitters.default.json")
SUBMITTERS_FILE = Path(
    os.environ.get("SUBMITTERS_FILE", str(Path(__file__).with_name("submitters.json")))
)
WORKER_TOKEN = os.environ.get("HUB_WORKER_TOKEN", "").strip()
HUB_MAX_CONCURRENT_ARIA2 = max(
    1, int(os.environ.get("HUB_MAX_CONCURRENT_ARIA2", "16"))
)
HUB_SLOT_VIDEO = max(0, int(os.environ.get("HUB_SLOT_VIDEO", "1")))
HUB_SLOT_ARCHIVE = max(0, int(os.environ.get("HUB_SLOT_ARCHIVE", "0")))
# Firefox capture: static token and/or minted tokens under hub-jobs
HUB_CAPTURE_TOKEN = os.environ.get("HUB_CAPTURE_TOKEN", "").strip()
CAPTURE_TOKENS_FILE = JOBS_DIR / "capture-tokens.json"
CAPTURE_FOLDERS_FILE = JOBS_DIR / "capture-folders.json"
SEARCH_PROVIDERS_FILE = JOBS_DIR / "search-providers.json"
DEFAULT_SEARCH_PROVIDERS = Path(__file__).with_name("search-providers.json")
# Hub-owned file-type → folder defaults (Firefox Auto / empty drive+folder).
DEFAULT_TYPE_FOLDER_MAP: dict[str, dict[str, str]] = {
    "media": {"drive_id": "nas", "folder_id": "media_archive"},
    "video": {"drive_id": "nas", "folder_id": "media_archive"},
    "website": {"drive_id": "nas", "folder_id": "inbox"},
    "github": {"drive_id": "nas", "folder_id": "inbox"},
    "hf": {"drive_id": "nas", "folder_id": "ai_language"},
    "http": {"drive_id": "nas", "folder_id": "inbox"},
    "magnet": {"drive_id": "nas", "folder_id": "inbox"},
    "default": {"drive_id": "nas", "folder_id": "inbox"},
}
WORKER_STATUS_FILE = JOBS_DIR / "worker-status.json"
SCAN_REQUEST_FILE = JOBS_DIR / "scan-drives.request"
SCAN_RESULT_FILE = JOBS_DIR / "scan-drives.result"
ARCHIVE_PROBE_REQUEST_FILE = JOBS_DIR / "archive-probe.request"
ARCHIVE_PROBE_RESULT_FILE = JOBS_DIR / "archive-probe.result"
SEARCH_REQUEST_FILE = JOBS_DIR / "search.request"
SEARCH_RESULT_FILE = JOBS_DIR / "search.result"
RESEARCH_REQUEST_FILE = JOBS_DIR / "research.request"
RESEARCH_RESULT_FILE = JOBS_DIR / "research.result"
RESEARCH_CACHE_FILE = JOBS_DIR / "research-cache.json"
DEFAULT_RESEARCH_PRESETS = Path(__file__).with_name("research-presets.json")
HF_FILES_REQUEST_FILE = JOBS_DIR / "hf-files.request"
HF_FILES_RESULT_FILE = JOBS_DIR / "hf-files.result"
STORAGE_SCAN_REQUEST_FILE = JOBS_DIR / "storage-scan.request"
STORAGE_SCAN_STATUS_FILE = JOBS_DIR / "storage-scan.status"
STORAGE_TREES_DIR = JOBS_DIR / "storage-trees"
STORAGE_VOLUME_ROOTS: dict[str, str] = {
    "media": "/mnt/media",
    "tnas": "/mnt/tnas",
    "usb": "/mnt/external",
}
STORAGE_ALLOWLIST_PREFIXES = (
    "/mnt/media",
    "/mnt/tnas",
    "/mnt/external",
)
SEARCH_RELAY_TIMEOUT_S = float(os.environ.get("HUB_SEARCH_RELAY_TIMEOUT_S", "35"))
TORRENT_SEARCH_RELAY_TIMEOUT_S = float(
    os.environ.get("HUB_TORRENT_SEARCH_TIMEOUT_S", "120")
)
MACHINES_FILE = JOBS_DIR / "machines.json"
MACHINES_STATE_FILE = JOBS_DIR / "machines-state.json"
MACHINES_SSH_IDENTITY = os.environ.get("MACHINES_SSH_IDENTITY", "").strip() or None
MC_MANAGER_URL = os.environ.get("MC_MANAGER_URL", "http://100.112.168.40:3030").rstrip("/")
MC_MANAGER_TOKEN = os.environ.get("MC_MANAGER_TOKEN", "").strip()
HUB_TZ = ZoneInfo(os.environ.get("HUB_TZ", "Australia/Brisbane"))

machines_ctl.set_paths(MACHINES_FILE, MACHINES_STATE_FILE)

app = FastAPI(title="Bucket-HUB Requests", docs_url=None, redoc_url=None)


@app.middleware("http")
async def capture_cors(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Allow Firefox extension origins to call capture APIs."""
    origin = request.headers.get("origin") or ""
    is_ext = origin.startswith("moz-extension://")
    if request.method == "OPTIONS" and request.url.path.startswith("/api/capture"):
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Capture-Token",
            "Access-Control-Max-Age": "86400",
        }
        if is_ext:
            headers["Access-Control-Allow-Origin"] = origin
        return Response(status_code=204, headers=headers)
    response = await call_next(request)
    if is_ext and request.url.path.startswith("/api/capture"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Capture-Token"
        )
    return response


ERROR_HINTS: dict[str, str] = {
    "/mnt/media not mounted": "Media HDD is not mounted on the worker",
    "Media HDD is not mounted": "Media HDD is not mounted on the worker",
    "/mnt/tnas not mounted": "NAS is not mounted on the worker",
    "NAS is not mounted": "NAS is not mounted on the worker",
    "NAS staging needs Media HDD": "NAS staging needs Media HDD mounted",
    "Block device /dev/sda": "Media HDD not detected — reseat USB / power",
    "worker v1 only handles": "This job type is not supported yet",
    "Sign in to confirm": "YouTube blocked VPN IP — export cookies or use a direct video URL",
    "No video formats found": "YouTube blocked VPN IP — export cookies or use a direct video URL",
    "max-file-not-found": "Torrent: files not found on peers",
    "aria2 removed": "Download removed from aria2",
    "VPN required": "VPN/Proton is down — downloads blocked (fail-closed)",
    "JACKETT_API_KEY": "Torrent search needs Jackett API key on the worker",
    "YouTube cookies missing": "YouTube cookies missing — export cookies.txt on the worker (Proton)",
    "Sign in to confirm you’re not a bot": "YouTube bot check — re-export fresh cookies.txt",
    "Sign in to confirm you're not a bot": "YouTube bot check — re-export fresh cookies.txt",
    "Connection refused": "Worker lost contact with the Hub API (Pi hub-form) — download may still run on ai-server; use Retry to reattach",
    "Errno 111": "Worker lost contact with the Hub API (Pi hub-form) — download may still run on ai-server; use Retry to reattach",
}


def _human_error(raw: str | None, job: dict[str, Any] | None = None) -> str | None:
    if not raw:
        return None
    text = str(raw)
    low = text.strip().lower()
    if low in ("timed out", "timeout") and job:
        prog = job.get("progress") or {}
        try:
            done = int(prog.get("completed") or 0)
            total = int(prog.get("total") or 0)
        except (TypeError, ValueError):
            done, total = 0, 0
        if total > 0 and done > 0:
            pct = round(done * 100 / total)
            gb_done = done / 1_000_000_000
            gb_total = total / 1_000_000_000
            return (
                f"Worker stopped at {pct}% ({gb_done:.1f} / {gb_total:.1f} GB) — "
                "not finished; partial data may still be on the Expansion HDD / aria2. "
                "Use Queue → History → Retry (or re-queue with a show folder under torrents/)."
            )
        return (
            "Worker stopped waiting (timeout) — if this was a large torrent, "
            "check aria2 on ai-server or retry from History."
        )
    for key, hint in ERROR_HINTS.items():
        if key in text:
            return hint
    # Collapse noisy subprocess dumps
    if text.startswith("Command '[") or text.startswith("Command ["):
        low = text.lower()
        if "yt-dlp" in low or "youtube" in low or "metube" in low:
            return "Video download failed (yt-dlp / Proton)"
        if "gluetun" in low:
            return "Worker command failed via Gluetun"
        return "Worker command failed"
    if len(text) > 120:
        return text[:117] + "..."
    return text


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    """Copy job with a display-friendly error field for Hub UI."""
    out = dict(job)
    out["error_display"] = _human_error(job.get("error"), job)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mc_proxy(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> Any:
    if not MC_MANAGER_TOKEN:
        raise HTTPException(503, "Minecraft manager not configured on Hub")
    url = f"{MC_MANAGER_URL}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "X-Mc-Token": MC_MANAGER_TOKEN,
        "User-Agent": "bucket-hub-form/2.0",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = URLRequest(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(exc.code, str(detail)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Minecraft manager unreachable: {exc}") from exc


def _ensure_dirs() -> None:
    JOBS_SUB.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    if not LIBRARY_FILE.exists():
        LIBRARY_FILE.write_text(DEFAULT_LIBRARY.read_text(encoding="utf-8"), encoding="utf-8")


def _load_library() -> dict[str, Any]:
    _ensure_dirs()
    return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))


def _save_library(data: dict[str, Any]) -> None:
    LIBRARY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _folder_key(drive_id: str, folder_id: str) -> str:
    return f"{drive_id}/{folder_id}"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return s[:48] or uuid.uuid4().hex[:8]


def _ensure_submitters() -> None:
    if not SUBMITTERS_FILE.exists() and SUBMITTERS_DEFAULT.exists():
        SUBMITTERS_FILE.write_text(
            SUBMITTERS_DEFAULT.read_text(encoding="utf-8"), encoding="utf-8"
        )


def _load_submitters_config() -> dict[str, Any]:
    _ensure_submitters()
    if not SUBMITTERS_FILE.exists():
        return {"version": 1, "submitters": {}}
    return json.loads(SUBMITTERS_FILE.read_text(encoding="utf-8"))


def _resolve_submitter_slug(slug: str) -> str | None:
    slug = slug.strip().lower()
    submitters = _load_submitters_config().get("submitters", {})
    if slug in submitters:
        return slug
    for sid, scfg in submitters.items():
        aliases = [a.lower() for a in scfg.get("aliases") or []]
        if slug in aliases:
            return sid
    return None


def _submitter_cfg(slug: str) -> dict[str, Any]:
    canonical = _resolve_submitter_slug(slug)
    if not canonical:
        raise HTTPException(404, "Unknown request page")
    return _load_submitters_config()["submitters"][canonical]


def _verify_submitter_token(scfg: dict[str, Any], token: str | None) -> None:
    env_name = (scfg.get("token_env") or "").strip()
    if not env_name:
        return
    expected = os.environ.get(env_name, "").strip()
    if not expected:
        return
    if not token or token != expected:
        raise HTTPException(401, "Invalid or missing access key")


def _parse_submitter_text(text: str) -> tuple[str | None, str, str]:
    """Return url (or None), kind, title."""
    raw = text.strip()
    if not raw:
        raise ValueError("Request is empty")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    first = lines[0]
    extra = "\n".join(lines[1:]).strip()

    def with_scheme(value: str) -> str:
        if re.match(r"^https?://", value, re.I) or value.startswith("magnet:"):
            return value
        if any(x in value.lower() for x in ("youtube.com", "youtu.be", "github.com", "huggingface.co")):
            return "https://" + value.lstrip("/")
        return value

    if first.lower().startswith("magnet:"):
        return first, "magnet", extra[:80]
    if re.match(r"^https?://", first, re.I):
        url = first
        return url, _detect_kind(url, ""), (extra or "")[:80]
    lowered = first.lower()
    if any(x in lowered for x in ("youtube.com", "youtu.be", "github.com", "huggingface.co")):
        url = with_scheme(first)
        return url, _detect_kind(url, ""), (extra or "")[:80]
    title = raw if len(raw) <= 80 else raw[:77] + "..."
    return None, "request", title


def _job_high_priority_tier(job: dict[str, Any]) -> int:
    return 0 if (job.get("options") or {}).get("high_priority") else 1


def _job_queue_claim_sort_key(job: dict[str, Any]) -> tuple[Any, ...]:
    """Lower key = claimed sooner. Matches Hub UI queue order for status=queued."""
    high = _job_high_priority_tier(job)
    rank = job.get("queue_rank")
    created = str(job.get("created_at") or "")
    jid = str(job.get("id") or "")
    if isinstance(rank, (int, float)):
        return (high, 0, float(rank), created, jid)
    return (high, 1, created, jid)


def _next_queue_rank() -> int:
    max_rank = 0
    for job in _load_all_jobs():
        if job.get("status") != "queued":
            continue
        rank = job.get("queue_rank")
        if isinstance(rank, (int, float)):
            max_rank = max(max_rank, int(rank))
    return max_rank + 10


def _assign_queue_rank_if_queued(job: dict[str, Any]) -> None:
    if job.get("status") == "queued" and job.get("queue_rank") is None:
        job["queue_rank"] = _next_queue_rank()


def _enqueue_job(job: dict[str, Any]) -> dict[str, Any]:
    job_id = job.get("id") or uuid.uuid4().hex[:12]
    job["id"] = job_id
    job.setdefault("created_at", _now())
    job.setdefault("status", "queued")
    job.setdefault("error", None)
    job.setdefault("started_at", None)
    job.setdefault("finished_at", None)
    _ensure_dirs()
    _assign_queue_rank_if_queued(job)
    (JOBS_SUB / f"{job_id}.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    if (job.get("options") or {}).get("high_priority"):
        _auto_wake_worker_if_needed(job_id)
    return job


def _auto_wake_worker_if_needed(job_id: str) -> None:
    """Auto-wake ai-server via WoL when a high-priority job is queued and the worker appears asleep."""
    try:
        ws: dict[str, Any] = {}
        if WORKER_STATUS_FILE.exists():
            ws = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
        at = ws.get("at") or ""
        if not at:
            return
        ts = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        if age_s < 120:
            return
        machines_ctl.wake_machine("ubuntu")
        print(f"[auto-wake] Sent WoL to ai-server for high-priority job {job_id} (heartbeat {age_s:.0f}s old)", flush=True)
    except Exception as exc:
        print(f"[auto-wake] Failed: {exc}", flush=True)


def _resolve_submitter_destination(scfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
    lib = _load_library()
    drive_id = scfg.get("default_drive_id", "media_hdd")
    folder_id = scfg.get("default_folder_id", "movies")
    drive = lib.get("drives", {}).get(drive_id)
    if not drive:
        raise HTTPException(503, "Default drive not configured")
    folder = next((f for f in drive.get("folders", []) if f["id"] == folder_id), None)
    if not folder:
        folder = next((f for f in drive.get("folders", []) if f.get("present") is not False), None)
    if not folder:
        raise HTTPException(503, "Default folder not available")
    return drive, folder, _folder_abs_path(drive, folder)


class DiscoveredFolder(BaseModel):
    rel: str
    name: str


class DiscoveredDrive(BaseModel):
    id: str
    label: str = ""
    root: str = ""
    mounted: bool = False
    removable: bool = False
    free_bytes: int | None = None
    folders: list[DiscoveredFolder] = Field(default_factory=list)
    folders_reliable: bool = True


def _folder_abs_path(drive: dict[str, Any], folder: dict[str, Any]) -> Path:
    rel = folder.get("rel", "")
    if rel in (".", ""):
        return Path(drive["root"])
    return Path(drive["root"]) / rel


def _merge_library_from_discovery(drives: list[DiscoveredDrive]) -> bool:
    """Apply worker filesystem scan to library.json (preserve ids + tags)."""
    if not drives:
        return False
    lib = _load_library()
    lib.setdefault("drives", {})
    changed = False
    discovered_ids = {dd.id for dd in drives}
    for dd in drives:
        drive = lib["drives"].get(dd.id)
        if not drive:
            drive = {
                "label": dd.label,
                "root": dd.root,
                "removable": dd.removable,
                "folders": [],
            }
            lib["drives"][dd.id] = drive
            changed = True
        if drive.get("label") != dd.label and dd.label:
            drive["label"] = dd.label
            changed = True
        if drive.get("root") != dd.root and dd.root:
            drive["root"] = dd.root
            changed = True
        if drive.get("removable") != dd.removable:
            drive["removable"] = dd.removable
            changed = True
        if drive.get("mounted") != dd.mounted:
            drive["mounted"] = dd.mounted
            changed = True
        if dd.free_bytes is not None:
            free_gb = round(dd.free_bytes / (1024**3), 1)
            if drive.get("free_gb") != free_gb:
                drive["free_gb"] = free_gb
                changed = True
        if not dd.folders_reliable:
            # Keep prior folder list when the worker hit I/O errors mid-scan.
            continue
        discovered_rels = {f.rel for f in dd.folders}
        by_rel = {f["rel"]: f for f in drive.get("folders", [])}
        for df in dd.folders:
            if df.rel in by_rel:
                if by_rel[df.rel].get("present") is not True:
                    by_rel[df.rel]["present"] = True
                    changed = True
            else:
                fid = _slug(df.rel if df.rel != "." else "root")
                existing_ids = {f["id"] for f in drive.get("folders", [])}
                if fid in existing_ids:
                    fid = f"{fid}_{uuid.uuid4().hex[:4]}"
                folder = {"id": fid, "name": df.name, "rel": df.rel, "present": True}
                drive.setdefault("folders", []).append(folder)
                by_rel[df.rel] = folder
                changed = True
        for folder in drive.get("folders", []):
            present = folder["rel"] in discovered_rels if dd.mounted else False
            if folder.get("present") is not present:
                folder["present"] = present
                changed = True
    # Prune stale removable drives: mark undiscovered ones offline, and
    # remove duplicates that share the same root as a discovered drive.
    discovered_roots = {dd.root for dd in drives if dd.root}
    prune_ids: list[str] = []
    for did, drive in lib.get("drives", {}).items():
        if not drive.get("removable") or did in discovered_ids:
            continue
        # If another discovered drive owns this root, this is a stale
        # duplicate (e.g. old UUID-based ID replaced by mountpoint-based ID).
        if drive.get("root") in discovered_roots:
            prune_ids.append(did)
            continue
        if drive.get("mounted") is not False:
            drive["mounted"] = False
            changed = True
        for folder in drive.get("folders", []):
            if folder.get("present") is not False:
                folder["present"] = False
                changed = True
    for did in prune_ids:
        del lib["drives"][did]
        changed = True
        print(f"pruned stale duplicate drive {did}", flush=True)
    if changed:
        _save_library(lib)
    return changed


def _http_json(url: str, timeout: float = 20.0) -> Any:
    req = URLRequest(url, headers={"User-Agent": "Bucket-HUB/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@app.on_event("startup")
def startup() -> None:
    _ensure_dirs()
    _ensure_submitters()


@app.get("/api/library")
def get_library() -> dict[str, Any]:
    return _load_library()


@app.post("/api/library/scan")
def request_drive_scan() -> dict[str, Any]:
    """Ask the Ubuntu worker to remount USB / rescan drives on the next poll."""
    _ensure_dirs()
    payload = {"requested_at": _now(), "status": "pending"}
    SCAN_REQUEST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if SCAN_RESULT_FILE.exists():
        SCAN_RESULT_FILE.unlink(missing_ok=True)
    return {"ok": True, "pending": True, "requested_at": payload["requested_at"]}


@app.get("/api/library/scan/pending")
def scan_pending(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_worker(x_worker_token)
    pending = SCAN_REQUEST_FILE.exists()
    return {"pending": pending}


@app.post("/api/library/scan/complete")
def scan_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    result = {
        "ok": bool(body.get("ok", True)),
        "detail": body.get("detail") or "",
        "drive_ids": body.get("drive_ids") or [],
        "at": body.get("at") or _now(),
    }
    SCAN_RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    SCAN_REQUEST_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/library/scan/status")
def scan_status() -> dict[str, Any]:
    pending = SCAN_REQUEST_FILE.exists()
    result: dict[str, Any] = {}
    if SCAN_RESULT_FILE.exists():
        try:
            result = json.loads(SCAN_RESULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            result = {}
    return {"pending": pending, "result": result}


class ArchiveProbeIn(BaseModel):
    url: str = Field(min_length=8)

    @field_validator("url")
    @classmethod
    def strip_http_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


@app.post("/api/archive/probe")
def archive_probe(body: ArchiveProbeIn) -> dict[str, Any]:
    """Ask the Ubuntu worker to probe archive engine (httrack vs FlareSolverr). Blocks until result."""
    _ensure_dirs()
    if ARCHIVE_PROBE_RESULT_FILE.exists():
        ARCHIVE_PROBE_RESULT_FILE.unlink(missing_ok=True)
    ARCHIVE_PROBE_REQUEST_FILE.write_text(
        json.dumps({"url": body.url, "requested_at": _now()}, indent=2),
        encoding="utf-8",
    )
    deadline = time.time() + 45.0
    while time.time() < deadline:
        if ARCHIVE_PROBE_RESULT_FILE.is_file():
            try:
                return json.loads(
                    ARCHIVE_PROBE_RESULT_FILE.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise HTTPException(500, f"Probe result unreadable: {exc}") from exc
        time.sleep(0.35)
    ARCHIVE_PROBE_REQUEST_FILE.unlink(missing_ok=True)
    raise HTTPException(
        504,
        "Archive probe timed out — check worker, Gluetun VPN, and FlareSolverr on ai-server",
    )


@app.get("/api/archive/probe/pending")
def archive_probe_pending(
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not ARCHIVE_PROBE_REQUEST_FILE.is_file():
        return {"pending": False}
    try:
        req = json.loads(ARCHIVE_PROBE_REQUEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ARCHIVE_PROBE_REQUEST_FILE.unlink(missing_ok=True)
        return {"pending": False}
    ARCHIVE_PROBE_REQUEST_FILE.unlink(missing_ok=True)
    return {"pending": True, "request": req}


@app.post("/api/archive/probe/complete")
def archive_probe_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    result = {
        "ok": bool(body.get("ok", True)),
        "url": body.get("url") or "",
        "engine": body.get("engine") or "httrack",
        "detail": body.get("detail") or "",
        "cloudflare": bool(body.get("cloudflare")),
        "at": body.get("at") or _now(),
        "error": body.get("error") or "",
    }
    ARCHIVE_PROBE_RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"ok": True}


class FolderCreate(BaseModel):
    drive_id: str
    name: str = Field(min_length=1, max_length=80)
    parent_rel: str = ""

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = v.strip().replace("/", "-").replace("\\", "-")
        if not v:
            raise ValueError("Folder name required")
        return v


@app.post("/api/library/folders")
def create_folder(body: FolderCreate) -> dict[str, Any]:
    lib = _load_library()
    drive = lib.get("drives", {}).get(body.drive_id)
    if not drive:
        raise HTTPException(400, "Unknown drive")
    parent = body.parent_rel.strip().strip("/")
    rel = f"{parent}/{body.name}" if parent else body.name
    fid = _slug(rel)
    existing = {f["id"] for f in drive["folders"]}
    if fid in existing:
        fid = f"{fid}_{uuid.uuid4().hex[:4]}"
    folder = {
        "id": fid,
        "name": body.name,
        "rel": rel,
        # Hub-created paths (e.g. torrents/Show/Season NN) are not in the
        # worker's top-level folder scan; still valid once mkdir runs on claim.
        "present": True,
    }
    drive["folders"].append(folder)
    key = _folder_key(body.drive_id, fid)
    lib.setdefault("tags_by_folder", {}).setdefault(key, [])
    _save_library(lib)
    return {"ok": True, "folder": folder, "drive_id": body.drive_id}


class TagAdd(BaseModel):
    drive_id: str
    folder_id: str
    tag: str = Field(min_length=1, max_length=40)

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, v: str) -> str:
        v = v.strip().lstrip("#").lower()
        v = re.sub(r"\s+", "-", v)
        if not v:
            raise ValueError("Tag required")
        return v


@app.get("/api/library/tags")
def get_tags(drive_id: str, folder_id: str) -> dict[str, Any]:
    lib = _load_library()
    key = _folder_key(drive_id, folder_id)
    tags = lib.get("tags_by_folder", {}).get(key, [])
    return {"tags": tags, "key": key}


@app.post("/api/library/tags")
def add_tag(body: TagAdd) -> dict[str, Any]:
    lib = _load_library()
    drive = lib.get("drives", {}).get(body.drive_id)
    if not drive or not any(f["id"] == body.folder_id for f in drive["folders"]):
        raise HTTPException(400, "Unknown drive/folder")
    key = _folder_key(body.drive_id, body.folder_id)
    tags = lib.setdefault("tags_by_folder", {}).setdefault(key, [])
    if body.tag not in tags:
        tags.append(body.tag)
        tags.sort()
        _save_library(lib)
    return {"ok": True, "tags": tags}


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    source: str = "auto"  # auto | github | hf | web | youtube | torrent | all | both
    torrent_cats: list[int] = []  # Jackett category IDs (empty = all)
    torrent_hub_category: str = "all"  # all | tv | movies | games — catalog indexer filter
    torrent_pack_search: bool = False  # dual Jackett query: "{show} complete" + season
    torrent_tv_search: bool = False  # TV: pack queries + plain (filter client-side)
    torrent_result_limit: int = 0  # 0 = worker default (40 when tv search)


def search_relay_timeout_s(body: SearchBody, resolved_source: str) -> float:
    """Pi wait for worker; torrent + TV pack queries can exceed default 35s."""
    src = (resolved_source or "").strip().lower()
    if src == "torrent" or "torrent" in {s.strip() for s in src.split(",")}:
        if body.torrent_tv_search:
            return max(TORRENT_SEARCH_RELAY_TIMEOUT_S, 150.0)
        return TORRENT_SEARCH_RELAY_TIMEOUT_S
    return SEARCH_RELAY_TIMEOUT_S


def _default_search_providers() -> dict[str, Any]:
    return {
        "github": {"enabled": True, "label": "GitHub", "engine": "native"},
        "hf": {"enabled": True, "label": "Hugging Face", "engine": "native"},
        "web": {
            "enabled": True,
            "label": "Web",
            "engine": "searxng",
            "categories": "general",
        },
        "youtube": {
            "enabled": True,
            "label": "YouTube",
            "engine": "searxng",
            "categories": "videos",
        },
        "torrent": {
            "enabled": False,
            "label": "Torrent",
            "engine": "jackett",
        },
    }


def _load_search_providers() -> dict[str, Any]:
    base = _default_search_providers()
    path = SEARCH_PROVIDERS_FILE
    if not path.is_file() and DEFAULT_SEARCH_PROVIDERS.is_file():
        path = DEFAULT_SEARCH_PROVIDERS
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, dict) and key in base:
                        base[key] = {**base[key], **val}
        except Exception:
            pass
    return base


def _resolve_search_source(query: str, source: str) -> str:
    src = (source or "auto").strip().lower()
    if src and src != "auto":
        return src
    low = query.lower()
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if "huggingface.co" in low or low.startswith("hf:"):
        return "hf"
    if "github.com" in low:
        return "github"
    # Default catalog search stays GitHub+HF; Web/YouTube via chips or All.
    return "both"


@app.get("/api/search/providers")
def search_providers() -> dict[str, Any]:
    """Enabled search providers for Hub UI chips (Phase D1)."""
    providers = _load_search_providers()
    out = []
    for pid, cfg in providers.items():
        if not cfg.get("enabled", True):
            continue
        out.append(
            {
                "id": pid,
                "label": cfg.get("label") or pid,
                "engine": cfg.get("engine") or "native",
            }
        )
    return {"providers": out, "default": "auto"}


def _wait_relay_result(
    request_file: Path,
    result_file: Path,
    request_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if result_file.exists():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if data.get("request_id") == request_id:
                request_file.unlink(missing_ok=True)
                return data
        if not request_file.exists() and not result_file.exists():
            break
        time.sleep(0.35)
    request_file.unlink(missing_ok=True)
    raise HTTPException(
        504,
        "Timed out waiting for Proton (Gluetun) worker. Torrent/TV searches can take "
        "up to 2 minutes — retry, or check hub-worker on ai-server if this persists.",
    )


@app.post("/api/search")
def search(body: SearchBody) -> dict[str, Any]:
    """Search via Ubuntu worker + Gluetun (never Pi ISP egress)."""
    q = body.query.strip()
    source = _resolve_search_source(q, body.source)
    providers = _load_search_providers()
    # Drop disabled providers from multi-source requests
    if source in ("both", "all", "auto"):
        wanted = (
            ["github", "hf", "web", "youtube"]
            if source == "all"
            else ["github", "hf"]
        )
        enabled = [p for p in wanted if providers.get(p, {}).get("enabled", True)]
        source = ",".join(enabled) if enabled else "github"
    elif source in providers and not providers[source].get("enabled", True):
        raise HTTPException(400, f"Search provider disabled: {source}")
    _ensure_dirs()
    request_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "request_id": request_id,
        "query": q,
        "source": source,
        "requested_at": _now(),
        "status": "pending",
    }
    if body.torrent_cats:
        payload["torrent_cats"] = body.torrent_cats
    payload["torrent_hub_category"] = normalize_hub_category(body.torrent_hub_category)
    if body.torrent_pack_search:
        payload["torrent_pack_search"] = True
    if body.torrent_tv_search:
        payload["torrent_tv_search"] = True
    if body.torrent_result_limit > 0:
        payload["torrent_result_limit"] = body.torrent_result_limit
    SEARCH_RESULT_FILE.unlink(missing_ok=True)
    SEARCH_REQUEST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    relay_timeout = search_relay_timeout_s(body, source)
    data = _wait_relay_result(
        SEARCH_REQUEST_FILE, SEARCH_RESULT_FILE, request_id, relay_timeout
    )
    if not data.get("ok", True):
        raise HTTPException(502, data.get("detail") or "Private search failed")
    return {
        "results": data.get("results") or [],
        "errors": data.get("errors") or [],
        "source": data.get("source") or source,
        "egress": data.get("egress") or "proton",
        "vpn_detail": data.get("vpn_detail") or "",
        "series_info": data.get("series_info"),
        "target_seasons": data.get("target_seasons") or [],
    }


@app.get("/api/search/pending")
def search_pending(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not SEARCH_REQUEST_FILE.exists():
        return {"pending": False}
    try:
        req = json.loads(SEARCH_REQUEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        req = {}
    return {"pending": True, "request": req}


@app.post("/api/search/complete")
def search_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    result = {
        "ok": bool(body.get("ok", True)),
        "detail": body.get("detail") or "",
        "request_id": body.get("request_id") or "",
        "source": body.get("source") or "",
        "results": body.get("results") or [],
        "errors": body.get("errors") or [],
        "egress": body.get("egress") or "proton",
        "vpn_detail": body.get("vpn_detail") or "",
        "series_info": body.get("series_info"),
        "target_seasons": body.get("target_seasons") or [],
        "at": body.get("at") or _now(),
    }
    SEARCH_RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    SEARCH_REQUEST_FILE.unlink(missing_ok=True)
    return {"ok": True}


def _summarize_torrent_indexers(
    catalog_rows: list[dict[str, Any]],
    merged: list[dict[str, Any]],
    snapshot_at: str | None,
) -> dict[str, Any]:
    """Counts for Hub overview: healthy probes vs catalog (+ extra Jackett indexers)."""
    catalog_ids = {
        str(r.get("jackett_id") or "")
        for r in catalog_rows
        if isinstance(r, dict) and r.get("jackett_id")
    }
    catalog_total = len(catalog_ids)
    by_id = {
        str(m.get("jackett_id") or ""): m
        for m in merged
        if isinstance(m, dict) and m.get("jackett_id")
    }
    extras = [
        iid
        for iid, row in by_id.items()
        if iid not in catalog_ids and row.get("configured")
    ]
    total = catalog_total + len(extras)
    healthy = sum(1 for m in merged if m.get("status") == "healthy")
    configured = sum(1 for m in merged if m.get("configured"))
    configured_catalog = sum(
        1 for iid in catalog_ids if (by_id.get(iid) or {}).get("configured")
    )
    return {
        "catalog_total": catalog_total,
        "total": total,
        "healthy": healthy,
        "configured": configured,
        "configured_in_catalog": configured_catalog,
        "active_label": f"{healthy}/{total or catalog_total}",
        "access_label": f"{configured_catalog}/{catalog_total or total}",
        "has_snapshot": bool(snapshot_at),
        "snapshot_at": snapshot_at,
    }


def _load_torrent_indexers_catalog() -> dict[str, Any]:
    catalog: dict[str, Any] = {"indexers": [], "megathread_url": ""}
    if TORRENT_INDEXERS_CATALOG.is_file():
        try:
            catalog = json.loads(TORRENT_INDEXERS_CATALOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return catalog


def _load_indexers_snapshot() -> dict[str, Any]:
    if not INDEXERS_STATUS_FILE.is_file():
        return {}
    try:
        data = json.loads(INDEXERS_STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_indexers_candidates_store() -> dict[str, Any]:
    if not INDEXERS_CANDIDATES_FILE.is_file():
        return load_candidates_store(None)
    try:
        raw = json.loads(INDEXERS_CANDIDATES_FILE.read_text(encoding="utf-8"))
        return load_candidates_store(raw if isinstance(raw, dict) else None)
    except Exception:
        return load_candidates_store(None)


def _save_indexers_candidates_store(store: dict[str, Any]) -> None:
    _ensure_dirs()
    INDEXERS_CANDIDATES_FILE.write_text(
        json.dumps(load_candidates_store(store), indent=2),
        encoding="utf-8",
    )


def _load_indexers_candidates_status() -> dict[str, Any]:
    if not INDEXERS_CANDIDATES_STATUS_FILE.is_file():
        return {}
    try:
        data = json.loads(INDEXERS_CANDIDATES_STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_torrent_indexers_payload() -> dict[str, Any]:
    catalog = _load_torrent_indexers_catalog()
    snapshot = _load_indexers_snapshot()
    candidates_store = _load_indexers_candidates_store()
    candidates_status = _load_indexers_candidates_status()
    by_id = {
        str(r.get("jackett_id") or ""): r
        for r in (snapshot.get("indexers") or [])
        if isinstance(r, dict)
    }
    catalog_rows = [
        r for r in (catalog.get("indexers") or []) if isinstance(r, dict)
    ]
    catalog_by_id = {
        str(r.get("jackett_id") or ""): r for r in catalog_rows if r.get("jackett_id")
    }
    merged: list[dict[str, Any]] = []
    for row in catalog_rows:
        iid = str(row.get("jackett_id") or "")
        live = by_id.get(iid) or {}
        merged.append({**row, **live, "jackett_id": iid})
    for iid, live in by_id.items():
        if any(m.get("jackett_id") == iid for m in merged):
            continue
        cat_row = catalog_by_id.get(iid) or {}
        merged.append(
            {
                **live,
                "jackett_id": iid,
                "categories": cat_row.get("categories") or ["general"],
            }
        )
    merged = attach_candidates_to_indexers(
        merged, candidates_store, candidates_status, catalog_by_id
    )
    snapshot_at = snapshot.get("at")
    return {
        "megathread_url": catalog.get("megathread_url") or "",
        "megathread_anime_url": catalog.get("megathread_anime_url") or "",
        "megathread_games_url": catalog.get("megathread_games_url") or "",
        "catalog_version": catalog.get("catalog_version") or "",
        "source_note": catalog.get("source_note") or "",
        "snapshot_at": snapshot_at,
        "probe_query": snapshot.get("probe_query"),
        "candidates_checked_at": candidates_status.get("at"),
        "summary": _summarize_torrent_indexers(catalog_rows, merged, snapshot_at),
        "indexers": merged,
    }


@app.get("/api/torrent/indexers")
def torrent_indexers_status() -> dict[str, Any]:
    """Megathread catalog + last worker Jackett health snapshot."""
    return _build_torrent_indexers_payload()


@app.get("/api/torrent/indexers/candidates")
def torrent_indexers_candidates_get() -> dict[str, Any]:
    store = _load_indexers_candidates_store()
    status = _load_indexers_candidates_status()
    return {"ok": True, "store": store, "status_at": status.get("at")}


class IndexerCandidatesPutBody(BaseModel):
    jackett_id: str = Field(min_length=1, max_length=128)
    candidates: list[dict[str, Any]] = Field(default_factory=list)


@app.put("/api/torrent/indexers/candidates")
def torrent_indexers_candidates_put(body: IndexerCandidatesPutBody) -> dict[str, Any]:
    iid = body.jackett_id.strip()
    store = _load_indexers_candidates_store()
    by_id = store.setdefault("by_jackett_id", {})
    cleaned: list[dict[str, Any]] = []
    for row in body.candidates:
        if not isinstance(row, dict):
            continue
        try:
            norm = validate_public_http_url(str(row.get("url") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        cleaned.append(
            {
                "url": norm,
                "label": str(row.get("label") or "")[:120],
                "source": str(row.get("source") or "manual")[:32],
                "added_at": row.get("added_at") or _now(),
            }
        )
    if cleaned:
        by_id[iid] = {"candidates": cleaned}
    else:
        by_id.pop(iid, None)
    _save_indexers_candidates_store(store)
    return {"ok": True, "jackett_id": iid, "count": len(cleaned)}


class IndexerCandidatesCheckBody(BaseModel):
    include_references: bool = True


@app.post("/api/torrent/indexers/candidates/check")
def torrent_indexers_candidates_check_queue(
    body: IndexerCandidatesCheckBody | None = None,
) -> dict[str, Any]:
    _ensure_dirs()
    include_refs = True if body is None else body.include_references
    INDEXERS_CANDIDATES_CHECK_REQUEST_FILE.write_text(
        json.dumps({"at": _now(), "include_references": include_refs}, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "queued": True, "include_references": include_refs}


class IndexerImportHintsBody(BaseModel):
    jackett_id: str | None = None
    all: bool = False


@app.post("/api/torrent/indexers/candidates/import-hints")
def torrent_indexers_candidates_import_hints(
    body: IndexerImportHintsBody,
) -> dict[str, Any]:
    """Copy catalog reference_urls into tracked mirrors (deduped)."""
    catalog = _load_torrent_indexers_catalog()
    catalog_rows = [
        r for r in (catalog.get("indexers") or []) if isinstance(r, dict)
    ]
    catalog_by_id = {
        str(r.get("jackett_id") or ""): r for r in catalog_rows if r.get("jackett_id")
    }
    target_ids: list[str]
    if body.all:
        target_ids = list(catalog_by_id.keys())
    elif body.jackett_id:
        iid = body.jackett_id.strip()
        if iid not in catalog_by_id:
            raise HTTPException(404, f"Unknown catalog indexer {iid}")
        target_ids = [iid]
    else:
        raise HTTPException(400, "Set jackett_id or all=true")

    store = _load_indexers_candidates_store()
    by_id = store.setdefault("by_jackett_id", {})
    added = 0
    for iid in target_ids:
        entries = parse_reference_url_entries(catalog_by_id[iid].get("reference_urls"))
        if not entries:
            continue
        block = by_id.setdefault(iid, {"candidates": []})
        existing = {
            str(c.get("url") or "")
            for c in (block.get("candidates") or [])
            if isinstance(c, dict)
        }
        for url, label in entries:
            try:
                norm = validate_public_http_url(url)
            except ValueError:
                continue
            if norm in existing:
                continue
            block["candidates"].append(
                {
                    "url": norm,
                    "label": label[:120],
                    "source": "megathread_import",
                    "added_at": _now(),
                }
            )
            existing.add(norm)
            added += 1
    _save_indexers_candidates_store(store)
    return {"ok": True, "added": added, "indexers": len(target_ids)}


@app.get("/api/torrent/indexers/candidates/check/pending")
def torrent_indexers_candidates_check_pending(
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not INDEXERS_CANDIDATES_CHECK_REQUEST_FILE.is_file():
        return {"pending": False}
    try:
        req = json.loads(
            INDEXERS_CANDIDATES_CHECK_REQUEST_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        req = {}
    include_refs = bool(req.get("include_references", True))
    payload = _build_torrent_indexers_payload()
    urls_to_check: dict[str, list[str]] = {}
    for row in payload.get("indexers") or []:
        if not isinstance(row, dict) or not row.get("jackett_id"):
            continue
        iid = str(row["jackett_id"])
        urls = mirror_urls_to_check(
            row.get("mirror_candidates") or [],
            include_references=include_refs,
        )
        if urls:
            urls_to_check[iid] = urls
    snapshot = _load_indexers_snapshot()
    by_live = {
        str(r.get("jackett_id") or ""): r
        for r in (snapshot.get("indexers") or [])
        if isinstance(r, dict)
    }
    return {
        "pending": True,
        "request": req,
        "include_references": include_refs,
        "urls_to_check": urls_to_check,
        "candidates": _load_indexers_candidates_store().get("by_jackett_id") or {},
        "jackett_site_urls": {
            iid: (row.get("site_url") or "")
            for iid, row in by_live.items()
            if row.get("site_url")
        },
    }


@app.post("/api/torrent/indexers/candidates/check/complete")
def torrent_indexers_candidates_check_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    INDEXERS_CANDIDATES_STATUS_FILE.write_text(json.dumps(body, indent=2), encoding="utf-8")
    INDEXERS_CANDIDATES_CHECK_REQUEST_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/torrent/indexers/refresh")
def torrent_indexers_refresh(
    probe_query: str = "breaking bad season 1",
) -> dict[str, Any]:
    _ensure_dirs()
    INDEXERS_SCAN_REQUEST_FILE.write_text(
        json.dumps(
            {"probe_query": probe_query.strip() or "breaking bad season 1", "at": _now()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"ok": True, "queued": True}


@app.get("/api/torrent/indexers/scan/pending")
def torrent_indexers_scan_pending(
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not INDEXERS_SCAN_REQUEST_FILE.is_file():
        return {"pending": False}
    try:
        req = json.loads(INDEXERS_SCAN_REQUEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        req = {}
    return {"pending": True, "request": req}


@app.post("/api/torrent/indexers/scan/complete")
def torrent_indexers_scan_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    INDEXERS_STATUS_FILE.write_text(json.dumps(body, indent=2), encoding="utf-8")
    INDEXERS_SCAN_REQUEST_FILE.unlink(missing_ok=True)
    return {"ok": True}


def _load_research_presets() -> dict[str, Any]:
    path = JOBS_DIR / "research-presets.json"
    if not path.is_file() and DEFAULT_RESEARCH_PRESETS.is_file():
        _ensure_dirs()
        path.write_text(DEFAULT_RESEARCH_PRESETS.read_text(encoding="utf-8"), encoding="utf-8")
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("presets"), list):
                return data
        except Exception:
            pass
    if DEFAULT_RESEARCH_PRESETS.is_file():
        return json.loads(DEFAULT_RESEARCH_PRESETS.read_text(encoding="utf-8"))
    return {"version": 1, "presets": []}


class ResearchBody(BaseModel):
    preset: str = Field(min_length=1, max_length=64)


@app.get("/api/research/presets")
def research_presets() -> dict[str, Any]:
    data = _load_research_presets()
    presets = []
    for p in data.get("presets") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        presets.append(
            {
                "id": p["id"],
                "label": p.get("label") or p["id"],
                "description": p.get("description") or "",
            }
        )
    return {"presets": presets}


@app.get("/api/research")
def research_cached(preset: str | None = None) -> dict[str, Any]:
    """Return cached research for a preset (or the last refresh if unset)."""
    if not RESEARCH_CACHE_FILE.is_file():
        return {"ok": True, "results": [], "preset": preset or "", "cached": False}
    try:
        data = json.loads(RESEARCH_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": True, "results": [], "preset": preset or "", "cached": False}
    by = data.get("by_preset") if isinstance(data.get("by_preset"), dict) else None
    entry = None
    if preset and by and isinstance(by.get(preset), dict):
        entry = by[preset]
    elif not preset and isinstance(data.get("results"), list):
        # legacy flat cache
        entry = data
    elif not preset and by:
        # newest by `at`
        best = None
        for v in by.values():
            if not isinstance(v, dict):
                continue
            if best is None or str(v.get("at") or "") > str(best.get("at") or ""):
                best = v
        entry = best
    if not entry:
        return {
            "ok": True,
            "results": [],
            "preset": preset or "",
            "cached": False,
            "detail": "No cache for this preset yet — refresh",
        }
    return {
        "ok": True,
        "results": entry.get("results") or [],
        "preset": entry.get("preset") or preset or "",
        "label": entry.get("label") or "",
        "egress": entry.get("egress") or "proton",
        "errors": entry.get("errors") or [],
        "at": entry.get("at") or "",
        "cached": True,
    }


@app.post("/api/research")
def research_run(body: ResearchBody) -> dict[str, Any]:
    """Refresh a research preset via Ubuntu worker + Gluetun (never Pi ISP)."""
    preset_id = body.preset.strip()
    data = _load_research_presets()
    preset = next(
        (p for p in (data.get("presets") or []) if isinstance(p, dict) and p.get("id") == preset_id),
        None,
    )
    if not preset:
        raise HTTPException(404, f"Unknown research preset: {preset_id}")
    _ensure_dirs()
    request_id = uuid.uuid4().hex
    payload = {
        "request_id": request_id,
        "preset": preset_id,
        "preset_def": preset,
        "requested_at": _now(),
        "status": "pending",
    }
    RESEARCH_RESULT_FILE.unlink(missing_ok=True)
    RESEARCH_REQUEST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = _wait_relay_result(
        RESEARCH_REQUEST_FILE,
        RESEARCH_RESULT_FILE,
        request_id,
        max(SEARCH_RELAY_TIMEOUT_S, 55.0),
    )
    if not result.get("ok", True):
        raise HTTPException(502, result.get("detail") or "Research via Proton failed")
    entry = {
        "preset": preset_id,
        "label": preset.get("label") or preset_id,
        "results": result.get("results") or [],
        "errors": result.get("errors") or [],
        "egress": result.get("egress") or "proton",
        "vpn_detail": result.get("vpn_detail") or "",
        "at": result.get("at") or _now(),
    }
    cache: dict[str, Any] = {"version": 1, "by_preset": {}}
    if RESEARCH_CACHE_FILE.is_file():
        try:
            prev = json.loads(RESEARCH_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(prev.get("by_preset"), dict):
                cache["by_preset"] = prev["by_preset"]
        except Exception:
            pass
    cache["by_preset"][preset_id] = entry
    RESEARCH_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "results": entry["results"],
        "errors": entry["errors"],
        "preset": preset_id,
        "label": entry["label"],
        "egress": entry["egress"],
        "vpn_detail": entry["vpn_detail"],
        "at": entry["at"],
        "cached": False,
    }


@app.get("/api/research/pending")
def research_pending(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not RESEARCH_REQUEST_FILE.exists():
        return {"pending": False}
    try:
        req = json.loads(RESEARCH_REQUEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        req = {}
    return {"pending": True, "request": req}


@app.post("/api/research/complete")
def research_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    result = {
        "ok": bool(body.get("ok", True)),
        "detail": body.get("detail") or "",
        "request_id": body.get("request_id") or "",
        "preset": body.get("preset") or "",
        "results": body.get("results") or [],
        "errors": body.get("errors") or [],
        "egress": body.get("egress") or "proton",
        "vpn_detail": body.get("vpn_detail") or "",
        "at": body.get("at") or _now(),
    }
    RESEARCH_RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    RESEARCH_REQUEST_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/hf/files")
def hf_files(repo_id: str, revision: str = "main") -> dict[str, Any]:
    """List HF files via Ubuntu worker + Gluetun (never Pi ISP egress)."""
    repo_id = repo_id.strip()
    if not repo_id or "/" not in repo_id:
        raise HTTPException(400, "repo_id must be org/name")
    _ensure_dirs()
    request_id = uuid.uuid4().hex
    payload = {
        "request_id": request_id,
        "repo_id": repo_id,
        "revision": revision,
        "requested_at": _now(),
        "status": "pending",
    }
    HF_FILES_RESULT_FILE.unlink(missing_ok=True)
    HF_FILES_REQUEST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    data = _wait_relay_result(
        HF_FILES_REQUEST_FILE, HF_FILES_RESULT_FILE, request_id, SEARCH_RELAY_TIMEOUT_S
    )
    if not data.get("ok", True):
        raise HTTPException(502, data.get("detail") or "HF file list via Proton failed")
    return {
        "repo_id": data.get("repo_id") or repo_id,
        "revision": data.get("revision") or revision,
        "files": data.get("files") or [],
        "folders": data.get("folders") or [],
        "whole_repo_url": data.get("whole_repo_url")
        or f"https://huggingface.co/{repo_id}",
        "egress": data.get("egress") or "proton",
        "vpn_detail": data.get("vpn_detail") or "",
    }


@app.get("/api/hf/files/pending")
def hf_files_pending(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not HF_FILES_REQUEST_FILE.exists():
        return {"pending": False}
    try:
        req = json.loads(HF_FILES_REQUEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        req = {}
    return {"pending": True, "request": req}


@app.post("/api/hf/files/complete")
def hf_files_complete(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    result = {
        "ok": bool(body.get("ok", True)),
        "detail": body.get("detail") or "",
        "request_id": body.get("request_id") or "",
        "repo_id": body.get("repo_id") or "",
        "revision": body.get("revision") or "main",
        "files": body.get("files") or [],
        "folders": body.get("folders") or [],
        "whole_repo_url": body.get("whole_repo_url") or "",
        "egress": body.get("egress") or "proton",
        "vpn_detail": body.get("vpn_detail") or "",
        "at": body.get("at") or _now(),
    }
    HF_FILES_RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    HF_FILES_REQUEST_FILE.unlink(missing_ok=True)
    return {"ok": True}


def _load_capture_tokens() -> dict[str, Any]:
    if not CAPTURE_TOKENS_FILE.exists():
        return {"tokens": []}
    try:
        return json.loads(CAPTURE_TOKENS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"tokens": []}


def _save_capture_tokens(data: dict[str, Any]) -> None:
    _ensure_dirs()
    CAPTURE_TOKENS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _capture_token_valid(token: str | None) -> bool:
    if not token:
        return False
    if HUB_CAPTURE_TOKEN and token == HUB_CAPTURE_TOKEN:
        return True
    data = _load_capture_tokens()
    now = datetime.now(timezone.utc)
    for row in data.get("tokens") or []:
        if row.get("token") != token:
            continue
        exp = row.get("expires_at")
        if not exp:
            return True
        try:
            if datetime.fromisoformat(str(exp).replace("Z", "+00:00")) > now:
                return True
        except ValueError:
            continue
    return False


def _require_capture_token(
    authorization: str | None = None,
    x_capture_token: str | None = None,
) -> None:
    token = (x_capture_token or "").strip()
    if not token and authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not _capture_token_valid(token):
        raise HTTPException(
            401,
            "Capture token required (set HUB_CAPTURE_TOKEN or mint via POST /api/capture/token)",
        )


def _default_capture_destination(lib: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    drives = lib.get("drives") or {}
    # Prefer a mounted drive with an inbox-like folder
    for prefer in ("inbox", "media-archive", "downloads"):
        for did, drive in drives.items():
            if drive.get("mounted") is False:
                continue
            for folder in drive.get("folders") or []:
                name = (folder.get("name") or folder.get("rel") or "").lower()
                if prefer in name.replace(" ", "-") or name == prefer:
                    return did, folder["id"], drive, folder
    for did, drive in drives.items():
        if drive.get("mounted") is False:
            continue
        folders = drive.get("folders") or []
        if folders:
            return did, folders[0]["id"], drive, folders[0]
    raise HTTPException(503, "No mounted drive/folder available for capture")


def _load_type_folder_map() -> dict[str, dict[str, str]]:
    """Merge optional capture-folders.json over built-in defaults."""
    out = {k: dict(v) for k, v in DEFAULT_TYPE_FOLDER_MAP.items()}
    if not CAPTURE_FOLDERS_FILE.is_file():
        return out
    try:
        data = json.loads(CAPTURE_FOLDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return out
    raw = data.get("type_folder_map") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return out
    for key, row in raw.items():
        if not isinstance(row, dict):
            continue
        did = str(row.get("drive_id") or "").strip()
        fid = str(row.get("folder_id") or "").strip()
        if did and fid:
            out[str(key).strip().lower()] = {"drive_id": did, "folder_id": fid}
    return out


def _capture_map_key(body_kind: str, job_kind: str, source: str) -> str:
    k = (body_kind or "auto").strip().lower()
    if k in ("media", "website", "github", "hf"):
        return k
    if job_kind == "video":
        return "media"
    if job_kind in ("github", "hf", "magnet"):
        return job_kind
    if source == "website" or job_kind == "http":
        return "website"
    return "default"


def _lookup_mapped_destination(
    lib: dict[str, Any], map_key: str
) -> tuple[str, str, dict[str, Any], dict[str, Any]] | None:
    fmap = _load_type_folder_map()
    for key in (map_key, "default"):
        row = fmap.get(key) or {}
        did = str(row.get("drive_id") or "").strip()
        fid = str(row.get("folder_id") or "").strip()
        if not did or not fid:
            continue
        drive = (lib.get("drives") or {}).get(did)
        if not drive or drive.get("mounted") is False:
            continue
        folder = next((f for f in drive.get("folders") or [] if f.get("id") == fid), None)
        if folder:
            return did, fid, drive, folder
    return None


def _resolve_capture_destination(
    lib: dict[str, Any], map_key: str
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    hit = _lookup_mapped_destination(lib, map_key)
    if hit:
        return hit
    return _default_capture_destination(lib)


class CaptureBody(BaseModel):
    url: str = Field(min_length=3)
    title: str = ""
    kind: str = "auto"  # auto | website | media | github | hf | video | http | magnet
    drive_id: str = ""
    folder_id: str = ""
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    high_priority: bool = False
    notify_on_complete: bool = False

    @field_validator("url")
    @classmethod
    def strip_url(cls, v: str) -> str:
        v = v.strip()
        if not (
            v.startswith("http://")
            or v.startswith("https://")
            or v.startswith("magnet:")
        ):
            raise ValueError("URL must start with http(s):// or magnet:")
        return v


def _capture_kind(url: str, kind: str) -> tuple[str, str]:
    """Return (job_kind, source). website → http (archive/pull via aria2/http)."""
    k = (kind or "auto").strip().lower()
    if k == "website":
        return "http", "website"
    if k == "media":
        detected = _detect_kind(url, "")
        if detected in ("video", "http", "magnet"):
            return detected, "media"
        return "video", "media"
    if k in ("github", "hf", "video", "http", "magnet"):
        return k, k
    if k == "auto":
        return _detect_kind(url, ""), "capture"
    return _detect_kind(url, ""), k or "capture"


@app.get("/api/capture/destinations")
def capture_destinations(
    authorization: str | None = Header(default=None),
    x_capture_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_capture_token(authorization, x_capture_token)
    lib = _load_library()
    out: list[dict[str, Any]] = []
    for did, drive in (lib.get("drives") or {}).items():
        if drive.get("mounted") is False:
            continue
        for folder in drive.get("folders") or []:
            out.append(
                {
                    "drive_id": did,
                    "folder_id": folder["id"],
                    "label": f"{drive.get('label') or did} / {folder.get('name')}",
                    "kind_hint": "media"
                    if "model" in (folder.get("name") or "").lower()
                    else "website",
                }
            )
    fmap = _load_type_folder_map()
    return {
        "destinations": out,
        "kinds": ["auto", "media", "website", "github", "hf"],
        "kind_labels": {
            "auto": "Auto",
            "media": "Video / YouTube",
            "website": "Website",
            "github": "GitHub",
            "hf": "Hugging Face",
        },
        "type_folder_map": fmap,
    }


@app.post("/api/capture/token")
def mint_capture_token(
    body: dict[str, Any] = Body(default_factory=dict),
    x_worker_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_capture_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Mint a short-lived capture token (worker token or existing capture token)."""
    ok = False
    if WORKER_TOKEN and x_worker_token == WORKER_TOKEN:
        ok = True
    if _capture_token_valid(
        (x_capture_token or "").strip()
        or (
            authorization[7:].strip()
            if authorization and authorization.lower().startswith("bearer ")
            else ""
        )
    ):
        ok = True
    if HUB_CAPTURE_TOKEN and (
        (x_capture_token or "") == HUB_CAPTURE_TOKEN
        or (authorization or "").endswith(HUB_CAPTURE_TOKEN)
    ):
        ok = True
    if not ok:
        raise HTTPException(401, "Not authorized to mint capture tokens")
    days = int(body.get("ttl_days") or 30)
    days = max(1, min(days, 90))
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    data = _load_capture_tokens()
    tokens = [t for t in (data.get("tokens") or []) if t.get("token")]
    tokens.append(
        {
            "token": token,
            "label": str(body.get("label") or "firefox")[:40],
            "created_at": _now(),
            "expires_at": expires.isoformat(),
        }
    )
    data["tokens"] = tokens[-20:]
    _save_capture_tokens(data)
    return {"ok": True, "token": token, "expires_at": expires.isoformat(), "ttl_days": days}


@app.post("/api/capture")
async def capture_url(
    body: CaptureBody,
    authorization: str | None = Header(default=None),
    x_capture_token: str | None = Header(default=None),
) -> JSONResponse:
    """Firefox / share entry: enqueue download with Proton defaults."""
    _require_capture_token(authorization, x_capture_token)
    lib = _load_library()
    job_kind, source = _capture_kind(body.url, body.kind)
    map_key = _capture_map_key(body.kind, job_kind, source)
    drive_id = body.drive_id.strip()
    folder_id = body.folder_id.strip()
    if drive_id and folder_id:
        drive = lib.get("drives", {}).get(drive_id)
        if not drive:
            raise HTTPException(400, "Unknown drive")
        if drive.get("mounted") is False:
            raise HTTPException(503, f"{drive.get('label', drive_id)} is not mounted")
        folder = next((f for f in drive["folders"] if f["id"] == folder_id), None)
        if not folder:
            raise HTTPException(400, "Unknown folder")
    else:
        drive_id, folder_id, drive, folder = _resolve_capture_destination(lib, map_key)

    dest_path = str(_folder_abs_path(drive, folder))
    if job_kind == "video":
        worker = "metube"
    elif job_kind in ("github", "hf"):
        worker = "repo"
    else:
        worker = "aria2"

    tags = []
    for t in body.tags:
        t = t.strip().lstrip("#").lower()
        if t:
            tags.append(t)
    if source == "website" and "website" not in tags:
        tags.append("website")

    title = body.title.strip() or body.url[:80]
    job = {
        "url": body.url,
        "title": title,
        "kind": job_kind,
        "source": source,
        "drive_id": drive_id,
        "folder_id": folder_id,
        "folder_name": folder["name"],
        "destination_path": dest_path,
        "worker": worker,
        "tags": tags,
        "options": {
            "via_proton": True,
            "sensitive": True,
            "high_priority": body.high_priority,
            "notify_on_complete": body.notify_on_complete,
            "captured_via": "firefox",
        },
        "hf": {"repo_id": "", "mode": "whole", "files": []},
        "notes": body.notes.strip(),
        "requested_by": "firefox-capture",
    }
    job = _enqueue_job(job)
    return JSONResponse(
        {
            "ok": True,
            "job": {
                "id": job["id"],
                "title": job["title"],
                "kind": job["kind"],
                "map_key": map_key,
                "destination": f"{drive.get('label') or drive_id} / {folder['name']}",
                "via_proton": True,
            },
        }
    )


class JobCreate(BaseModel):
    url: str = Field(min_length=3)
    title: str = ""
    kind: str = "auto"  # auto | video | http | magnet | github | hf | website_archive
    drive_id: str
    folder_id: str
    tags: list[str] = Field(default_factory=list)
    high_priority: bool = False
    notify_on_complete: bool = False
    notes: str = ""
    hf_mode: str = "whole"  # whole | files
    hf_files: list[str] = Field(default_factory=list)
    hf_repo_id: str = ""
    source: str = ""  # github | hf | manual
    archive_depth: int = 0  # 0 = unlimited recursion for website_archive
    torrent_meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def strip_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("magnet:")):
            raise ValueError("URL must start with http(s):// or magnet:")
        return v


def _detect_kind(url: str, source: str) -> str:
    if source == "github":
        return "github"
    if source == "hf":
        return "hf"
    u = url.lower()
    if u.startswith("magnet:"):
        return "magnet"
    if "github.com" in u:
        return "github"
    if "huggingface.co" in u:
        return "hf"
    if any(x in u for x in ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com")):
        return "video"
    if u.endswith(".torrent"):
        return "magnet"
    return "http"


@app.get("/api/jobs")
def list_jobs(
    limit: int = 30,
    status: str | None = None,
    requested_by: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    _ensure_dirs()
    cap = max(1, min(limit, 100))
    jobs: list[dict[str, Any]] = []
    for path in JOBS_SUB.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if status and job.get("status") != status:
            continue
        # Hide handled Lilly items unless explicitly requested
        if not status and job.get("status") == "dismissed":
            continue
        if requested_by and job.get("requested_by") != requested_by:
            continue
        if tag and tag not in (job.get("tags") or []):
            continue
        jobs.append(job)
    jobs.sort(
        key=lambda j: j.get("created_at") or j.get("started_at") or "",
        reverse=True,
    )
    return {"jobs": [_job_public(j) for j in jobs[:cap]]}


def _load_all_jobs() -> list[dict[str, Any]]:
    _ensure_dirs()
    jobs: list[dict[str, Any]] = []
    for path in JOBS_SUB.glob("*.json"):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return jobs


def _job_local_ts(job: dict[str, Any], field: str) -> datetime | None:
    raw = job.get(field)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(HUB_TZ)
    except ValueError:
        return None


def _prior_local_day_bounds() -> tuple[datetime, datetime, str]:
    now = datetime.now(HUB_TZ)
    end = datetime.combine(now.date(), datetime.min.time(), tzinfo=HUB_TZ)
    start = end - timedelta(days=1)
    label = start.strftime("%A %d %b")
    return start, end, label


def _job_on_prior_day(job: dict[str, Any], start: datetime, end: datetime) -> bool:
    for field in ("finished_at", "started_at", "created_at"):
        ts = _job_local_ts(job, field)
        if ts and start <= ts < end:
            return True
    return False


@app.get("/api/jobs/summary/last-night")
def jobs_summary_last_night() -> dict[str, Any]:
    """Jobs from the previous local calendar day (Australia/Brisbane by default)."""
    _ensure_dirs()
    start, end, label = _prior_local_day_bounds()
    matched = [j for j in _load_all_jobs() if _job_on_prior_day(j, start, end)]
    matched.sort(
        key=lambda j: j.get("finished_at") or j.get("started_at") or j.get("created_at") or "",
        reverse=True,
    )
    counts = dict(Counter(j.get("status", "?") for j in matched))
    slim = [
        {
            "id": j.get("id"),
            "title": j.get("title") or j.get("kind"),
            "kind": j.get("kind"),
            "status": j.get("status"),
            "folder_name": j.get("folder_name"),
            "destination_path": j.get("destination_path"),
            "error": _human_error(j.get("error")),
            "finished_at": j.get("finished_at"),
        }
        for j in matched[:20]
    ]
    return {
        "label": label,
        "timezone": str(HUB_TZ),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "total": len(matched),
        "counts": counts,
        "jobs": slim,
    }


@app.post("/api/jobs")
async def create_job(body: JobCreate) -> JSONResponse:
    lib = _load_library()
    drive = lib.get("drives", {}).get(body.drive_id)
    if not drive:
        raise HTTPException(400, "Unknown drive")
    if drive.get("mounted") is False:
        raise HTTPException(503, f"{drive.get('label', body.drive_id)} is not mounted on the worker")
    folder = next((f for f in drive["folders"] if f["id"] == body.folder_id), None)
    if not folder:
        raise HTTPException(400, "Unknown folder")
    kind = body.kind if body.kind != "auto" else _detect_kind(body.url, body.source)
    if folder.get("present") is False:
        # Nested torrent inbox folders are created by the Hub and may be absent
        # from the worker's shallow drive scan even when the path exists on disk.
        if kind not in ("magnet", "http", "auto") or not str(folder.get("rel") or "").startswith(
            "torrents/"
        ):
            raise HTTPException(
                400, f"Folder not found on worker disk: {folder.get('rel')}"
            )
    dest_path = str(_folder_abs_path(drive, folder))

    # persist any new tags onto folder catalog
    key = _folder_key(body.drive_id, body.folder_id)
    catalog = lib.setdefault("tags_by_folder", {}).setdefault(key, [])
    changed = False
    clean_tags: list[str] = []
    for t in body.tags:
        t = t.strip().lstrip("#").lower()
        if not t:
            continue
        clean_tags.append(t)
        if t not in catalog:
            catalog.append(t)
            changed = True
    if changed:
        catalog.sort()
        _save_library(lib)

    if kind == "video":
        worker = "metube"
    elif kind in ("github", "hf"):
        worker = "repo"
    elif kind == "website_archive":
        worker = "httrack"
    else:
        worker = "aria2"

    if kind == "website_archive":
        domain = urlparse(body.url).netloc or "unknown"
        dest_path = str(Path(dest_path) / domain)

    if worker == "aria2":
        aria_err = validate_aria2_destination(dest_path)
        if aria_err:
            raise HTTPException(400, aria_err)

    job: dict[str, Any] = {
        "url": body.url,
        "title": body.title.strip() or (f"Archive {urlparse(body.url).netloc}" if kind == "website_archive" else ""),
        "kind": kind,
        "source": body.source or kind,
        "drive_id": body.drive_id,
        "folder_id": body.folder_id,
        "folder_name": folder["name"],
        "destination_path": dest_path,
        "worker": worker,
        "tags": clean_tags,
        "options": {
            "via_proton": True,
            "sensitive": True,
            "high_priority": body.high_priority,
            "notify_on_complete": body.notify_on_complete,
        },
        "hf": {
            "repo_id": body.hf_repo_id,
            "mode": body.hf_mode,
            "files": body.hf_files,
        },
        "notes": body.notes.strip(),
    }
    if body.torrent_meta:
        allowed = (
            "jf_score",
            "category",
            "tracker",
            "eng_subs",
            "has_subs",
            "release_kind",
            "show_title_guess",
        )
        job["torrent_meta"] = {
            k: body.torrent_meta[k] for k in allowed if k in body.torrent_meta
        }
    if kind == "website_archive":
        job["options"]["depth"] = body.archive_depth
    job = _enqueue_job(job)
    return JSONResponse({"ok": True, "job": job})


class SyncCreate(BaseModel):
    source_drive_id: str
    source_folder_id: str
    dest_drive_id: str
    dest_folder_id: str
    title: str = ""
    notes: str = ""
    mirror: bool = False
    high_priority: bool = False
    notify_on_complete: bool = False


def _resolve_drive_folder(
    lib: dict[str, Any], drive_id: str, folder_id: str, *, role: str
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    drive = lib.get("drives", {}).get(drive_id)
    if not drive:
        raise HTTPException(400, f"Unknown {role} drive")
    if drive.get("mounted") is False:
        raise HTTPException(503, f"{drive.get('label', drive_id)} is not mounted on the worker")
    folder = next((f for f in drive["folders"] if f["id"] == folder_id), None)
    if not folder:
        raise HTTPException(400, f"Unknown {role} folder")
    if folder.get("present") is False:
        raise HTTPException(400, f"{role.title()} folder not on worker disk: {folder.get('rel')}")
    return drive, folder, _folder_abs_path(drive, folder)


@app.post("/api/jobs/sync")
async def create_sync_job(body: SyncCreate) -> JSONResponse:
    lib = _load_library()
    _, src_folder, src_path = _resolve_drive_folder(
        lib, body.source_drive_id, body.source_folder_id, role="source"
    )
    _, dest_folder, dest_path = _resolve_drive_folder(
        lib, body.dest_drive_id, body.dest_folder_id, role="destination"
    )
    if src_path == dest_path:
        raise HTTPException(400, "Source and destination paths are the same")
    title = body.title.strip() or f"Copy {src_folder['name']} → {dest_folder['name']}"
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "created_at": _now(),
        "status": "queued",
        "url": "",
        "title": title,
        "kind": "sync",
        "source": "sync",
        "drive_id": body.dest_drive_id,
        "folder_id": body.dest_folder_id,
        "folder_name": dest_folder["name"],
        "source_drive_id": body.source_drive_id,
        "source_folder_id": body.source_folder_id,
        "source_folder_name": src_folder["name"],
        "source_path": str(src_path),
        "destination_path": str(dest_path),
        "worker": "rsync",
        "tags": [],
        "options": {
            "via_proton": False,
            "sensitive": False,
            "mirror": body.mirror,
            "high_priority": body.high_priority,
            "notify_on_complete": body.notify_on_complete,
        },
        "hf": {"repo_id": "", "mode": "whole", "files": []},
        "notes": body.notes.strip(),
        "error": None,
        "started_at": None,
        "finished_at": None,
    }
    job = _enqueue_job(job)
    return JSONResponse({"ok": True, "job": job})


def _read_job(job_id: str) -> dict[str, Any]:
    path = JOBS_SUB / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "Job not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _sync_agent_review_file(job: dict[str, Any]) -> None:
    AGENT_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    jid = str(job.get("id") or "")
    if not jid:
        return
    path = AGENT_REVIEW_DIR / f"{jid}.md"
    ar = job.get("agent_review")
    if not ar:
        if path.exists():
            path.unlink()
        return
    path.write_text(agent_review_markdown(job), encoding="utf-8")


def _apply_job_health(job: dict[str, Any]) -> None:
    """Set or clear agent_review from progress + status."""
    review = evaluate_job_health(job)
    if review:
        prev_code = (job.get("agent_review") or {}).get("code")
        if prev_code != review.get("code") or not job.get("agent_review"):
            job["agent_review"] = review
    elif job.get("agent_review"):
        job.pop("agent_review", None)


def _scan_running_jobs_health() -> int:
    updated = 0
    for job in _load_all_jobs():
        if job.get("status") not in ("running", "claimed"):
            continue
        before = job.get("agent_review")
        _apply_job_health(job)
        if job.get("agent_review") != before:
            _write_job(job)
            updated += 1
    return updated


def _write_job(job: dict[str, Any]) -> None:
    path = JOBS_SUB / f"{job['id']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    _sync_agent_review_file(job)


def _require_worker(x_worker_token: str | None) -> None:
    if not WORKER_TOKEN:
        raise HTTPException(503, "HUB_WORKER_TOKEN not configured on Hub")
    if not x_worker_token or x_worker_token != WORKER_TOKEN:
        raise HTTPException(401, "Invalid worker token")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _job_public(_read_job(job_id))


class JobUpdate(BaseModel):
    status: str | None = None
    error: str | None = None
    progress: dict[str, Any] | None = None
    aria2_gid: str | None = None
    media_pipeline: dict[str, Any] | None = None
    agent_review: dict[str, Any] | None = None


@app.patch("/api/jobs/{job_id}")
def patch_job(
    job_id: str,
    body: JobUpdate,
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    job = _read_job(job_id)
    prev_status = job.get("status")
    if body.status is not None:
        allowed = {"queued", "claimed", "running", "paused", "done", "error"}
        if body.status not in allowed:
            raise HTTPException(400, f"status must be one of {sorted(allowed)}")
        job["status"] = body.status
        if body.status == "queued":
            job.pop("claimed_by", None)
        if body.status == "running":
            job["error"] = None
        if body.status in ("claimed", "running") and not job.get("started_at"):
            job["started_at"] = _now()
        if body.status == "paused":
            job.setdefault("paused_at", _now())
        if body.status in ("done", "error"):
            job["finished_at"] = _now()
    if body.error is not None:
        job["error"] = body.error
    if body.progress is not None:
        job["progress"] = body.progress
        job["progress_watch"] = update_progress_watch(job, body.progress)
        _apply_job_health(job)
    if body.aria2_gid is not None:
        job["aria2_gid"] = body.aria2_gid
    if body.media_pipeline is not None:
        job["media_pipeline"] = body.media_pipeline
    if "agent_review" in body.model_fields_set:
        if body.agent_review is None:
            job.pop("agent_review", None)
        else:
            job["agent_review"] = body.agent_review
    _write_job(job)
    try:
        from notify import maybe_notify_job

        maybe_notify_job(prev_status, job)
    except Exception as exc:  # noqa: BLE001
        print(f"notify hook failed: {exc}", flush=True)
    return {"ok": True, "job": job}


def _is_aria2_job(job: dict[str, Any]) -> bool:
    if job.get("worker") == "aria2":
        return True
    kind = (job.get("kind") or "http").lower()
    return kind in ("http", "magnet")


def _job_kind_bucket(job: dict[str, Any]) -> str:
    kind = (job.get("kind") or "http").lower()
    if _is_aria2_job(job):
        return "aria2"
    if kind == "video":
        return "video"
    if kind == "website_archive":
        return "archive"
    if kind in ("sync", "storage_delete"):
        return "exclusive"
    return "other"


def _active_work_counts() -> dict[str, int]:
    counts = {"aria2": 0, "video": 0, "archive": 0, "exclusive": 0, "other": 0}
    for job in _load_all_jobs():
        if job.get("status") not in ("running", "claimed"):
            continue
        bucket = _job_kind_bucket(job)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _job_claim_allowed(job: dict[str, Any], counts: dict[str, int]) -> bool:
    bucket = _job_kind_bucket(job)
    if bucket == "aria2":
        return counts["aria2"] < HUB_MAX_CONCURRENT_ARIA2
    if bucket == "video":
        return counts["video"] < HUB_SLOT_VIDEO
    if bucket == "archive":
        if counts["aria2"] > 0:
            return False
        if HUB_SLOT_ARCHIVE <= 0:
            return counts["archive"] < 1 and counts["exclusive"] == 0
        return counts["archive"] < HUB_SLOT_ARCHIVE
    if bucket == "exclusive":
        return (
            counts["aria2"] == 0
            and counts["video"] == 0
            and counts["archive"] == 0
            and counts["exclusive"] == 0
        )
    return counts["aria2"] == 0 and counts["exclusive"] == 0


def _active_job_count(exclude_id: str | None = None) -> int:
    n = 0
    for job in _load_all_jobs():
        if exclude_id and job.get("id") == exclude_id:
            continue
        if job.get("status") in ("running", "claimed"):
            n += 1
    return n


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str) -> dict[str, Any]:
    """Pause a running aria2 download (worker applies aria2.pause within one poll)."""
    job = _read_job(job_id)
    if not _is_aria2_job(job):
        raise HTTPException(400, "Only aria2 downloads can be paused")
    if job.get("status") not in ("running", "claimed"):
        raise HTTPException(400, f"Cannot pause job in status {job.get('status')}")
    if not job.get("aria2_gid"):
        raise HTTPException(400, "No aria2 download in progress yet")
    job["status"] = "paused"
    job["paused_at"] = _now()
    _write_job(job)
    return {"ok": True, "job": _job_public(job)}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict[str, Any]:
    """Resume a paused aria2 job (re-queues when another download is active)."""
    job = _read_job(job_id)
    if not _is_aria2_job(job):
        raise HTTPException(400, "Only aria2 downloads can be resumed")
    if job.get("status") != "paused":
        raise HTTPException(400, f"Cannot resume job in status {job.get('status')}")
    if not job.get("aria2_gid"):
        raise HTTPException(400, "Missing aria2_gid — use Retry instead")
    opts = dict(job.get("options") or {})
    opts["high_priority"] = True
    job["options"] = opts
    job["status"] = "queued"
    job["error"] = None
    job.pop("finished_at", None)
    job.pop("claimed_by", None)
    job["queue_rank"] = _next_queue_rank()
    _write_job(job)
    return {
        "ok": True,
        "job": _job_public(job),
        "other_active": _active_job_count(exclude_id=job_id),
    }


class CancelBody(BaseModel):
    purge: bool = False


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, body: CancelBody | None = None) -> dict[str, Any]:
    """Cancel a queued, running, or paused job. purge=true removes aria2 + partial files."""
    opts = body or CancelBody()
    job = _read_job(job_id)
    if job.get("status") in ("done", "error", "dismissed", "purging"):
        raise HTTPException(400, f"Job already {job['status']}")
    prev_status = job.get("status")
    job["error"] = "Cancelled by user"
    if opts.purge and _is_aria2_job(job):
        job["status"] = "purging"
        job["purge_requested_at"] = _now()
        job.pop("finished_at", None)
    else:
        job["status"] = "error"
        job["finished_at"] = _now()
    _write_job(job)
    return {
        "ok": True,
        "job": _job_public(job),
        "was": prev_status,
        "purge": bool(opts.purge and _is_aria2_job(job)),
    }


@app.get("/api/jobs/purge/pending")
def purge_pending_job(
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    for job in _load_all_jobs():
        if job.get("status") == "purging":
            return {"job": job}
    return {"job": None}


class PurgeCompleteBody(BaseModel):
    removed_paths: list[str] = []
    detail: str = ""


@app.post("/api/jobs/{job_id}/purge-complete")
def purge_complete_job(
    job_id: str,
    body: PurgeCompleteBody,
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    job = _read_job(job_id)
    if job.get("status") != "purging":
        raise HTTPException(400, f"Job is not purging (status={job.get('status')})")
    job["status"] = "dismissed"
    job["finished_at"] = _now()
    job.pop("aria2_gid", None)
    job["progress"] = {}
    if body.removed_paths:
        job["purge_removed_paths"] = body.removed_paths
    if body.detail:
        job["purge_detail"] = body.detail
    _write_job(job)
    return {"ok": True, "job": _job_public(job)}


def _transient_hub_worker_error(raw: str | None) -> bool:
    if not raw:
        return False
    low = raw.lower()
    return any(
        x in low
        for x in (
            "connection refused",
            "errno 111",
            "connection reset",
            "broken pipe",
            "timed out",
            "temporarily unavailable",
        )
    )


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, Any]:
    """Re-queue a failed or cancelled job."""
    job = _read_job(job_id)
    if job.get("status") not in ("error", "dismissed", "done"):
        raise HTTPException(
            400,
            f"Can only retry error/dismissed/done jobs, got {job.get('status')}",
        )
    if job.get("worker") == "aria2":
        aria_err = validate_aria2_destination(str(job.get("destination_path") or ""))
        if aria_err:
            raise HTTPException(400, aria_err)
    keep_aria2 = (
        job.get("worker") == "aria2"
        and job.get("aria2_gid")
        and _transient_hub_worker_error(str(job.get("error") or ""))
    )
    job["status"] = "queued"
    job["error"] = None
    job["finished_at"] = None
    job.pop("claimed_by", None)
    if not keep_aria2:
        job["started_at"] = None
        job.pop("aria2_gid", None)
        job["progress"] = {}
        job["queue_rank"] = _next_queue_rank()
    else:
        opts = dict(job.get("options") or {})
        opts["high_priority"] = True
        job["options"] = opts
    _assign_queue_rank_if_queued(job)
    _write_job(job)
    return {"ok": True, "job": _job_public(job), "reattach_aria2": keep_aria2}


class QueueReorderBody(BaseModel):
    ordered_ids: list[str] = Field(..., min_length=1)


@app.post("/api/jobs/queue/reorder")
def reorder_queued_jobs(body: QueueReorderBody) -> dict[str, Any]:
    """Set queue_rank for all status=queued jobs (downloads queue only; not review)."""
    queued = [j for j in _load_all_jobs() if j.get("status") == "queued"]
    by_id = {str(j["id"]): j for j in queued if j.get("id")}
    expected = set(by_id.keys())
    ordered = [str(x).strip() for x in body.ordered_ids if str(x).strip()]
    if len(ordered) != len(set(ordered)):
        raise HTTPException(400, "duplicate job ids in ordered_ids")
    if set(ordered) != expected:
        raise HTTPException(
            400,
            "ordered_ids must list every queued job exactly once "
            f"({len(expected)} queued, got {len(ordered)} ids)",
        )
    updated: list[dict[str, Any]] = []
    for idx, jid in enumerate(ordered):
        job = by_id[jid]
        job["queue_rank"] = (idx + 1) * 10
        opts = dict(job.get("options") or {})
        opts.pop("high_priority", None)
        job["options"] = opts
        _write_job(job)
        updated.append(_job_public(job))
    updated.sort(key=_job_queue_claim_sort_key)
    return {"ok": True, "jobs": updated}


class WorkerHeartbeat(BaseModel):
    worker: str = "ubuntu-worker"
    vpn_ok: bool = False
    vpn_detail: str = ""
    media_mounted: bool = False
    tnas_mounted: bool = False
    youtube_cookies: bool = False
    at: str = ""
    drives: list[DiscoveredDrive] = Field(default_factory=list)


@app.post("/api/worker/heartbeat")
def worker_heartbeat(
    body: WorkerHeartbeat,
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    _ensure_dirs()
    merged = False
    if body.drives:
        merged = _merge_library_from_discovery(body.drives)
    WORKER_STATUS_FILE.write_text(body.model_dump_json(indent=2), encoding="utf-8")
    health_updates = _scan_running_jobs_health()
    return {"ok": True, "library_merged": merged, "job_health_updates": health_updates}


@app.get("/api/jobs/agent-review")
def list_agent_review_jobs(limit: int = 40) -> dict[str, Any]:
    """Jobs flagged for agent/human review (stuck metadata, stalls, import failures)."""
    cap = max(1, min(limit, 100))
    flagged: list[dict[str, Any]] = []
    for job in _load_all_jobs():
        if not job.get("agent_review"):
            continue
        flagged.append(_job_public(job))
    flagged.sort(
        key=lambda j: (j.get("agent_review") or {}).get("flagged_at") or "",
        reverse=True,
    )
    return {
        "ok": True,
        "count": len(flagged),
        "jobs": flagged[:cap],
        "brief_dir": str(AGENT_REVIEW_DIR),
    }


@app.get("/api/devops/log")
def devops_log_tail(lines: int = 50) -> dict[str, Any]:
    """Tail the dev-ops log from the last worker heartbeat."""
    ws: dict[str, Any] = {}
    if WORKER_STATUS_FILE.exists():
        try:
            ws = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    log_lines = ws.get("dev_ops_tail", [])
    return {
        "lines": log_lines[-max(1, min(lines, 200)):],
        "total": len(log_lines),
        "worker_at": ws.get("at", ""),
    }


@app.get("/api/worker/aria2-destinations")
def aria2_destinations() -> dict[str, Any]:
    """Static aria2 path rules (compose must match on ai-server)."""
    return {"ok": True, "destinations": aria2_destination_catalog()}


@app.get("/api/status")
def hub_status() -> dict[str, Any]:
    _ensure_dirs()
    ws: dict[str, Any] = {}
    if WORKER_STATUS_FILE.exists():
        try:
            ws = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            ws = {}
    jobs: list[dict[str, Any]] = []
    for path in JOBS_SUB.glob("*.json"):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    counts = Counter(j.get("status", "?") for j in jobs)
    age_s: float | None = None
    at = ws.get("at") or ""
    if at:
        try:
            ts = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        except ValueError:
            age_s = None
    # Worker polls ~10s; treat >25s as stale (fail-closed for "live" VPN).
    stale = age_s is None or age_s > 25
    vpn_ok = bool(ws.get("vpn_ok"))
    return {
        "worker": ws,
        "jobs": dict(counts),
        "form": "ok",
        "vpn_live": bool(vpn_ok and not stale),
        "vpn_stale": stale,
        "heartbeat_age_s": round(age_s, 1) if age_s is not None else None,
        "youtube_cookies": bool(ws.get("youtube_cookies")),
        "aria2_destinations": aria2_destination_catalog(),
        "max_concurrent_aria2": HUB_MAX_CONCURRENT_ARIA2,
        "aria2_active_slots": ws.get("aria2_active_slots"),
        "aria2_max_active": ws.get("aria2_max_active"),
        "aria2_target_mbps": ws.get("aria2_target_mbps"),
        "aria2_total_download_bps": ws.get("aria2_total_download_bps"),
    }


@app.post("/api/jobs/claim")
def claim_job(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Atomically claim the next queued job (priority, queue_rank, slot limits)."""
    _require_worker(x_worker_token)
    _ensure_dirs()
    candidates: list[dict[str, Any]] = []
    for path in JOBS_SUB.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if job.get("status") == "queued":
            candidates.append(job)
    if not candidates:
        return {"ok": True, "job": None}

    candidates.sort(key=_job_queue_claim_sort_key)
    counts = _active_work_counts()
    job: dict[str, Any] | None = None
    for candidate in candidates:
        if _job_claim_allowed(candidate, counts):
            job = candidate
            break
    if job is None:
        return {"ok": True, "job": None}
    # re-read to reduce races
    fresh = _read_job(job["id"])
    if fresh.get("status") != "queued":
        return {"ok": True, "job": None}
    if not _job_claim_allowed(fresh, _active_work_counts()):
        return {"ok": True, "job": None}
    fresh["status"] = "claimed"
    fresh["started_at"] = _now()
    fresh["claimed_by"] = "ubuntu-worker"
    _write_job(fresh)
    return {"ok": True, "job": fresh}


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return FORM_PATH.read_text(encoding="utf-8")


@app.get("/capture", response_class=HTMLResponse)
def capture_page() -> str:
    """Thin Access phone queuer (YES link / NO note)."""
    if not CAPTURE_PATH.is_file():
        raise HTTPException(404, "capture.html missing")
    return CAPTURE_PATH.read_text(encoding="utf-8")


@app.get("/storage", response_class=HTMLResponse)
def storage_page() -> str:
    """Full-page storage visual inspector (Hub root via Caddy /storage)."""
    if not STORAGE_PATH.is_file():
        raise HTTPException(404, "storage.html missing")
    return STORAGE_PATH.read_text(encoding="utf-8")


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> str:
    """Chat wrapper with on-demand LLM toggle (Hub root via Caddy /chat)."""
    if not CHAT_PATH.is_file():
        raise HTTPException(404, "chat.html missing")
    return CHAT_PATH.read_text(encoding="utf-8")


@app.get("/overnight", response_class=HTMLResponse)
def overnight_page() -> str:
    """Overnight drag-and-drop schedule planner."""
    if not OVERNIGHT_PATH.is_file():
        raise HTTPException(404, "overnight.html missing")
    return OVERNIGHT_PATH.read_text(encoding="utf-8")


@app.get("/indexers", response_class=HTMLResponse)
def indexers_page() -> str:
    """Jackett indexer health + megathread catalog."""
    if not INDEXERS_PATH.is_file():
        raise HTTPException(404, "indexers.html missing")
    return INDEXERS_PATH.read_text(encoding="utf-8")


@app.get("/assets/hub-overnight.js")
def hub_overnight_js() -> FileResponse:
    if not OVERNIGHT_JS_PATH.is_file():
        raise HTTPException(404, "hub-overnight.js missing")
    return FileResponse(
        OVERNIGHT_JS_PATH,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/assets/hub-design.css")
def hub_design_css() -> FileResponse:
    if not HUB_DESIGN_CSS_PATH.is_file():
        raise HTTPException(404, "hub-design.css missing")
    return FileResponse(
        HUB_DESIGN_CSS_PATH,
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/vendor/gp-treemap.bundle.js")
def gp_treemap_bundle() -> FileResponse:
    """Vendored imbue-ai/gp-treemap (GPL-2) for /storage visualiser."""
    path = VENDOR_DIR / "gp-treemap.bundle.js"
    if not path.is_file():
        raise HTTPException(404, "gp-treemap bundle missing")
    return FileResponse(
        path,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _storage_path_allowed(path_str: str) -> Path:
    raw = (path_str or "").strip()
    if not raw or ".." in Path(raw).parts:
        raise HTTPException(400, "Invalid path")
    path = Path(raw)
    if not path.is_absolute():
        raise HTTPException(400, "Path must be absolute")
    resolved = str(path)
    if not any(
        resolved == prefix or resolved.startswith(prefix + "/")
        for prefix in STORAGE_ALLOWLIST_PREFIXES
    ):
        raise HTTPException(400, "Path outside allowed storage mounts")
    for prefix in STORAGE_ALLOWLIST_PREFIXES:
        if resolved == prefix or resolved == prefix + "/":
            raise HTTPException(400, "Refusing action on volume root")
    return path


def _read_storage_scan_status() -> dict[str, Any]:
    if not STORAGE_SCAN_STATUS_FILE.is_file():
        return {"status": "idle"}
    try:
        return json.loads(STORAGE_SCAN_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "idle"}


@app.get("/api/storage/volumes")
def storage_volumes() -> dict[str, Any]:
    """Mounted flags from last worker heartbeat / library."""
    lib = _load_library()
    status: dict[str, Any] = {}
    if WORKER_STATUS_FILE.is_file():
        try:
            status = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            status = {}
    drives = lib.get("drives") or {}
    volumes = []
    for vid, root in STORAGE_VOLUME_ROOTS.items():
        mounted = False
        label = vid
        if vid == "media":
            mounted = bool(status.get("media_mounted"))
            label = (drives.get("media") or {}).get("label") or "Media HDD"
            if not mounted and (drives.get("media") or {}).get("mounted") is True:
                mounted = True
        elif vid == "tnas":
            mounted = bool(status.get("tnas_mounted"))
            label = (drives.get("nas") or {}).get("label") or "TNAS"
            if not mounted and (drives.get("nas") or {}).get("mounted") is True:
                mounted = True
        else:
            # Any removable / external drive marked mounted in library
            for did, d in drives.items():
                if d.get("removable") and d.get("mounted") is not False:
                    root_d = str(d.get("root") or "")
                    if root_d.startswith("/mnt/external") or did.startswith("usb"):
                        mounted = True
                        label = d.get("label") or "USB"
                        break
            if not mounted:
                # Still offer tab if tree cache exists (may remount on scan)
                tree_file = STORAGE_TREES_DIR / f"{vid}.json"
                mounted = tree_file.is_file()
                label = "USB"
        volumes.append(
            {
                "id": vid,
                "label": label,
                "root": root,
                "mounted": mounted,
            }
        )
    return {"volumes": volumes}


@app.get("/api/storage/tree")
def storage_tree(volume: str = "media") -> dict[str, Any]:
    if volume not in STORAGE_VOLUME_ROOTS:
        raise HTTPException(400, "Unknown volume")
    STORAGE_TREES_DIR.mkdir(parents=True, exist_ok=True)
    path = STORAGE_TREES_DIR / f"{volume}.json"
    scan = _read_storage_scan_status()
    if not path.is_file():
        return {
            "volume": volume,
            "tree": None,
            "scanned_at": None,
            "scan_status": scan.get("status", "idle"),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Corrupt tree cache: {exc}") from exc
    return {
        "volume": volume,
        "tree": data.get("tree"),
        "scanned_at": data.get("scanned_at"),
        "scan_status": scan.get("status", "idle"),
        "error": data.get("error"),
    }


class StorageScanBody(BaseModel):
    volume: str = "media"


@app.post("/api/storage/scan")
def storage_scan_request(body: StorageScanBody) -> dict[str, Any]:
    if body.volume not in STORAGE_VOLUME_ROOTS and body.volume != "all":
        raise HTTPException(400, "Unknown volume")
    _ensure_dirs()
    STORAGE_TREES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "volume": body.volume,
        "requested_at": _now(),
    }
    STORAGE_SCAN_REQUEST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    STORAGE_SCAN_STATUS_FILE.write_text(
        json.dumps(
            {"status": "queued", "volume": body.volume, "updated_at": _now()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"ok": True, "volume": body.volume}


@app.get("/api/storage/scan/pending")
def storage_scan_pending(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_worker(x_worker_token)
    if not STORAGE_SCAN_REQUEST_FILE.is_file():
        return {"pending": False}
    try:
        req = json.loads(STORAGE_SCAN_REQUEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        STORAGE_SCAN_REQUEST_FILE.unlink(missing_ok=True)
        return {"pending": False}
    STORAGE_SCAN_REQUEST_FILE.unlink(missing_ok=True)
    STORAGE_SCAN_STATUS_FILE.write_text(
        json.dumps(
            {
                "status": "running",
                "volume": req.get("volume"),
                "updated_at": _now(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"pending": True, "request": req}


@app.post("/api/storage/scan/complete")
def storage_scan_complete(
    body: dict[str, Any] = Body(...),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    volume = str(body.get("volume") or "")
    if volume not in STORAGE_VOLUME_ROOTS:
        raise HTTPException(400, "Unknown volume")
    STORAGE_TREES_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "volume": volume,
        "scanned_at": body.get("scanned_at") or time.time(),
        "tree": body.get("tree"),
        "error": body.get("error"),
    }
    (STORAGE_TREES_DIR / f"{volume}.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    STORAGE_SCAN_STATUS_FILE.write_text(
        json.dumps(
            {
                "status": "idle" if not body.get("error") else "error",
                "volume": volume,
                "updated_at": _now(),
                "error": body.get("error"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"ok": True}


@app.get("/api/storage/scan/status")
def storage_scan_status() -> dict[str, Any]:
    return _read_storage_scan_status()


class StorageDeleteBody(BaseModel):
    path: str


@app.post("/api/storage/delete")
def storage_delete(body: StorageDeleteBody) -> JSONResponse:
    path = _storage_path_allowed(body.path)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "created_at": _now(),
        "status": "queued",
        "url": "",
        "title": f"Delete {path.name}",
        "kind": "storage_delete",
        "source": "storage",
        "drive_id": "",
        "folder_id": "",
        "folder_name": "",
        "source_path": str(path),
        "destination_path": "",
        "worker": "storage",
        "tags": [],
        "options": {"via_proton": False, "sensitive": True},
        "hf": {"repo_id": "", "mode": "whole", "files": []},
        "notes": "",
        "error": None,
        "started_at": None,
        "finished_at": None,
    }
    job = _enqueue_job(job)
    return JSONResponse({"ok": True, "job": job})


class StorageCopyBody(BaseModel):
    path: str
    dest_drive_id: str
    dest_folder_id: str
    title: str = ""


@app.post("/api/storage/copy")
def storage_copy(body: StorageCopyBody) -> JSONResponse:
    src = _storage_path_allowed(body.path)
    lib = _load_library()
    _, dest_folder, dest_path = _resolve_drive_folder(
        lib, body.dest_drive_id, body.dest_folder_id, role="destination"
    )
    title = body.title.strip() or f"Copy {src.name} → {dest_folder['name']}"
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "created_at": _now(),
        "status": "queued",
        "url": "",
        "title": title,
        "kind": "sync",
        "source": "storage",
        "drive_id": body.dest_drive_id,
        "folder_id": body.dest_folder_id,
        "folder_name": dest_folder["name"],
        "source_drive_id": "",
        "source_folder_id": "",
        "source_folder_name": src.name,
        "source_path": str(src),
        "destination_path": str(dest_path),
        "worker": "rsync",
        "tags": [],
        "options": {
            "via_proton": False,
            "sensitive": False,
            "mirror": False,
            "high_priority": False,
            "notify_on_complete": False,
        },
        "hf": {"repo_id": "", "mode": "whole", "files": []},
        "notes": "",
        "error": None,
        "started_at": None,
        "finished_at": None,
    }
    job = _enqueue_job(job)
    return JSONResponse({"ok": True, "job": job})


class PhoneRequestBody(BaseModel):
    mode: str = Field(..., pattern="^(link|note)$")
    url: str = ""
    text: str = ""
    title: str = ""
    kind: str = "auto"
    drive_id: str = ""
    folder_id: str = ""
    requested_by: str = "hub"


@app.get("/api/requests/summary")
def requests_summary() -> dict[str, Any]:
    """Pending shared Requests count for Hub badge."""
    pending = 0
    for job in _load_all_jobs():
        if job.get("status") == "review":
            pending += 1
    return {"pending": pending}


@app.post("/api/requests")
def create_phone_request(body: PhoneRequestBody) -> JSONResponse:
    """Phone / Access queuer: link → download queue; note → shared Requests review."""
    lib = _load_library()
    who = (body.requested_by or "hub").strip().lower() or "hub"
    who = re.sub(r"[^a-z0-9_-]", "", who)[:32] or "hub"

    if body.mode == "note":
        notes = (body.text or body.title or "").strip()
        if not notes:
            raise HTTPException(400, "Enter a short note or project name")
        title = (body.title or notes).strip()
        if len(title) > 80:
            title = title[:77] + "..."
        # Park on NAS inbox when mounted; else first mounted folder
        try:
            drive_id, folder_id, drive, folder = _resolve_capture_destination(lib, "default")
        except HTTPException:
            raise HTTPException(503, "No mounted drive for Requests — mount Media HDD or TNAS")
        dest_path = str(_folder_abs_path(drive, folder))
        job = _enqueue_job(
            {
                "status": "review",
                "url": "",
                "title": title,
                "kind": "request",
                "source": "phone-note",
                "requested_by": who,
                "drive_id": drive_id,
                "folder_id": folder_id,
                "folder_name": folder["name"],
                "destination_path": dest_path,
                "worker": "manual",
                "tags": ["request", "phone"],
                "options": {
                    "via_proton": False,
                    "sensitive": True,
                    "high_priority": False,
                    "notify_on_complete": False,
                    "captured_via": "phone",
                },
                "hf": {"repo_id": "", "mode": "whole", "files": []},
                "notes": notes,
            }
        )
        return JSONResponse(
            {
                "ok": True,
                "queued": False,
                "status": "review",
                "job_id": job["id"],
                "detail": "Saved to Requests — review when home",
            }
        )

    # mode=link
    url = (body.url or body.text or "").strip()
    if not (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("magnet:")
    ):
        raise HTTPException(400, "URL must start with http(s):// or magnet:")
    kind = body.kind if body.kind != "auto" else _detect_kind(url, "")
    drive_id = body.drive_id.strip()
    folder_id = body.folder_id.strip()
    if not drive_id or not folder_id:
        raise HTTPException(400, "Pick a drive and folder")
    drive = lib.get("drives", {}).get(drive_id)
    if not drive:
        raise HTTPException(400, "Unknown drive")
    if drive.get("mounted") is False:
        raise HTTPException(503, f"{drive.get('label', drive_id)} is not mounted")
    folder = next((f for f in drive.get("folders") or [] if f.get("id") == folder_id), None)
    if not folder:
        raise HTTPException(400, "Unknown folder")
    if folder.get("present") is False:
        raise HTTPException(400, f"Folder not found on disk: {folder.get('rel')}")
    dest_path = str(_folder_abs_path(drive, folder))
    if kind == "video":
        worker = "metube"
    elif kind in ("github", "hf"):
        worker = "repo"
    else:
        worker = "aria2"
    title = (body.title or "").strip() or url[:80]
    job = _enqueue_job(
        {
            "url": url,
            "title": title,
            "kind": kind,
            "source": "phone-link",
            "requested_by": who,
            "drive_id": drive_id,
            "folder_id": folder_id,
            "folder_name": folder["name"],
            "destination_path": dest_path,
            "worker": worker,
            "tags": ["phone"],
            "options": {
                "via_proton": True,
                "sensitive": True,
                "high_priority": False,
                "notify_on_complete": False,
                "captured_via": "phone",
            },
            "hf": {"repo_id": "", "mode": "whole", "files": []},
            "notes": (body.text or "").strip(),
        }
    )
    return JSONResponse(
        {
            "ok": True,
            "queued": True,
            "status": "queued",
            "job_id": job["id"],
            "detail": f"Queued to {drive.get('label') or drive_id} / {folder['name']}",
        }
    )


class SubmitterSubmit(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    key: str = ""


@app.get("/api/request/{slug}/config")
def submitter_config(slug: str) -> dict[str, Any]:
    scfg = _submitter_cfg(slug)
    canonical = _resolve_submitter_slug(slug)
    env_name = (scfg.get("token_env") or "").strip()
    requires_key = bool(env_name and os.environ.get(env_name, "").strip())
    return {
        "slug": canonical,
        "display_name": scfg.get("display_name", canonical),
        "greeting": scfg.get("greeting", ""),
        "placeholder": scfg.get("placeholder", ""),
        "requires_key": requires_key,
    }


@app.post("/api/request/{slug}/submit")
def submitter_request(slug: str, body: SubmitterSubmit) -> dict[str, Any]:
    canonical = _resolve_submitter_slug(slug)
    if not canonical:
        raise HTTPException(404, "Unknown request page")
    scfg = _submitter_cfg(slug)
    _verify_submitter_token(scfg, body.key.strip() or None)
    drive, folder, dest_path = _resolve_submitter_destination(scfg)
    try:
        url, kind, title = _parse_submitter_text(body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    tag = (scfg.get("tag") or f"{canonical}-request").strip().lower()
    tags = [tag] if tag else []
    status = "queued"
    notes = body.text.strip()
    if kind == "request" or not url:
        status = "review"
        url = ""
        title = title or notes[:80]
        worker = "manual"
    elif kind == "video":
        worker = "metube"
    elif kind in ("github", "hf"):
        worker = "repo"
    else:
        worker = "aria2"

    job = _enqueue_job(
        {
            "status": status,
            "url": url,
            "title": title or (url[:80] if url else notes[:80]),
            "kind": kind if url else "request",
            "source": canonical,
            "requested_by": canonical,
            "drive_id": scfg.get("default_drive_id", "media_hdd"),
            "folder_id": folder["id"],
            "folder_name": folder["name"],
            "destination_path": str(dest_path),
            "worker": worker,
            "tags": tags,
            "options": {
                "via_proton": bool(url),
                "sensitive": True,
                "high_priority": False,
                "notify_on_complete": False,
            },
            "hf": {"repo_id": "", "mode": "whole", "files": []},
            "notes": notes,
        }
    )
    return {"ok": True, "queued": status == "queued", "job_id": job["id"], "status": status}


class ApproveBody(BaseModel):
    url: str = ""


def _worker_for_kind(kind: str) -> str:
    if kind == "video":
        return "metube"
    if kind in ("github", "hf"):
        return "repo"
    return "aria2"


@app.post("/api/jobs/{job_id}/approve")
def approve_review_job(
    job_id: str, body: ApproveBody = Body(default_factory=ApproveBody)
) -> dict[str, Any]:
    job = _read_job(job_id)
    if job.get("status") != "review":
        raise HTTPException(400, "Only review jobs can be approved")
    url = (body.url or "").strip() or (job.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Paste a download URL before approving")
    job["url"] = url
    job["kind"] = _detect_kind(url, job.get("source") or "")
    job["worker"] = _worker_for_kind(job["kind"])
    job["options"] = {**(job.get("options") or {}), "via_proton": True}
    job["status"] = "queued"
    job["error"] = None
    _write_job(job)
    return {"ok": True, "job": job}


@app.post("/api/jobs/{job_id}/dismiss")
def dismiss_review_job(job_id: str) -> dict[str, Any]:
    """Remove a job from the active queue (review, failed, or waiting)."""
    job = _read_job(job_id)
    allowed = {"review", "error", "queued", "done"}
    if job.get("status") not in allowed:
        raise HTTPException(
            400,
            f"Only review, error, queued, or done jobs can be dismissed, got {job.get('status')}",
        )
    job["status"] = "dismissed"
    job["finished_at"] = _now()
    job["error"] = None
    _write_job(job)
    return {"ok": True, "job": _job_public(job)}


@app.post("/api/jobs/dismiss-queue")
def dismiss_queue_jobs() -> dict[str, Any]:
    """Dismiss all review, error, and queued jobs (clears Active/Failed queue UI)."""
    dismissed: list[str] = []
    for job in _load_all_jobs():
        if job.get("status") not in ("review", "error", "queued"):
            continue
        job["status"] = "dismissed"
        job["finished_at"] = _now()
        job["error"] = None
        _write_job(job)
        dismissed.append(str(job.get("id") or ""))
    return {"ok": True, "count": len(dismissed), "ids": dismissed}


class MinecraftPower(BaseModel):
    action: str = Field(..., pattern="^(start|stop)$")


class MinecraftMapSelect(BaseModel):
    map_id: str = Field(..., min_length=1)


@app.get("/api/minecraft/status")
def minecraft_status() -> dict[str, Any]:
    return _mc_proxy("GET", "/api/status")


@app.get("/api/minecraft/maps")
def minecraft_maps() -> dict[str, Any]:
    return _mc_proxy("GET", "/api/maps")


@app.get("/api/minecraft/mods")
def minecraft_mods() -> dict[str, Any]:
    return _mc_proxy("GET", "/api/mods")


@app.post("/api/minecraft/power")
def minecraft_power(body: MinecraftPower) -> dict[str, Any]:
    timeout = 300.0 if body.action == "start" else 240.0
    return _mc_proxy("POST", "/api/power", {"action": body.action}, timeout=timeout)


@app.post("/api/minecraft/maps/select")
def minecraft_map_select(body: MinecraftMapSelect) -> dict[str, Any]:
    return _mc_proxy(
        "POST",
        "/api/maps/select",
        {"map_id": body.map_id},
        timeout=900.0,
    )


_machines_status_cache: dict[str, Any] | None = None
_machines_status_cache_at: float = 0.0
MACHINES_STATUS_CACHE_SEC = 20.0


def _invalidate_machines_status_cache() -> None:
    global _machines_status_cache_at
    _machines_status_cache_at = 0.0


def _machines_status_cached() -> dict[str, Any]:
    global _machines_status_cache, _machines_status_cache_at
    now = time.monotonic()
    if (
        _machines_status_cache is not None
        and now - _machines_status_cache_at < MACHINES_STATUS_CACHE_SEC
    ):
        return _machines_status_cache
    data = machines_ctl.list_machines_status(identity=MACHINES_SSH_IDENTITY)
    _machines_status_cache = data
    _machines_status_cache_at = now
    return data


@app.get("/api/machines")
def machines_list() -> dict[str, Any]:
    _ensure_dirs()
    return _machines_status_cached()


@app.post("/api/machines/{machine_id}/wake")
def machines_wake(machine_id: str) -> dict[str, Any]:
    _ensure_dirs()
    _invalidate_machines_status_cache()
    try:
        return machines_ctl.wake_machine(machine_id)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown machine: {machine_id}") from exc
    except Exception as exc:
        machines_ctl.record_action(machine_id, "wake", False, str(exc))
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/machines/{machine_id}/sleep")
def machines_sleep(machine_id: str) -> dict[str, Any]:
    _ensure_dirs()
    _invalidate_machines_status_cache()
    try:
        return machines_ctl.sleep_machine(machine_id, identity=MACHINES_SSH_IDENTITY)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown machine: {machine_id}") from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _migrate_overnight_schedule(sched: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy duration_min / step types for complete-or-timeout runner."""
    sched.setdefault("advance_mode", "complete_or_timeout")
    type_map = {
        "research_synthesis": "dsh_research",
        "log_review": "dsh_log_review",
    }
    for step in sched.get("steps") or []:
        if step.get("type") in type_map:
            step["type"] = type_map[step["type"]]
        if "timeout_min" not in step and "duration_min" in step:
            step["timeout_min"] = step.pop("duration_min")
    return sched


def _load_overnight_schedule() -> dict[str, Any]:
    _ensure_dirs()
    if OVERNIGHT_SCHEDULE_FILE.is_file():
        try:
            data = json.loads(OVERNIGHT_SCHEDULE_FILE.read_text(encoding="utf-8"))
            migrated = _migrate_overnight_schedule(data)
            if migrated != data:
                OVERNIGHT_SCHEDULE_FILE.write_text(
                    json.dumps(migrated, indent=2) + "\n", encoding="utf-8"
                )
            return migrated
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, f"Corrupt overnight schedule: {exc}") from exc
    if DEFAULT_OVERNIGHT_SCHEDULE.is_file():
        data = json.loads(DEFAULT_OVERNIGHT_SCHEDULE.read_text(encoding="utf-8"))
        data = _migrate_overnight_schedule(data)
        OVERNIGHT_SCHEDULE_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return data
    raise HTTPException(503, "No overnight schedule template")


@app.get("/api/llm/status")
def llm_status_api() -> dict[str, Any]:
    _ensure_dirs()
    try:
        return llm_ctl.llm_status(identity=MACHINES_SSH_IDENTITY)
    except Exception as exc:
        return {"state": "error", "model": None, "error": str(exc)}


@app.post("/api/llm/start")
def llm_start_api() -> dict[str, Any]:
    _ensure_dirs()
    try:
        return llm_ctl.llm_start(identity=MACHINES_SSH_IDENTITY)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/llm/stop")
def llm_stop_api() -> dict[str, Any]:
    _ensure_dirs()
    try:
        return llm_ctl.llm_stop(identity=MACHINES_SSH_IDENTITY)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


LLM_CHAT_URL = os.environ.get(
    "LLM_CHAT_URL", "http://192.168.50.65:8000/v1/chat/completions"
)
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5-coder-14b")


class SmartDownloadBody(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=500)
    auto_queue: bool = False


def _llm_chat(messages: list[dict[str, str]], max_tokens: int = 300) -> str:
    """Call local vLLM and return assistant content."""
    payload = json.dumps(
        {"model": LLM_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    ).encode()
    req = URLRequest(LLM_CHAT_URL, payload, {"Content-Type": "application/json"})
    req.method = "POST"
    with urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"].strip()


@app.post("/api/smart-download")
def smart_download(body: SmartDownloadBody) -> dict[str, Any]:
    """LLM-powered natural language → torrent search → queue."""
    _ensure_dirs()

    # Step 1: LLM generates an optimised search query
    query_prompt = [
        {
            "role": "system",
            "content": (
                "You are a torrent search query optimiser. "
                "The user describes what they want to download. "
                "Generate the BEST short search query for a torrent indexer "
                "(like 1337x or TPB). Output ONLY the search query, nothing else. "
                "Use common release group conventions (e.g. 1080p, S01, x265)."
            ),
        },
        {"role": "user", "content": body.prompt},
    ]
    try:
        search_query = _llm_chat(query_prompt, max_tokens=60)
    except Exception as exc:
        raise HTTPException(502, f"LLM unreachable: {exc}") from exc

    # Clean up — LLM sometimes wraps in quotes
    search_query = search_query.strip().strip('"').strip("'").strip("`")
    if not search_query:
        raise HTTPException(400, "LLM returned empty query")

    # Step 2: Search via existing Hub search API (relay to worker/Jackett)
    try:
        sb = SearchBody(query=search_query, source="torrent")
        search_data = search(sb)
    except Exception as exc:
        search_data = {"results": [], "errors": [str(exc)]}

    results = search_data.get("results", [])
    if not results:
        return {
            "search_query": search_query,
            "results": [],
            "pick": None,
            "queued": False,
            "message": "No torrents found. Try rephrasing.",
            "errors": search_data.get("errors", []),
        }

    # Step 3: LLM picks the best result
    result_lines = []
    for i, r in enumerate(results[:15]):
        seeds = r.get("seeders") or r.get("seeds", "?")
        size_mb = round(r.get("size", 0) / 1048576) if r.get("size") else "?"
        result_lines.append(f"{i+1}. {r.get('title','')} — {size_mb}MB, {seeds} seeds")

    pick_prompt = [
        {
            "role": "system",
            "content": (
                "You are a download assistant. The user wants: "
                f'"{body.prompt}"\n\n'
                "Pick the BEST torrent from the results below. Consider: "
                "relevance to request, file size (bigger is usually better for "
                "video/media), seeds (more = faster). "
                "Reply with ONLY the result number (e.g. '1'), nothing else."
            ),
        },
        {"role": "user", "content": "\n".join(result_lines)},
    ]
    try:
        pick_raw = _llm_chat(pick_prompt, max_tokens=10)
        pick_num = int("".join(c for c in pick_raw if c.isdigit())[:2]) - 1
        if pick_num < 0 or pick_num >= len(results):
            pick_num = 0
    except Exception:
        pick_num = 0

    chosen = results[pick_num]

    # Step 4: Auto-queue if requested
    queued = False
    job_id = None
    if body.auto_queue and chosen.get("magnet"):
        try:
            job = {
                "id": str(uuid.uuid4())[:8],
                "url": chosen["magnet"],
                "title": chosen.get("title", ""),
                "kind": "magnet",
                "status": "queued",
                "created_at": _now(),
                "tags": ["smart-download"],
                "notes": f"AI pick for: {body.prompt}",
            }
            # Use default destination
            lib = _load_library()
            drives = lib.get("drives", [])
            if drives:
                job["drive_id"] = drives[0].get("id", "")
                folders = drives[0].get("folders", [])
                if folders:
                    job["folder_id"] = folders[0].get("id", "")
            _enqueue_job(job)
            queued = True
            job_id = job["id"]
        except Exception:
            pass

    return {
        "search_query": search_query,
        "results": [
            {
                "title": r.get("title", ""),
                "size": r.get("size"),
                "seeds": r.get("seeders") or r.get("seeds"),
                "source": r.get("source", "torrent"),
                "url": r.get("url", ""),
                "magnet": bool(r.get("magnet")),
            }
            for r in results[:10]
        ],
        "pick": {
            "index": pick_num,
            "title": chosen.get("title", ""),
            "magnet": chosen.get("magnet", chosen.get("url", "")),
        },
        "queued": queued,
        "job_id": job_id,
        "message": f"Best match: {chosen.get('title', '')}",
    }


@app.get("/api/overnight/schedule")
def overnight_schedule_get() -> dict[str, Any]:
    return {"schedule": _load_overnight_schedule()}


class OvernightScheduleBody(BaseModel):
    schedule: dict[str, Any]


@app.put("/api/overnight/schedule")
def overnight_schedule_put(body: OvernightScheduleBody) -> dict[str, Any]:
    _ensure_dirs()
    sched = body.schedule
    if not isinstance(sched.get("steps"), list):
        raise HTTPException(400, "schedule.steps must be a list")
    OVERNIGHT_SCHEDULE_FILE.write_text(json.dumps(sched, indent=2) + "\n", encoding="utf-8")
    return {"ok": True}


class OvernightRunBody(BaseModel):
    dry_run: bool = False


@app.post("/api/overnight/run")
def overnight_run_trigger(body: OvernightRunBody = Body(default_factory=OvernightRunBody)) -> dict[str, Any]:
    """Trigger overnight runner (background subprocess on Pi)."""
    _ensure_dirs()
    script = Path(os.environ.get("OVERNIGHT_RUN_SCRIPT", "")).expanduser()
    if not script.is_file():
        script = Path(__file__).with_name("scripts/overnight-run.py")
    if not script.is_file():
        script = Path.home() / "homelab/scripts/overnight-run.py"
    if not script.is_file():
        raise HTTPException(503, "overnight-run.py not found on Hub host")
    import subprocess

    args = ["python3", str(script)]
    if body.dry_run:
        args.append("--dry-run")
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "HUB_BASE": "http://127.0.0.1:3020", "JOBS_DIR": str(JOBS_DIR)},
    )
    return {"ok": True, "pid": proc.pid, "message": "Overnight run started in background"}


@app.get("/api/overnight/status")
def overnight_run_status() -> dict[str, Any]:
    """Live or last overnight run state for the planner UI."""
    _ensure_dirs()
    if OVERNIGHT_STATE_FILE.is_file():
        try:
            return json.loads(OVERNIGHT_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"status": "idle"}


@app.get("/api/overnight/last-night")
def overnight_last_night() -> dict[str, Any]:
    """Combined nightly review: overnight run results + prior day job summary."""
    _ensure_dirs()

    # 1. Overnight run results (from JSONL log)
    run_steps: list[dict[str, Any]] = []
    run_meta: dict[str, Any] = {"status": "idle"}
    tz = ZoneInfo("Australia/Brisbane")
    yesterday = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    run_file = None
    for day in (today, yesterday):
        candidate = OVERNIGHT_RUNS_DIR / f"{day}.jsonl"
        if candidate.is_file():
            run_file = candidate
            break
    if run_file and run_file.is_file():
        try:
            for line in run_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    run_steps.append({
                        "step_id": entry.get("step_id"),
                        "label": entry.get("label"),
                        "type": entry.get("type"),
                        "ok": entry.get("ok"),
                        "duration_s": entry.get("duration_s"),
                        "error": entry.get("error"),
                        "advance_reason": entry.get("advance_reason") or (entry.get("result") or {}).get("advance_reason"),
                        "timed_out": (entry.get("result") or {}).get("timed_out", False),
                        "at": entry.get("at"),
                    })
        except Exception:
            pass
    if run_steps:
        first = run_steps[0].get("at") or ""
        last = run_steps[-1].get("at") or ""
        all_ok = all(s.get("ok") for s in run_steps)
        run_meta = {
            "status": "done" if all_ok else "failed",
            "started_at": first,
            "finished_at": last,
            "steps_total": len(run_steps),
            "steps_ok": sum(1 for s in run_steps if s.get("ok")),
            "steps_failed": sum(1 for s in run_steps if not s.get("ok")),
            "date": run_file.stem if run_file else yesterday,
        }

    # 2. Prior day job summary (reuse existing logic)
    start, end, label = _prior_local_day_bounds()
    matched = [j for j in _load_all_jobs() if _job_on_prior_day(j, start, end)]
    matched.sort(
        key=lambda j: j.get("finished_at") or j.get("started_at") or j.get("created_at") or "",
        reverse=True,
    )
    job_counts = dict(Counter(j.get("status", "?") for j in matched))
    job_slim = [
        {
            "id": j.get("id"),
            "title": j.get("title") or j.get("kind"),
            "kind": j.get("kind"),
            "status": j.get("status"),
            "error": _human_error(j.get("error")),
            "finished_at": j.get("finished_at"),
        }
        for j in matched[:10]
    ]

    # 3. Machine events from run steps
    machine_events = [
        s for s in run_steps
        if s.get("type") in ("machine_wake", "machine_sleep")
    ]

    # 4. Dev-ops warnings from worker heartbeat
    warnings: list[str] = []
    try:
        if WORKER_STATUS_FILE.exists():
            ws = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
            stale = ws.get("stale_mounts") or []
            if stale:
                warnings.append(f"Stale mounts detected: {', '.join(stale)}")
            if not ws.get("vpn_ok"):
                warnings.append("VPN was down during last heartbeat")
            tail = ws.get("dev_ops_tail") or []
            warn_lines = [ln for ln in tail if "[WARN]" in ln or "[ERROR]" in ln]
            warnings.extend(warn_lines[-5:])
    except Exception:
        pass

    return {
        "run": run_meta,
        "steps": run_steps,
        "jobs": {"counts": job_counts, "items": job_slim, "date": label},
        "machine_events": machine_events,
        "warnings": warnings,
    }


@app.get("/{slug}")
def submitter_page(slug: str, key: str | None = None) -> RedirectResponse:
    if slug in ("api", "docs", "favicon.ico", "storage", "capture", "chat", "overnight"):
        raise HTTPException(404, "Not found")
    if _resolve_submitter_slug(slug) is None:
        raise HTTPException(404, "Not found")
    target = "/?panel=requests"
    if key:
        target += f"&key={quote(key)}"
    return RedirectResponse(url=target, status_code=302)
