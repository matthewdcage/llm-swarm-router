#!/usr/bin/env python3
"""Bucket-HUB Ubuntu worker: claim jobs; pull via aria2/yt-dlp/git/HF through Gluetun."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid as _uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from download_destinations import (
    ARIA2_TORRENTS_CONTAINER_DIR,
    aria2_dir_for,
    validate_aria2_destination,
)
from hub_torrent_parse import (
    classify_release_kind,
    season_numbers_from_title,
    guess_show_title,
    parse_season_bucket,
    release_kind_rank,
    normalize_tv_search_query,
    target_seasons_from_query,
    torrent_pack_search_queries,
    torrent_row_matches_target_season,
)
from job_health import evaluate_job_health, update_progress_watch
from media_pipeline import (
    radarr_add_payload_from_lookup,
    sonarr_add_payload_from_lookup,
)
from torrent_indexer_catalog import (
    load_catalog,
    normalize_hub_category,
    resolve_indexer_ids_for_search,
)

DEFAULT_HUB = "http://192.168.50.138:3020,http://100.85.42.0:3020"
HUB_URLS = [
    u.strip().rstrip("/")
    for u in os.environ.get("HUB_URL", DEFAULT_HUB).split(",")
    if u.strip()
]
WORKER_TOKEN = os.environ.get("HUB_WORKER_TOKEN", "")
HUB_TORRENT_INDEXERS_CATALOG = os.environ.get(
    "HUB_TORRENT_INDEXERS_CATALOG",
    str(Path.home() / "homelab/hub-form/torrent-indexers-catalog.json"),
)
ARIA2_RPC = os.environ.get("ARIA2_RPC", "http://127.0.0.1:6800/jsonrpc")
ARIA2_SECRET = os.environ.get("ARIA2_RPC_SECRET", "homelab-aria2")
MOVIES_USB_ROOT = "/mnt/external/movies"
MOVIES_USB_TORRENTS = f"{MOVIES_USB_ROOT}/torrents"
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
METUBE_URL = os.environ.get("METUBE_URL", "http://127.0.0.1:8089").rstrip("/")
POLL_S = float(os.environ.get("WORKER_POLL_S", "8"))
SEARCH_POLL_DURING_DOWNLOAD_S = float(
    os.environ.get("HUB_SEARCH_POLL_DURING_DOWNLOAD_S", "4")
)
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


HUB_ARIA2_MAX_ACTIVE = max(1, int(os.environ.get("HUB_ARIA2_MAX_ACTIVE", "16")))
# Aggregate download cap (decimal Mbps). 0 = only use HUB_ARIA2_MAX_ACTIVE (no bandwidth gate).
HUB_ARIA2_TARGET_MBPS = _env_float("HUB_ARIA2_TARGET_MBPS", 600.0)
HUB_ARIA2_TARGET_HEADROOM = min(
    1.0, max(0.5, _env_float("HUB_ARIA2_TARGET_HEADROOM", 0.90))
)
HUB_ARIA2_BOOTSTRAP_SLOTS = max(
    1,
    min(
        HUB_ARIA2_MAX_ACTIVE,
        int(os.environ.get("HUB_ARIA2_BOOTSTRAP_SLOTS", "4")),
    ),
)
HUB_SLOT_VIDEO = max(0, int(os.environ.get("HUB_SLOT_VIDEO", "1")))
HUB_SLOT_ARCHIVE = max(0, int(os.environ.get("HUB_SLOT_ARCHIVE", "0")))
# Per-download BitTorrent options (leech-only; global aria2.conf should match).
ARIA2_LEECH_OPTS: dict[str, str] = {
    "seed-ratio": "0",
    "seed-time": "0",
    "bt-seed-unverified": "false",
}

ARIA2_TRACKS: dict[str, dict[str, Any]] = {}
_supervisor_last_search = 0.0
_supervisor_last_heartbeat = 0.0
_video_executor: ThreadPoolExecutor | None = None
_video_running: set[str] = set()
_search_service_lock = threading.Lock()
VPN_CHECK_URL = os.environ.get("VPN_CHECK_URL", "https://ifconfig.me/ip")
HOME_IP_PREFIXES = [
    p.strip()
    for p in os.environ.get("HOME_IP_PREFIXES", "").split(",")
    if p.strip()
]
MOUNT_MEDIA_SCRIPT = Path(
    os.environ.get("MOUNT_MEDIA_SCRIPT", "/home/bucket/homelab/scripts/mount-media-docker.sh")
)
MOUNT_TNAS_SCRIPT = Path(
    os.environ.get("MOUNT_TNAS_SCRIPT", "/home/bucket/homelab/scripts/mount-tnas-docker.sh")
)
MOUNT_EXTERNAL_SCRIPT = Path(
    os.environ.get(
        "MOUNT_EXTERNAL_SCRIPT",
        "/home/bucket/homelab/scripts/mount-external-docker.sh",
    )
)
MEDIA_DEV = os.environ.get("MEDIA_DEV", "/dev/sda2")
EXTERNAL_MOUNT_BASE = Path(os.environ.get("EXTERNAL_MOUNT", "/mnt/external"))
KNOWN_MOUNT_ROOTS = {"/mnt/media", "/mnt/tnas"}
STORAGE_VOLUME_ROOTS = {
    "media": Path("/mnt/media"),
    "tnas": Path("/mnt/tnas"),
    "usb": Path("/mnt/external"),
}
STORAGE_MAX_CHILDREN = int(os.environ.get("STORAGE_MAX_CHILDREN", "300"))
STORAGE_MAX_DEPTH = int(os.environ.get("STORAGE_MAX_DEPTH", "10"))
YTDLP_IMAGE = os.environ.get("YTDLP_IMAGE", "ghcr.io/yt-dlp/yt-dlp:latest")
# MeTube image ships Deno (required by modern yt-dlp YouTube extraction).
METUBE_IMAGE = os.environ.get("METUBE_IMAGE", "ghcr.io/alexta69/metube:latest")
GIT_IMAGE = os.environ.get("GIT_IMAGE", "alpine/git:latest")
HF_IMAGE = os.environ.get("HF_IMAGE", "python:3.11-slim")
HTTRACK_IMAGE = os.environ.get("HTTRACK_IMAGE", "hubhttrack:latest")
BROWSERTRIX_IMAGE = os.environ.get("BROWSERTRIX_IMAGE", "webrecorder/browsertrix-crawler:latest")
FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "http://127.0.0.1:8191").rstrip("/")
# SearXNG shares gluetun network; published on host as :8888 → container :8080
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
JACKETT_URL = os.environ.get("JACKETT_URL", "http://127.0.0.1:9117").rstrip("/")
JACKETT_API_KEY = os.environ.get("JACKETT_API_KEY", "").strip()
YTDLP_COOKIES = Path(
    os.environ.get(
        "YTDLP_COOKIES",
        "/home/bucket/homelab/data/ytdlp/cookies.txt",
    )
)


DEV_OPS_LOG = Path(os.environ.get("DEV_OPS_LOG", "/home/bucket/homelab/logs/dev-ops.log"))
_STALE_MOUNTS: set[str] = set()


def _safe_check_path(path: str, check: str = "stat", timeout: int = 3) -> bool:
    """Check a filesystem path via subprocess to avoid D-state on stale mounts.

    When a USB drive disconnects but stays mounted, any in-process stat/is_mount
    call enters uninterruptible kernel D-state.  Running the check in a subprocess
    with a timeout keeps the worker alive: the subprocess is killed if it hangs.

    check: "stat" (path exists), "mount" (is a mountpoint), "ls" (can list dir)
    """
    if path in _STALE_MOUNTS:
        return False
    cmd_map = {
        "stat": ["stat", path],
        "mount": ["mountpoint", "-q", path],
        "ls": ["ls", path],
    }
    try:
        r = subprocess.run(
            cmd_map.get(check, ["stat", path]),
            capture_output=True, timeout=timeout,
        )
        if r.returncode != 0 and check == "mount":
            return False
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        _STALE_MOUNTS.add(path)
        _dev_ops_log("worker", "WARN", f"stale mount detected (timeout): {path}")
        return False
    except Exception:
        return False


def _safe_disk_usage(path: str, timeout: int = 3) -> int | None:
    """Get free bytes via subprocess to avoid D-state hangs."""
    if path in _STALE_MOUNTS:
        return None
    try:
        r = subprocess.run(
            ["stat", "-f", "-c", "%a %S", path],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            if len(parts) == 2:
                return int(parts[0]) * int(parts[1])
    except subprocess.TimeoutExpired:
        _STALE_MOUNTS.add(path)
        _dev_ops_log("worker", "WARN", f"stale mount (disk_usage timeout): {path}")
    except Exception:
        pass
    return None


def _safe_listdir(path: str, timeout: int = 5) -> list[str] | None:
    """List directory contents via subprocess to avoid D-state."""
    if path in _STALE_MOUNTS:
        return None
    try:
        r = subprocess.run(
            ["ls", "-1", path],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            return [x for x in r.stdout.strip().split("\n") if x]
        return None
    except subprocess.TimeoutExpired:
        _STALE_MOUNTS.add(path)
        _dev_ops_log("worker", "WARN", f"stale mount (listdir timeout): {path}")
        return None
    except Exception:
        return None


def _recover_stale_mounts() -> None:
    """Periodically attempt to clear stale mount set if mounts recover."""
    recovered = set()
    for mount in list(_STALE_MOUNTS):
        try:
            r = subprocess.run(
                ["mountpoint", "-q", mount],
                capture_output=True, timeout=2,
            )
            if r.returncode != 0:
                recovered.add(mount)
                _dev_ops_log("worker", "INFO", f"stale mount cleared (unmounted): {mount}")
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            recovered.add(mount)
    _STALE_MOUNTS.difference_update(recovered)


def _dev_ops_log(source: str, level: str, message: str) -> None:
    """Append a structured line to the dev ops log."""
    try:
        DEV_OPS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        line = f"[{ts}] [{source}] [{level}] {message}\n"
        with DEV_OPS_LOG.open("a") as f:
            f.write(line)
    except Exception:
        pass


def hub_urls() -> list[str]:
    return HUB_URLS or [DEFAULT_HUB.split(",")[0]]


def _http_json(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Accept": "application/json", "User-Agent": "bucket-hub-worker/2.0"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if WORKER_TOKEN:
        hdrs["X-Worker-Token"] = WORKER_TOKEN
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def hub_call(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    last: Exception | None = None
    for base in hub_urls():
        try:
            return _http_json(f"{base}{path}", method=method, body=body)
        except Exception as exc:  # noqa: BLE001
            last = exc
    if last:
        raise last
    raise RuntimeError("no hub urls configured")


def hub_get_job(job_id: str) -> dict[str, Any]:
    data = hub_call(f"/api/jobs/{job_id}")
    if isinstance(data, dict) and data.get("id"):
        return data
    if isinstance(data, dict) and isinstance(data.get("job"), dict):
        return data["job"]
    return {}


def should_exit_wait_for_job(status: str | None, error: str | None) -> str | None:
    """Return 'paused', 'cancelled', or None to keep waiting."""
    st = (status or "").strip().lower()
    if st == "purging":
        return "cancelled"
    if st == "paused":
        return "paused"
    if st == "error":
        err = (error or "").lower()
        if "cancelled by user" in err:
            return "cancelled"
    return None


def _aria2_pause_gid(gid: str) -> None:
    try:
        aria2("aria2.pause", [gid])
    except RuntimeError:
        aria2("aria2.forcePause", [gid])


def _aria2_unpause_gid(gid: str) -> None:
    try:
        aria2("aria2.unpause", [gid])
    except RuntimeError:
        aria2("aria2.forceUnpause", [gid])


def _aria2_rpc_transient(exc: BaseException) -> bool:
    low = str(exc).lower()
    return "timed out" in low or "timeout" in low


def aria2(method: str, params: list[Any] | None = None) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": "hub-worker",
        "method": method,
        "params": [f"token:{ARIA2_SECRET}"] + (params or []),
    }
    req = urllib.request.Request(
        ARIA2_RPC,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            raise RuntimeError(str(err.get("message") or err))
        raise RuntimeError(str(err))
    return data.get("result")


def vpn_ok() -> tuple[bool, str]:
    try:
        out = subprocess.check_output(
            ["docker", "exec", "gluetun", "wget", "-qO-", "--timeout=8", VPN_CHECK_URL],
            text=True,
            timeout=20,
        ).strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"vpn check failed: {exc}"
    if not out:
        return False, "empty vpn ip"
    for prefix in HOME_IP_PREFIXES:
        if out.startswith(prefix):
            return False, f"egress looks like home ISP ({out})"
    return True, out


def ensure_mounts() -> None:
    if not _safe_check_path("/mnt/media", "mount") and MOUNT_MEDIA_SCRIPT.is_file():
        subprocess.run([str(MOUNT_MEDIA_SCRIPT)], check=False, timeout=120)
    if not _safe_check_path("/mnt/tnas", "mount") and MOUNT_TNAS_SCRIPT.is_file():
        subprocess.run([str(MOUNT_TNAS_SCRIPT)], check=False, timeout=120)
    if MOUNT_EXTERNAL_SCRIPT.is_file():
        subprocess.run([str(MOUNT_EXTERNAL_SCRIPT)], check=False, timeout=120)
    _recover_stale_mounts()


def path_ready(dest: str) -> tuple[bool, str]:
    if dest.startswith("/mnt/media"):
        if not _safe_check_path("/mnt/media", "mount"):
            return False, "Media HDD is not mounted"
        return True, "media"
    if dest.startswith("/mnt/tnas"):
        if not _safe_check_path("/mnt/tnas", "mount"):
            return False, "NAS is not mounted"
        return True, "nas"
    if dest.startswith("/mnt/external/") or dest.startswith("/media/"):
        p = Path(dest)
        parts = p.parts
        if dest.startswith("/mnt/external/"):
            # parts: ('/', 'mnt', 'external', '<drive>', ...)
            if len(parts) < 4:
                return False, f"Invalid external path: {dest}"
            mount_root = str(Path("/mnt/external") / parts[3])
        elif len(parts) >= 3:
            mount_root = str(Path(parts[0]) / parts[1] / parts[2])
        else:
            return False, f"Invalid external path: {dest}"
        if not _safe_check_path(mount_root, "mount"):
            return False, f"External drive not mounted: {mount_root}"
        return True, "external"
    if not _safe_check_path(dest, "stat"):
        return False, f"Destination missing: {dest}"
    return True, "other"


def ensure_host_dir(host_path: str) -> None:
    Path(host_path).mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["cp", "-a", str(src) + "/.", str(dst)])


def write_tags_sidecar(dest: str, job: dict[str, Any]) -> None:
    """Legacy sidecar at destination root (non-aria2 jobs)."""
    tags = job.get("tags") or []
    if not tags:
        return
    side = Path(dest) / f".hub-tags-{job['id']}.json"
    try:
        side.write_text(
            json.dumps(
                {"job_id": job["id"], "tags": tags, "title": job.get("title"), "url": job.get("url")},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def write_hub_job_sidecar(download_dir: Path, job: dict[str, Any]) -> None:
    """Torrent completion metadata beside the payload (for Jellyfin tag sync)."""
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job["id"],
            "title": job.get("title"),
            "url": job.get("url"),
            "tags": job.get("tags") or [],
            "torrent_meta": job.get("torrent_meta") or {},
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        (download_dir / ".hub-job.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"hub-job sidecar failed: {exc}", flush=True)


def _jellyfin_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    url = f"{JELLYFIN_URL}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "X-MediaBrowser-Token": JELLYFIN_API_KEY,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def _hub_tags_for_jellyfin(job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for t in job.get("tags") or []:
        s = str(t).strip().lower()
        if s and s not in out:
            out.append(s)
    meta = job.get("torrent_meta") or {}
    if meta.get("category"):
        c = str(meta["category"]).strip().lower().replace("/", "-")
        if c and c not in out:
            out.append(c)
    if meta.get("eng_subs"):
        if "eng-subs" not in out:
            out.append("eng-subs")
    return out


def jellyfin_apply_hub_tags(job: dict[str, Any], host_dir: Path) -> None:
    """Merge Hub torrent tags onto Jellyfin items (by title/path after library scan)."""
    tags = _hub_tags_for_jellyfin(job)
    if not tags or not JELLYFIN_API_KEY:
        return
    folder_key = host_dir.name.lower()
    title = str(job.get("title") or "")
    search = re.sub(r"[^\w\s]", " ", title).strip()
    if len(search) < 3:
        search = folder_key[:40]
    time.sleep(20)
    try:
        data = _jellyfin_request(
            "GET",
            "/Items?"
            + urlencode(
                {
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Episode",
                    "Fields": "Path,Tags",
                    "SearchTerm": search[:80],
                    "Limit": "50",
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"jellyfin item search failed: {exc}", flush=True)
        return
    items = (data or {}).get("Items") or []
    updated = 0
    for item in items:
        path = (item.get("Path") or "").lower()
        if folder_key not in path and folder_key[:20] not in path:
            continue
        item_id = item.get("Id")
        if not item_id:
            continue
        existing = [str(t).lower() for t in (item.get("Tags") or [])]
        merged = list(existing)
        for t in tags:
            if t not in merged:
                merged.append(t)
        if merged == existing:
            continue
        try:
            _jellyfin_request(
                "POST",
                f"/Items/{item_id}",
                {"Tags": merged},
            )
            updated += 1
        except Exception as exc:  # noqa: BLE001
            print(f"jellyfin tag update {item_id}: {exc}", flush=True)
    if updated:
        print(f"jellyfin tags applied to {updated} item(s)", flush=True)


def update_job(job_id: str, retries: int = 3, **fields: Any) -> None:
    clearable = {"error", "agent_review"}
    body = {
        k: v
        for k, v in fields.items()
        if v is not None or k in clearable
    }
    if "error" in fields and fields["error"] is None:
        body["error"] = None
    if "agent_review" in fields and fields["agent_review"] is None:
        body["agent_review"] = None
    last: Exception | None = None
    for attempt in range(retries):
        try:
            hub_call(f"/api/jobs/{job_id}", method="PATCH", body=body)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2**attempt, 8))
    if last:
        raise last


def _maybe_sync_agent_review(
    job_id: str,
    hub_job: dict[str, Any],
    progress: dict[str, Any],
) -> None:
    """Mirror Pi job_health rules when progress PATCH succeeds."""
    try:
        snap = dict(hub_job)
        snap["progress"] = progress
        snap["progress_watch"] = update_progress_watch(snap, progress)
        review = evaluate_job_health(snap)
        prev_code = (hub_job.get("agent_review") or {}).get("code")
        new_code = (review or {}).get("code")
        if review and new_code != prev_code:
            update_job(job_id, agent_review=review)
        elif not review and hub_job.get("agent_review"):
            update_job(job_id, agent_review=None)
    except Exception as exc:  # noqa: BLE001
        print(f"agent_review sync {job_id}: {exc}", flush=True)


def _drive_slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return s[:32] or "drive"


def _scan_drive_root(
    root: Path, extra_subdirs: list[str] | None = None
) -> tuple[list[dict[str, str]], bool]:
    """Return (folders, reliable). reliable=False means I/O failed — Hub must keep old folders."""
    folders: list[dict[str, str]] = [{"rel": ".", "name": "(entire drive)"}]
    if not root.is_dir():
        return folders, True

    def add_dir(base: Path, name: str, rel_prefix: str = "") -> None:
        if name.startswith("."):
            return
        full = base / name
        try:
            if not full.is_dir():
                return
        except OSError:
            return
        rel = f"{rel_prefix}/{name}".lstrip("/") if rel_prefix else name
        folders.append({"rel": rel, "name": name})

    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        print(f"drive scan incomplete for {root}: {exc}", flush=True)
        return folders, False
    for name in names:
        add_dir(root, name)
    for sub_prefix in extra_subdirs or []:
        sub_root = root / sub_prefix
        try:
            if not sub_root.is_dir():
                continue
            sub_names = sorted(os.listdir(sub_root))
        except OSError:
            continue
        for name in sub_names:
            add_dir(sub_root, name, rel_prefix=sub_prefix)
    return folders, True


def remount_media_if_stale() -> None:
    """Remount Media HDD when the mount is up but directory reads fail (exFAT I/O)."""
    media = Path("/mnt/media")
    root = Path("/mnt/media/Media")
    if not Path(MEDIA_DEV).exists():
        print(f"skip media remount: {MEDIA_DEV} not present", flush=True)
        return
    if not media.is_mount():
        return
    try:
        os.listdir(root if root.is_dir() else media)
        return
    except OSError:
        pass
    print("media mount looks stale (I/O error); remounting…", flush=True)
    cmd = (
        f"nsenter -t 1 -m -u -i -n -- umount -l {media} || true; "
        f"nsenter -t 1 -m -u -i -n -- mkdir -p {media}; "
        f"nsenter -t 1 -m -u -i -n -- mount -t exfat -o uid=1000,gid=1000,umask=022 {MEDIA_DEV} {media}"
    )
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--privileged", "--pid=host", "busybox:latest", "sh", "-c", cmd],
            check=False,
            timeout=90,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"media remount failed: {proc.stderr or proc.stdout}", flush=True)
        elif not media.is_mount():
            print("media remount finished but mount is still down", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"media remount failed: {exc}", flush=True)


def _discover_catalog_drives() -> list[dict[str, Any]]:
    catalogs = [
        {
            "id": "media_hdd",
            "label": "Media HDD",
            "mount": "/mnt/media",
            "root": "/mnt/media/Media",
            "removable": False,
        },
        {
            "id": "nas",
            "label": "TNAS",
            "mount": "/mnt/tnas",
            "root": "/mnt/tnas",
            "extra_subdirs": ["AI Models", "Website Backups", "backups"],
            "removable": False,
        },
    ]
    out: list[dict[str, Any]] = []
    for cat in catalogs:
        try:
            mount = cat["mount"]
            root = Path(cat["root"])
            mounted = _safe_check_path(mount, "mount")
            if mounted:
                mounted = _safe_check_path(str(root), "stat")
            free_bytes: int | None = None
            if mounted:
                free_bytes = _safe_disk_usage(mount)
            folders: list[dict[str, str]] = []
            folders_reliable = True
            if mounted:
                folders, folders_reliable = _scan_drive_root(root, cat.get("extra_subdirs"))
            out.append(
                {
                    "id": cat["id"],
                    "label": cat["label"],
                    "root": str(root),
                    "mounted": bool(Path(mount).is_mount()),
                    "removable": cat.get("removable", False),
                    "free_bytes": free_bytes,
                    "folders": folders,
                    "folders_reliable": folders_reliable,
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(f"catalog discover failed for {cat.get('id')}: {exc}", flush=True)
    return out


def _discover_external_drives() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_roots: set[str] = set()

    # Partitions to never surface (boot, EFI, swap, LVM internals).
    _SKIP_FSTYPES = {"vfat", "swap", "LVM2_member"}
    _SKIP_LABELS = {"EFI", "efi", "SYSTEM", "Recovery", "BOOT"}

    def add_root(mountpoint: str, label: str, uuid: str) -> None:
        if mountpoint in seen_roots or mountpoint in KNOWN_MOUNT_ROOTS:
            return
        if not mountpoint.startswith(("/mnt/external/", "/media/")):
            return
        # Skip boot/EFI partitions that sneak through.
        if label in _SKIP_LABELS:
            return
        seen_roots.add(mountpoint)
        root = Path(mountpoint)
        mounted = _safe_check_path(mountpoint, "mount")
        if mounted:
            entries = _safe_listdir(mountpoint)
            if entries is None:
                print(f"stale mount detected (timeout/error on readdir): {mountpoint}", flush=True)
                mounted = False
        free_bytes: int | None = None
        if mounted:
            free_bytes = _safe_disk_usage(str(root))
        # Derive drive_id from the mountpoint leaf so lsblk and directory
        # walk produce the same ID for the same physical drive.
        mount_leaf = Path(mountpoint).name.lower().replace("-", "").replace(" ", "")
        drive_id = f"ext_{_drive_slug(mount_leaf[:16])}"
        folders: list[dict[str, str]] = []
        folders_reliable = True
        if mounted:
            try:
                folders, folders_reliable = _scan_drive_root(root)
            except OSError as exc:
                print(f"external scan failed for {mountpoint}: {exc}", flush=True)
                folders_reliable = False
        out.append(
            {
                "id": drive_id,
                "label": label or Path(mountpoint).name,
                "root": mountpoint,
                "mounted": mounted,
                "removable": True,
                "free_bytes": free_bytes,
                "folders": folders,
                "folders_reliable": folders_reliable,
            }
        )

    try:
        raw = subprocess.check_output(
            [
                "lsblk",
                "-J",
                "-o",
                "NAME,SIZE,TYPE,MOUNTPOINT,LABEL,RM,UUID,FSTYPE,TRAN",
            ],
            text=True,
            timeout=15,
        )
        data = json.loads(raw)
    except Exception:
        data = {"blockdevices": []}

    def walk(devs: list[dict[str, Any]]) -> Any:
        for dev in devs:
            if dev.get("type") == "part":
                yield dev
            yield from walk(dev.get("children") or [])

    for part in walk(data.get("blockdevices") or []):
        mountpoint = part.get("mountpoint")
        if not mountpoint:
            continue
        dev = f"/dev/{part.get('name', '')}"
        if dev == MEDIA_DEV:
            continue
        # Skip boot/EFI/swap by filesystem type.
        fstype = (part.get("fstype") or "").strip()
        if fstype in _SKIP_FSTYPES:
            continue
        rm = bool(part.get("rm"))
        tran = (part.get("tran") or "").lower()
        if not rm and tran != "usb" and not mountpoint.startswith("/mnt/external/"):
            continue
        label = part.get("label") or f"External ({part.get('size', '')})"
        uuid = part.get("uuid") or part.get("name", "")
        try:
            add_root(mountpoint, label, uuid)
        except OSError as exc:
            print(f"skip external {mountpoint}: {exc}", flush=True)

    try:
        if EXTERNAL_MOUNT_BASE.is_dir():
            for entry in sorted(EXTERNAL_MOUNT_BASE.iterdir()):
                try:
                    if entry.is_dir() and entry.is_mount():
                        add_root(str(entry), entry.name, entry.name)
                except OSError as exc:
                    print(f"skip stale external entry {entry}: {exc}", flush=True)
    except OSError as exc:
        print(f"external base list failed: {exc}", flush=True)

    return out


def discover_drives() -> list[dict[str, Any]]:
    """Scan catalog + hot-plugged external drives for Hub merge."""
    drives: list[dict[str, Any]] = []
    try:
        drives.extend(_discover_catalog_drives())
    except Exception as exc:  # noqa: BLE001
        print(f"catalog drive discovery failed: {exc}", flush=True)
    try:
        drives.extend(_discover_external_drives())
    except Exception as exc:  # noqa: BLE001
        print(f"external drive discovery failed: {exc}", flush=True)
    return drives


def consume_scan_request() -> bool:
    """Return True if Hub asked for an on-demand drive scan."""
    try:
        result = hub_call("/api/library/scan/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"scan pending check failed: {exc}", flush=True)
        return False
    return bool(result.get("pending"))


def acknowledge_scan(ok: bool, detail: str = "", drives: list[dict[str, Any]] | None = None) -> None:
    try:
        hub_call(
            "/api/library/scan/complete",
            method="POST",
            body={
                "ok": ok,
                "detail": detail,
                "drive_ids": [d.get("id") for d in (drives or []) if d.get("id")],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"scan complete ack failed: {exc}", flush=True)


def gluetun_http_json(url: str, timeout: int = 25) -> Any:
    """GET JSON via Gluetun container network (Proton egress)."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "container:gluetun",
        "curlimages/curl:8.11.1",
        "-fsSL",
        "--max-time",
        str(timeout),
        "-H",
        "User-Agent: Bucket-HUB-worker/2.1",
        "-H",
        "Accept: application/json",
    ]
    gh = os.environ.get("HUB_GITHUB_TOKEN", "").strip()
    if gh and "api.github.com" in url:
        cmd.extend(["-H", f"Authorization: Bearer {gh}"])
    hf = os.environ.get("HF_TOKEN", "").strip() or os.environ.get(
        "HUGGING_FACE_HUB_TOKEN", ""
    ).strip()
    if hf and "huggingface.co" in url:
        cmd.extend(["-H", f"Authorization: Bearer {hf}"])
    cmd.append(url)
    out = subprocess.check_output(cmd, text=True, timeout=timeout + 10)
    return json.loads(out) if out else {}


def jackett_http_json(path_and_query: str, timeout: int = 40) -> Any:
    """GET JSON from Jackett on the Gluetun netns (localhost to Jackett).

    Recent Jackett builds require a TestCookie handshake before API key auth
    works from non-browser clients; we mint that cookie via /UI/Login first.
    """
    if not JACKETT_API_KEY:
        raise RuntimeError("JACKETT_API_KEY not set")
    base = JACKETT_URL.rstrip("/")
    # Ensure apikey present
    sep = "&" if "?" in path_and_query else "?"
    if "apikey=" not in path_and_query.lower():
        path_and_query = f"{path_and_query}{sep}apikey={quote(JACKETT_API_KEY)}"
    url = (
        path_and_query
        if path_and_query.startswith("http")
        else f"{base}{path_and_query}"
    )
    cookie_dir = Path("/tmp/hub-jackett-cookies")
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_dir / "cookies.txt"
    # Handshake: follow redirects so Jackett sets TestCookie + session cookie.
    subprocess.check_call(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--network",
            "container:gluetun",
            "-v",
            f"{cookie_dir}:/cookies",
            "curlimages/curl:8.5.0",
            "-sS",
            "-L",
            "--max-redirs",
            "5",
            "-c",
            "/cookies/cookies.txt",
            "-b",
            "/cookies/cookies.txt",
            "-o",
            "/dev/null",
            f"{base}/UI/Login",
        ],
        timeout=25,
    )
    out = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--network",
            "container:gluetun",
            "-v",
            f"{cookie_dir}:/cookies",
            "curlimages/curl:8.5.0",
            # No -L on API calls (would follow to Login HTML).
            "-fsS",
            "--max-time",
            str(timeout),
            "-b",
            "/cookies/cookies.txt",
            "-c",
            "/cookies/cookies.txt",
            "-H",
            "Accept: application/json",
            "-H",
            "User-Agent: Bucket-HUB-worker/2.1",
            url,
        ],
        text=True,
        timeout=timeout + 15,
    )
    return json.loads(out) if out else {}


def consume_search_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/search/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"search pending check failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    req = result.get("request") or {}
    return req if isinstance(req, dict) else None


def try_run_pending_search(vpn: tuple[bool, str], *, context: str = "idle") -> bool:
    """Run one Hub search if queued. Safe from main loop and during aria2 wait."""
    if not _search_service_lock.acquire(blocking=False):
        return False
    try:
        req = consume_search_request()
        if not req:
            return False
        print(
            f"private search via Proton ({context}): {req.get('query')!r}",
            flush=True,
        )
        run_private_search(req, vpn)
        return True
    finally:
        _search_service_lock.release()


def consume_research_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/research/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"research pending check failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    req = result.get("request") or {}
    return req if isinstance(req, dict) else None


def run_private_research(req: dict[str, Any], vpn: tuple[bool, str]) -> None:
    """Preset feeds (HN / GitHub / HF) via Gluetun for Hub Research panel."""
    request_id = str(req.get("request_id") or "")
    preset = str(req.get("preset") or "")
    preset_def = req.get("preset_def") if isinstance(req.get("preset_def"), dict) else {}
    sources = preset_def.get("sources") if isinstance(preset_def.get("sources"), list) else []
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()

    def finish(*, ok: bool, detail: str = "", egress: str = "proton") -> None:
        hub_call(
            "/api/research/complete",
            method="POST",
            body={
                "ok": ok,
                "detail": detail,
                "request_id": request_id,
                "preset": preset,
                "results": results,
                "errors": errors,
                "egress": egress,
                "vpn_detail": vpn[1],
            },
        )

    if not vpn[0]:
        finish(ok=False, detail=f"VPN required for research: {vpn[1]}", egress="blocked")
        return

    for src in sources:
        if not isinstance(src, dict):
            continue
        stype = str(src.get("type") or "").strip().lower()
        try:
            if stype == "hn":
                q = str(src.get("query") or "").strip()
                tags = str(src.get("tags") or "story").strip() or "story"
                hits = int(src.get("hits") or 12)
                if tags == "front_page":
                    url = (
                        "https://hn.algolia.com/api/v1/search"
                        f"?tags=front_page&hitsPerPage={min(hits, 20)}"
                    )
                else:
                    url = (
                        "https://hn.algolia.com/api/v1/search"
                        f"?query={quote(q)}&tags={quote(tags)}"
                        f"&hitsPerPage={min(hits, 20)}"
                    )
                data = gluetun_http_json(url, timeout=30)
                for hit in (data.get("hits") or [])[:hits]:
                    if not isinstance(hit, dict):
                        continue
                    link = str(hit.get("url") or "").strip()
                    title = str(hit.get("title") or "").strip()
                    if not title:
                        continue
                    if not link:
                        oid = hit.get("objectID")
                        link = f"https://news.ycombinator.com/item?id={oid}" if oid else ""
                    if not link or link in seen:
                        continue
                    seen.add(link)
                    pts = hit.get("points")
                    results.append(
                        {
                            "source": "hn",
                            "provider": "algolia",
                            "id": str(hit.get("objectID") or link),
                            "title": title[:140],
                            "description": (
                                f"{pts} pts · {hit.get('author') or ''}".strip(" ·")
                                if pts is not None
                                else str(hit.get("author") or "")[:120]
                            ),
                            "url": link,
                            "kind_hint": "website",
                            "points": pts,
                        }
                    )
            elif stype == "github":
                q = str(src.get("query") or "").strip()
                if not q:
                    continue
                per_page = int(src.get("per_page") or 8)
                data = gluetun_http_json(
                    f"https://api.github.com/search/repositories?q={quote(q)}"
                    f"&sort=updated&order=desc&per_page={min(per_page, 10)}",
                    timeout=30,
                )
                for item in (data.get("items") or [])[:per_page]:
                    if not isinstance(item, dict):
                        continue
                    link = str(item.get("html_url") or "").strip()
                    name = str(item.get("full_name") or "").strip()
                    if not link or not name or link in seen:
                        continue
                    seen.add(link)
                    results.append(
                        {
                            "source": "github",
                            "provider": "github",
                            "id": name,
                            "title": name,
                            "description": (item.get("description") or "")[:160],
                            "url": link + ".git",
                            "clone_url": item.get("clone_url"),
                            "stars": item.get("stargazers_count", 0),
                            "kind_hint": "github",
                        }
                    )
            elif stype == "hf":
                q = str(src.get("query") or "").strip()
                if not q:
                    continue
                limit = int(src.get("limit") or 8)
                data = gluetun_http_json(
                    f"https://huggingface.co/api/models?search={quote(q)}&limit={min(limit, 12)}",
                    timeout=30,
                )
                if isinstance(data, list):
                    for item in data[:limit]:
                        if not isinstance(item, dict):
                            continue
                        mid = item.get("id") or item.get("modelId")
                        if not mid:
                            continue
                        link = f"https://huggingface.co/{mid}"
                        if link in seen:
                            continue
                        seen.add(link)
                        results.append(
                            {
                                "source": "hf",
                                "provider": "hf",
                                "id": mid,
                                "title": mid,
                                "description": "HF model",
                                "url": link,
                                "repo_id": mid,
                                "kind_hint": "hf",
                            }
                        )
            else:
                errors.append(f"unknown source type: {stype}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{stype or 'source'}: {exc}")

    finish(ok=True, detail="")


def run_private_search(req: dict[str, Any], vpn: tuple[bool, str]) -> None:
    request_id = str(req.get("request_id") or "")
    query = str(req.get("query") or "").strip()
    source = str(req.get("source") or "both")
    torrent_cats: list[int] = req.get("torrent_cats") or []
    torrent_pack_search = bool(req.get("torrent_pack_search"))
    torrent_tv_search = bool(req.get("torrent_tv_search"))
    try:
        torrent_result_limit = int(req.get("torrent_result_limit") or 0)
    except (TypeError, ValueError):
        torrent_result_limit = 0
    torrent_hub_category = normalize_hub_category(req.get("torrent_hub_category"))
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    if not vpn[0]:
        hub_call(
            "/api/search/complete",
            method="POST",
            body={
                "ok": False,
                "detail": f"VPN required for private search: {vpn[1]}",
                "request_id": request_id,
                "source": source,
                "results": [],
                "errors": [f"vpn: {vpn[1]}"],
                "egress": "blocked",
                "vpn_detail": vpn[1],
            },
        )
        return
    if not query:
        hub_call(
            "/api/search/complete",
            method="POST",
            body={
                "ok": False,
                "detail": "empty query",
                "request_id": request_id,
                "source": source,
                "egress": "proton",
                "vpn_detail": vpn[1],
            },
        )
        return

    def add_github(term_query: str) -> None:
        try:
            m = re.search(r"github\.com/([^/\s]+)/([^/\s?#]+)", term_query)
            term = (
                f"{m.group(1)}/{m.group(2).removesuffix('.git')}" if m else term_query
            )
            data = gluetun_http_json(
                f"https://api.github.com/search/repositories?q={quote(term)}&per_page=8"
            )
            for item in data.get("items", [])[:8]:
                results.append(
                    {
                        "source": "github",
                        "id": item["full_name"],
                        "title": item["full_name"],
                        "description": (item.get("description") or "")[:160],
                        "url": item["html_url"] + ".git",
                        "clone_url": item.get("clone_url"),
                        "stars": item.get("stargazers_count", 0),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"github: {exc}")

    def add_hf(term_query: str) -> None:
        try:
            m = re.search(r"huggingface\.co/([^/\s]+)/([^/\s?#]+)", term_query)
            term = (
                f"{m.group(1)}/{m.group(2)}"
                if m
                else term_query.removeprefix("hf:").strip()
            )
            for kind, endpoint in (
                ("model", "https://huggingface.co/api/models"),
                ("dataset", "https://huggingface.co/api/datasets"),
            ):
                data = gluetun_http_json(f"{endpoint}?search={quote(term)}&limit=6")
                if not isinstance(data, list):
                    continue
                for item in data[:6]:
                    mid = item.get("id") or item.get("modelId")
                    if not mid:
                        continue
                    results.append(
                        {
                            "source": "hf",
                            "kind": kind,
                            "id": mid,
                            "title": mid,
                            "description": f"HF {kind}",
                            "url": f"https://huggingface.co/{mid}",
                            "repo_id": mid,
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"hf: {exc}")

    def add_searxng(term_query: str, *, source_label: str, categories: str) -> None:
        """Query local SearXNG (already Proton-egress via Gluetun network)."""
        try:
            url = (
                f"{SEARXNG_URL}/search?q={quote(term_query)}"
                f"&format=json&categories={quote(categories)}"
            )
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Bucket-HUB-worker/2.1",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=28) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            items = data.get("results") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise RuntimeError("unexpected SearXNG response")
            for item in items[:10]:
                if not isinstance(item, dict):
                    continue
                link = str(item.get("url") or "").strip()
                title = str(item.get("title") or link or "").strip()
                if not link or not title:
                    continue
                kind_hint = "media" if source_label == "youtube" else "website"
                low = link.lower()
                if "youtube.com" in low or "youtu.be" in low:
                    kind_hint = "media"
                    source_label_out = "youtube"
                else:
                    source_label_out = source_label
                results.append(
                    {
                        "source": source_label_out,
                        "provider": "searxng",
                        "id": link,
                        "title": title[:120],
                        "description": str(item.get("content") or "")[:180],
                        "url": link,
                        "kind_hint": kind_hint,
                        "engine": item.get("engine") or "",
                        "score": item.get("score"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_label}/searxng: {exc}")

    def _jellyfin_score(title: str) -> int:
        """Rate a torrent title for Jellyfin media-server compatibility.

        Higher = better playback experience.  Scoring:
          Container:  MKV +3
          Codec:      x264 +4 (universal HW transcode), x265/HEVC +3, AV1 +1
          Source:     Remux +4, BluRay/BD +3, WEB-DL +2, WEBRip +1
          Audio:      Dual/Multi Audio +3, DTS/Atmos/DD+ +2, FLAC +1
          Subs:       Eng Subs +2, any subs +1
          Quality:    10bit +1, HDR +1, 1080p +1, 2160p/4K +2
          Penalties:  CAM/TS -5, HDTV -1, x264+Remux combo (bloated) -1
        """
        t = title.upper()
        s = 0
        # Container
        if ".MKV" in t or "[MKV]" in t or " MKV " in t:
            s += 3
        # Codec
        has_x264 = any(k in t for k in ("X264", "H.264", "H264", "AVC"))
        has_x265 = any(k in t for k in ("X265", "H.265", "H265", "HEVC"))
        has_av1 = "AV1" in t
        if has_x264:
            s += 4
        elif has_x265:
            s += 3
        elif has_av1:
            s += 1
        # Source
        is_remux = "REMUX" in t
        is_bluray = any(k in t for k in ("BLURAY", "BLU-RAY")) or re.search(r"\bBD\b", t)
        if is_remux:
            s += 4
        elif is_bluray:
            s += 3
        elif "WEB-DL" in t:
            s += 2
        elif "WEBRIP" in t:
            s += 1
        # Audio
        if re.search(r"DUAL.?AUDIO", t):
            s += 3
        elif re.search(r"MULTI.?AUDIO", t) or re.search(r"\bMULTI\b", t):
            s += 3
        if any(k in t for k in ("DTS-HD", "DTS", "ATMOS")):
            s += 2
        elif any(k in t for k in ("DD+", "DDP5", "DD5", "EAC3", "AC3")):
            s += 2
        elif "FLAC" in t:
            s += 1
        # Subs
        if re.search(r"ENG(LISH)?\s*SUB", t):
            s += 2
        elif re.search(r"\bSUB(BED|S|TITLE)?\b", t):
            s += 1
        # Quality / resolution
        if re.search(r"\b10.?BIT\b", t):
            s += 1
        if "HDR" in t:
            s += 1
        if re.search(r"\b2160P\b", t) or "4K" in t:
            s += 2
        elif re.search(r"\b1080P\b", t):
            s += 1
        # Penalties
        if any(k in t for k in ("CAM", "CAMRIP", "TELESYNC", " TS ", "HDTS")):
            s -= 5
        if "HDTV" in t:
            s -= 1
        if has_x264 and is_remux:
            s -= 1  # bloated combo
        return max(s, 0)

    def add_jackett(
        term_query: str,
        cats: list[int] | None = None,
        *,
        pack_search: bool = False,
        tv_search: bool = False,
        result_limit: int = 15,
        hub_category: str = "all",
    ) -> None:
        """Query local Jackett (WAN via Gluetun). Returns magnet/torrent links."""
        if not JACKETT_API_KEY:
            errors.append("torrent/jackett: JACKETT_API_KEY not set")
            return
        hub_cat = normalize_hub_category(hub_category)
        configured_ids = _jackett_indexer_ids_on_disk()
        catalog = load_catalog(Path(HUB_TORRENT_INDEXERS_CATALOG))
        indexer_ids = resolve_indexer_ids_for_search(
            hub_cat, configured_ids, catalog
        )
        if hub_cat != "all" and not indexer_ids:
            errors.append(
                f"torrent/jackett: no configured indexers for {hub_cat} chip "
                f"(add matching indexers in Jackett on ai-server)"
            )
            return
        if tv_search:
            term_query = normalize_tv_search_query(term_query)
        limit = max(5, min(result_limit or 15, 40))
        query_passes: list[tuple[str, bool]] = []
        if tv_search:
            pack_qs = torrent_pack_search_queries(term_query)
            for pq in pack_qs:
                query_passes.append((pq, True))
            base = re.sub(
                r"\b(complete|season|pack|series|full)\b",
                " ",
                term_query,
                flags=re.I,
            )
            base = re.sub(r"\s+", " ", base).strip() or term_query
            query_passes.append((base, False))
        elif pack_search:
            for pq in torrent_pack_search_queries(term_query):
                query_passes.append((pq, True))
        else:
            query_passes.append((term_query, False))
        try:
            cat_params = "".join(f"&Category[]={c}" for c in (cats or []))
            scored: list[tuple[int, int, int, dict[str, Any]]] = []
            seen_fetch: set[str] = set()
            any_items = False

            def ingest_items(items: list[Any], *, skip_episodes: bool) -> None:
                nonlocal any_items
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    magnet = str(item.get("MagnetUri") or "").strip()
                    link = str(item.get("Link") or "").strip()
                    fetch_url = magnet if magnet.lower().startswith("magnet:") else ""
                    if not fetch_url and _is_jackett_torrent_url(link):
                        fetch_url = link
                    title = str(item.get("Title") or "").strip()
                    if not title or not fetch_url:
                        continue
                    if fetch_url in seen_fetch:
                        continue
                    kind = classify_release_kind(title)
                    if skip_episodes and kind == "episode":
                        continue
                    seen_fetch.add(fetch_url)
                    any_items = True
                    seeders = item.get("Seeders")
                    try:
                        seeder_n = int(seeders) if seeders is not None else -1
                    except (TypeError, ValueError):
                        seeder_n = -1
                    size = item.get("Size")
                    peers = item.get("Peers")
                    tracker = str(item.get("Tracker") or item.get("TrackerId") or "")
                    cat_desc = str(item.get("CategoryDesc") or "")
                    pub_date = str(item.get("PublishDate") or "")[:10]
                    desc_bits = []
                    if seeder_n >= 0:
                        desc_bits.append(f"seeders {seeder_n}")
                    if size:
                        try:
                            mb = int(size) / (1024 * 1024)
                            desc_bits.append(
                                f"{mb:.0f} MB" if mb < 1024 else f"{mb / 1024:.1f} GB"
                            )
                        except (TypeError, ValueError):
                            pass
                    if tracker:
                        desc_bits.append(tracker)
                    jf = _jellyfin_score(title)
                    t_upper = title.upper()
                    has_eng_subs = bool(
                        re.search(r"ENG(LISH)?\s*SUB", t_upper)
                        or re.search(r"\bMULTI.?SUB", t_upper)
                    )
                    has_any_subs = has_eng_subs or bool(
                        re.search(r"\bSUB(BED|S|TITLE)?\b", t_upper)
                    )
                    rk = release_kind_rank(kind)
                    scored.append(
                        (
                            rk,
                            jf,
                            seeder_n,
                            {
                                "source": "torrent",
                                "provider": "jackett",
                                "id": fetch_url,
                                "title": title[:140],
                                "description": " · ".join(desc_bits)[:180],
                                "url": fetch_url,
                                "magnet": magnet if magnet.lower().startswith("magnet:") else "",
                                "kind_hint": "torrent",
                                "seeders": seeders,
                                "size": size,
                                "peers": peers,
                                "tracker": tracker,
                                "category": cat_desc,
                                "date": pub_date,
                                "jf_score": jf,
                                "eng_subs": has_eng_subs,
                                "has_subs": has_any_subs,
                                "release_kind": kind,
                                "show_title_guess": guess_show_title(title),
                                "season_bucket": parse_season_bucket(title),
                                "season_numbers": season_numbers_from_title(title),
                            },
                        )
                    )

            def jackett_result_paths(tq: str) -> list[str]:
                q = f"Query={quote(tq)}{cat_params}"
                if hub_cat == "all":
                    return [f"/api/v2.0/indexers/all/results?{q}"]
                return [
                    f"/api/v2.0/indexers/{iid}/results?{q}" for iid in indexer_ids
                ]

            def fetch_jackett_items(path: str) -> list[Any]:
                data = jackett_http_json(path, timeout=55)
                items = data.get("Results") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    raise RuntimeError("unexpected Jackett response")
                return items

            for tq, skip_eps in query_passes:
                paths = jackett_result_paths(tq)
                if len(paths) == 1:
                    items = fetch_jackett_items(paths[0])
                    if items:
                        ingest_items(items, skip_episodes=skip_eps)
                else:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        futures = [pool.submit(fetch_jackett_items, p) for p in paths]
                        for fut in as_completed(futures):
                            try:
                                batch = fut.result()
                            except Exception:
                                continue
                            if batch:
                                ingest_items(batch, skip_episodes=skip_eps)

            if not any_items:
                if pack_search or tv_search:
                    errors.append(
                        "torrent/jackett: no season/series packs found — try "
                        "Season packs filter or queue seasons one at a time"
                    )
                elif cats:
                    errors.append(
                        "torrent/jackett: no results for the selected category "
                        f"({len(cats)} cats). Try All, or TV for series."
                    )
                else:
                    errors.append(
                        "torrent/jackett: no results (add indexers in Jackett UI "
                        "http://192.168.50.65:9117 )"
                    )
                return
            if not scored:
                errors.append(
                    "torrent/jackett: results had no magnets "
                    "(enable TPB/1337x-style indexers in Jackett UI)"
                )
                return
            scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
            if tv_search:
                want_seasons = target_seasons_from_query(term_query)
                packs = [t for t in scored if t[0] >= 2]
                episodes = [t for t in scored if t[0] == 1]
                if len(want_seasons) == 1:
                    want = want_seasons[0]
                    matched = [
                        t for t in packs if torrent_row_matches_target_season(t[3], want)
                    ]
                    if matched:
                        packs = matched
                    else:
                        packs = [
                            t
                            for t in packs
                            if str(t[3].get("release_kind") or "") != "full_show"
                        ]
                packs.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
                episodes.sort(key=lambda t: (t[1], t[2]), reverse=True)
                pack_cap = min(len(packs), max(20, limit - 12))
                ep_cap = min(len(episodes), limit - pack_cap)
                picked = packs[:pack_cap] + episodes[:ep_cap]
            else:
                picked = scored[:limit]
            for _, _, _, row in picked:
                results.append(row)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"torrent/jackett: {exc}")

    # source: auto|both|github|hf|web|youtube|torrent|all  (+ comma lists)
    sources = {s.strip().lower() for s in str(source).split(",") if s.strip()}
    if not sources or sources == {"auto"}:
        sources = {"github", "hf"}
    if "both" in sources:
        sources.discard("both")
        sources.update({"github", "hf"})
    if "all" in sources:
        # Keep torrent opt-in via its own chip (noisier / indexer-dependent).
        sources = {"github", "hf", "web", "youtube"}

    if "github" in sources:
        add_github(query)
    if "hf" in sources:
        add_hf(query)
    if "web" in sources:
        add_searxng(query, source_label="web", categories="general")
    if "youtube" in sources:
        add_searxng(query, source_label="youtube", categories="videos")
    if "torrent" in sources:
        jlimit = torrent_result_limit if torrent_result_limit > 0 else (
            40 if torrent_tv_search else (25 if torrent_pack_search else 15)
        )
        add_jackett(
            query,
            cats=torrent_cats or None,
            pack_search=torrent_pack_search and not torrent_tv_search,
            tv_search=torrent_tv_search,
            result_limit=jlimit,
            hub_category=torrent_hub_category,
        )

    series_info = sonarr_series_lookup(query) if torrent_tv_search else None
    target_seasons = (
        target_seasons_from_query(query) if torrent_tv_search else []
    )
    hub_call(
        "/api/search/complete",
        method="POST",
        body={
            "ok": True,
            "detail": "",
            "request_id": request_id,
            "source": source,
            "results": results,
            "errors": errors,
            "egress": "proton",
            "vpn_detail": vpn[1],
            "series_info": series_info,
            "target_seasons": target_seasons,
        },
    )


def consume_hf_files_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/hf/files/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"hf-files pending check failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    req = result.get("request") or {}
    return req if isinstance(req, dict) else None


def run_private_hf_files(req: dict[str, Any], vpn: tuple[bool, str]) -> None:
    request_id = str(req.get("request_id") or "")
    repo_id = str(req.get("repo_id") or "").strip()
    revision = str(req.get("revision") or "main")
    if not vpn[0]:
        hub_call(
            "/api/hf/files/complete",
            method="POST",
            body={
                "ok": False,
                "detail": f"VPN required for HF listing: {vpn[1]}",
                "request_id": request_id,
                "repo_id": repo_id,
                "revision": revision,
                "egress": "blocked",
                "vpn_detail": vpn[1],
            },
        )
        return
    files: list[dict[str, Any]] = []
    folders: set[str] = set()
    try:
        url = (
            f"https://huggingface.co/api/models/{quote(repo_id, safe='/')}"
            f"/tree/{quote(revision)}?recursive=1"
        )
        try:
            data = gluetun_http_json(url, timeout=40)
        except Exception:
            url = (
                f"https://huggingface.co/api/datasets/{quote(repo_id, safe='/')}"
                f"/tree/{quote(revision)}?recursive=1"
            )
            data = gluetun_http_json(url, timeout=40)
        if isinstance(data, list):
            for item in data:
                typ = item.get("type")
                path = item.get("path") or ""
                if not path:
                    continue
                if typ == "directory":
                    folders.add(path)
                else:
                    files.append(
                        {
                            "path": path,
                            "size": item.get("size"),
                            "oid": item.get("oid"),
                        }
                    )
                    parent = "/".join(path.split("/")[:-1])
                    if parent:
                        folders.add(parent)
    except Exception as exc:  # noqa: BLE001
        hub_call(
            "/api/hf/files/complete",
            method="POST",
            body={
                "ok": False,
                "detail": str(exc),
                "request_id": request_id,
                "repo_id": repo_id,
                "revision": revision,
                "egress": "proton",
                "vpn_detail": vpn[1],
            },
        )
        return
    hub_call(
        "/api/hf/files/complete",
        method="POST",
        body={
            "ok": True,
            "detail": "",
            "request_id": request_id,
            "repo_id": repo_id,
            "revision": revision,
            "files": files[:2000],
            "folders": sorted(folders)[:500],
            "whole_repo_url": f"https://huggingface.co/{repo_id}",
            "egress": "proton",
            "vpn_detail": vpn[1],
        },
    )


def youtube_cookies_ok() -> bool:
    try:
        return YTDLP_COOKIES.is_file() and YTDLP_COOKIES.stat().st_size > 100
    except OSError:
        return False


def is_youtube_url(url: str) -> bool:
    low = (url or "").lower()
    return "youtube.com" in low or "youtu.be" in low


def send_heartbeat(vpn: tuple[bool, str], *, force_remount: bool = False) -> list[dict[str, Any]]:
    ok, detail = vpn
    if force_remount:
        remount_media_if_stale()
    try:
        drives = discover_drives()
    except Exception as exc:  # noqa: BLE001
        print(f"drive discovery failed: {exc}", flush=True)
        drives = []
    dev_ops_tail: list[str] = []
    try:
        if DEV_OPS_LOG.is_file():
            with DEV_OPS_LOG.open() as f:
                all_lines = f.readlines()
                dev_ops_tail = [ln.rstrip() for ln in all_lines[-50:]]
    except Exception:
        pass

    body = {
        "worker": "ubuntu-worker",
        "vpn_ok": ok,
        "vpn_detail": detail,
        "media_mounted": _safe_check_path("/mnt/media", "mount"),
        "tnas_mounted": _safe_check_path("/mnt/tnas", "mount"),
        "youtube_cookies": youtube_cookies_ok(),
        "drives": drives,
        "dev_ops_tail": dev_ops_tail,
        "stale_mounts": sorted(_STALE_MOUNTS),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "aria2_active_slots": len(ARIA2_TRACKS),
        "aria2_max_active": HUB_ARIA2_MAX_ACTIVE,
        "aria2_target_mbps": HUB_ARIA2_TARGET_MBPS,
        "aria2_total_download_bps": _aria2_total_download_bps(),
    }
    try:
        hub_call("/api/worker/heartbeat", method="POST", body=body)
    except Exception as exc:  # noqa: BLE001
        print(f"heartbeat failed: {exc}", flush=True)
    return drives


def claim_next() -> dict[str, Any] | None:
    return hub_call("/api/jobs/claim", method="POST", body={}).get("job")


def _recover_claimed_aria2_jobs(vpn: tuple[bool, str]) -> None:
    """Re-dispatch Hub jobs stuck in claimed (worker died after claim, before aria2)."""
    try:
        data = hub_call("/api/jobs?status=claimed&limit=40")
    except Exception as exc:  # noqa: BLE001
        print(f"recover claimed list: {exc}", flush=True)
        return
    for job in data.get("jobs") or []:
        kind = (job.get("kind") or "http").lower()
        if kind not in ("http", "magnet"):
            continue
        jid = str(job.get("id") or "")
        if not jid or jid in ARIA2_TRACKS:
            continue
        print(f"recover claimed aria2 job {jid}", flush=True)
        _dispatch_claimed_job(job, vpn)


def find_resumable_aria2_job() -> dict[str, Any] | None:
    """Hub job left running with an aria2 gid (e.g. after worker restart)."""
    try:
        data = hub_call("/api/jobs?status=running&limit=40")
    except Exception:
        return None
    for job in data.get("jobs") or []:
        if job.get("worker") != "aria2":
            continue
        gid = job.get("aria2_gid")
        if not gid:
            continue
        try:
            st = aria2(
                "aria2.tellStatus",
                [str(gid), ["status"]],
            )
        except RuntimeError:
            continue
        if st.get("status") in ("active", "waiting", "paused"):
            return job
    return None


def resume_aria2_job(job: dict[str, Any]) -> None:
    gid = str(job.get("aria2_gid") or "")
    if not gid:
        raise RuntimeError("missing aria2_gid")
    print(f"resume aria2 {job['id']} gid={gid}", flush=True)
    dest = str(job.get("destination_path") or "")
    aria_dir, _post = aria2_dir_for(dest) if dest else ("", None)
    opts = {
        "dir": aria_dir,
        "file-allocation": "none",
        "bt-metadata-only": "false",
        **ARIA2_LEECH_OPTS,
    }
    wait_aria2(
        gid,
        str(job["id"]),
        magnet_url=str(job.get("url") or ""),
        aria_opts=opts,
        dest_path=dest,
    )
    if hub_get_job(str(job["id"])).get("status") == "paused":
        return
    if dest:
        write_tags_sidecar(dest, job)


def docker_gluetun(args: list[str], timeout: int = 3600) -> None:
    subprocess.check_call(
        ["docker", "run", "--rm", "--network", "container:gluetun", *args],
        timeout=timeout,
    )


def _aria2_metadata_only(st: dict[str, Any]) -> bool:
    files = st.get("files") or []
    if not files:
        return False
    return all("[METADATA]" in (f.get("path") or "") for f in files)


def _aria2_status_fully_complete(st: dict[str, Any]) -> bool:
    if str(st.get("status") or "") != "complete":
        return False
    if _aria2_metadata_only(st):
        return False
    total = int(st.get("totalLength") or 0)
    completed = int(st.get("completedLength") or 0)
    if total < 5_000_000:
        return False
    if total > 0 and completed < total - 4096:
        return False
    return True


def _finalize_after_aria2(job_id: str) -> None:
    """Mark done only when aria2 reports a real data complete; keep paused/partial honest."""
    job = hub_get_job(job_id)
    if job.get("status") == "paused":
        return
    gid = str(job.get("aria2_gid") or "")
    if not gid:
        update_job(job_id, status="done", error=None)
        return
    try:
        st = aria2(
            "aria2.tellStatus",
            [gid, ["status", "completedLength", "totalLength", "files"]],
        )
    except RuntimeError:
        update_job(job_id, status="running", error=None)
        return
    a2_status = str(st.get("status") or "")
    if a2_status == "paused":
        update_job(job_id, status="paused", error=None)
        return
    if a2_status in ("active", "waiting"):
        update_job(job_id, status="running", error=None)
        return
    if _aria2_status_fully_complete(st):
        update_job(job_id, status="done", error=None)
        return
    if a2_status == "complete":
        update_job(
            job_id,
            status="running",
            error="aria2 reported complete but transfer size is not finished",
        )
        return
    update_job(job_id, status="running", error=None)


def _dir_has_substantial_media(path: Path, min_bytes: int = 200_000_000) -> bool:
    """True when a folder already holds real video (not just .aria2 / metainfo)."""
    if not path.is_dir():
        return False
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            low = name.lower()
            if not low.endswith((".mkv", ".mp4", ".avi", ".m4v", ".wmv")):
                continue
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
            if total >= min_bytes:
                return True
    return False


def _info_hash_from_magnet(url: str) -> str | None:
    m = re.search(r"btih:([0-9a-fA-F]{40})", url, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"btih:([0-9a-zA-Z]{32})", url, re.I)
    if m:
        raw = base64.b32decode(m.group(1).upper())
        return raw.hex()
    return None


def _mbps_to_bps(mbps: float) -> int:
    if mbps <= 0:
        return 0
    return int(mbps * 1_000_000 / 8)


def aria2_dispatch_allowed(
    active_tracks: int,
    total_download_bps: int,
    *,
    max_active: int | None = None,
    target_mbps: float | None = None,
    bootstrap_slots: int | None = None,
    headroom: float | None = None,
) -> bool:
    """Whether the supervisor may start another aria2 torrent."""
    cap_slots = max_active if max_active is not None else HUB_ARIA2_MAX_ACTIVE
    tgt_mbps = target_mbps if target_mbps is not None else HUB_ARIA2_TARGET_MBPS
    boot = bootstrap_slots if bootstrap_slots is not None else HUB_ARIA2_BOOTSTRAP_SLOTS
    hr = headroom if headroom is not None else HUB_ARIA2_TARGET_HEADROOM
    if active_tracks >= cap_slots:
        return False
    if tgt_mbps <= 0:
        return True
    if active_tracks < boot:
        return True
    line_cap = int(_mbps_to_bps(tgt_mbps) * hr)
    return total_download_bps < line_cap


def _aria2_total_download_bps() -> int:
    total = 0
    for row in _aria2_download_rows("aria2.tellActive"):
        total += int(row.get("downloadSpeed") or 0)
    return total


def _aria2_can_dispatch_more() -> bool:
    return aria2_dispatch_allowed(len(ARIA2_TRACKS), _aria2_total_download_bps())


def _aria2_download_rows(method: str) -> list[dict[str, Any]]:
    try:
        if method == "aria2.tellActive":
            rows = aria2(method) or []
        else:
            rows = aria2(method, [0, 200]) or []
    except RuntimeError:
        return []
    return [r for r in rows if isinstance(r, dict)]


def _aria2_find_gid_by_info_hash(
    info_hash: str,
    *,
    min_total_bytes: int = 0,
) -> str | None:
    want = info_hash.lower()
    best_gid: str | None = None
    best_total = -1
    for method in ("aria2.tellActive", "aria2.tellWaiting"):
        for row in _aria2_download_rows(method):
            if (row.get("infoHash") or "").lower() != want:
                continue
            status = str(row.get("status") or "")
            if status not in ("active", "waiting", "paused"):
                continue
            total = int(row.get("totalLength") or 0)
            if total < min_total_bytes:
                continue
            if total > best_total:
                best_total = total
                best_gid = str(row.get("gid") or "")
    return best_gid


def _aria2_purge_stopped_results() -> None:
    try:
        aria2("aria2.purgeDownloadResult", [])
    except RuntimeError:
        pass


def _torrents_delete_root() -> Path:
    return Path(MOVIES_USB_TORRENTS).resolve()


def _path_under_torrents(path: Path) -> bool:
    base = _torrents_delete_root()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved == base:
        return False
    return base in resolved.parents


def _safe_rmtree_torrents(path: Path) -> bool:
    if not path.exists():
        return False
    if not _path_under_torrents(path):
        print(f"purge skip (outside torrents): {path}", flush=True)
        return False
    shutil.rmtree(path)
    return True


def _aria2_detach_completed_gid(gid: str) -> None:
    """Stop seeding/uploading; keep completed files on disk (aria2.remove)."""
    if not gid:
        return
    try:
        aria2("aria2.remove", [gid])
    except RuntimeError:
        pass


def _aria2_force_remove_gid(gid: str) -> None:
    if not gid:
        return
    try:
        aria2("aria2.forceRemove", [gid])
    except RuntimeError:
        try:
            aria2("aria2.remove", [gid])
        except RuntimeError:
            pass


def _collect_aria2_torrent_paths(gid: str) -> set[Path]:
    paths: set[Path] = set()
    host_dir = _aria2_host_download_dir(gid)
    if host_dir:
        paths.add(host_dir if host_dir.is_dir() else host_dir.parent)
    try:
        st = aria2("aria2.tellStatus", [gid, ["files"]])
    except RuntimeError:
        return paths
    prefix = ARIA2_TORRENTS_CONTAINER_DIR.rstrip("/")
    base = Path(MOVIES_USB_TORRENTS)
    for row in st.get("files") or []:
        raw = (row.get("path") or "").strip()
        if not raw.startswith(prefix):
            continue
        rel = raw[len(prefix) :].lstrip("/")
        if not rel:
            continue
        host = base / rel
        paths.add(host.parent if host.name else host)
    return paths


def purge_aria2_download(job: dict[str, Any]) -> list[str]:
    """Remove aria2 gids and delete partial data under Expansion HDD torrents/."""
    removed: list[str] = []
    gids: set[str] = set()
    primary = str(job.get("aria2_gid") or "")
    if primary:
        gids.add(primary)
    url = str(job.get("url") or "")
    ih = _info_hash_from_magnet(url) if url.startswith("magnet:") else None
    if ih:
        ih = ih.lower()
        for method in ("aria2.tellActive", "aria2.tellWaiting", "aria2.tellStopped"):
            for row in _aria2_download_rows(method):
                if str(row.get("infoHash") or "").lower() == ih:
                    gids.add(str(row.get("gid") or ""))
    paths: set[Path] = set()
    for gid in sorted(g for g in gids if g):
        paths.update(_collect_aria2_torrent_paths(gid))
        _aria2_force_remove_gid(gid)
    for path in sorted(paths, key=lambda p: len(p.parts), reverse=True):
        if _safe_rmtree_torrents(path):
            removed.append(str(path))
    _aria2_purge_stopped_results()
    return removed


def consume_purge_request() -> dict[str, Any] | None:
    try:
        data = hub_call("/api/jobs/purge/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"purge pending check failed: {exc}", flush=True)
        return None
    job = (data or {}).get("job")
    return job if isinstance(job, dict) and job.get("id") else None


def run_purge_job(job: dict[str, Any]) -> None:
    jid = str(job.get("id") or "")
    print(f"purge aria2 job {jid}", flush=True)
    removed: list[str] = []
    detail = ""
    try:
        if _is_aria2_job_kind(job):
            removed = purge_aria2_download(job)
        else:
            detail = "not an aria2 job"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        print(f"purge {jid} error: {exc}", flush=True)
    hub_call(
        f"/api/jobs/{jid}/purge-complete",
        method="POST",
        body={"removed_paths": removed, "detail": detail},
    )


def _is_aria2_job_kind(job: dict[str, Any]) -> bool:
    if job.get("worker") == "aria2":
        return True
    kind = (job.get("kind") or "http").lower()
    return kind in ("http", "magnet")


JACKETT_HOST_INDEXERS = Path.home() / "homelab/data/jackett/Jackett/Indexers"


def _jackett_indexer_ids_on_disk() -> list[str]:
    root = JACKETT_HOST_INDEXERS
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json") if p.is_file())


def _jackett_read_indexer_config(indexer_id: str) -> dict[str, str]:
    path = JACKETT_HOST_INDEXERS / f"{indexer_id}.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").lower()
        val = row.get("value")
        if rid in ("sitelink", "lasterror") and val:
            out[rid] = str(val)
    return out


def _jackett_probe_indexer(indexer_id: str, query: str) -> dict[str, Any]:
    key = JACKETT_API_KEY
    if not key:
        return {"ok": False, "error": "JACKETT_API_KEY missing", "result_count": 0}
    path = (
        f"/api/v2.0/indexers/{indexer_id}/results?"
        f"Query={quote(query)}&apikey={quote(key)}"
    )
    probe_url = f"{JACKETT_URL.rstrip('/')}{path}"
    try:
        data = jackett_http_json(path, timeout=55)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc)[:240],
            "result_count": 0,
            "probe_url": probe_url,
        }
    items = data.get("Results") if isinstance(data, dict) else []
    count = len(items) if isinstance(items, list) else 0
    max_seed = 0
    if isinstance(items, list):
        for row in items[:30]:
            if not isinstance(row, dict):
                continue
            try:
                max_seed = max(max_seed, int(row.get("Seeders") or 0))
            except (TypeError, ValueError):
                pass
    return {
        "ok": count > 0,
        "result_count": count,
        "max_seeders": max_seed,
        "probe_url": probe_url,
        "error": None if count > 0 else "no results for probe query",
    }


def collect_jackett_indexer_snapshot(
    probe_query: str = "breaking bad season 1",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for iid in _jackett_indexer_ids_on_disk():
        cfg = _jackett_read_indexer_config(iid)
        probe = _jackett_probe_indexer(iid, probe_query)
        last_err = cfg.get("lasterror") or probe.get("error")
        if probe.get("ok"):
            status = "healthy"
        elif cfg.get("lasterror"):
            status = "error"
        elif probe.get("result_count", 0) == 0:
            status = "empty"
        else:
            status = "degraded"
        rows.append(
            {
                "jackett_id": iid,
                "configured": True,
                "site_url": cfg.get("sitelink") or "",
                "last_error": (last_err or "")[:300] or None,
                "probe_query": probe_query,
                "probe_url": probe.get("probe_url"),
                "result_count": probe.get("result_count", 0),
                "max_seeders": probe.get("max_seeders", 0),
                "status": status,
            }
        )
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probe_query": probe_query,
        "indexers": rows,
    }


def consume_indexers_scan_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/torrent/indexers/scan/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"indexers scan pending check failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    return result.get("request") or {}


def run_indexers_scan(req: dict[str, Any]) -> None:
    probe_query = str(req.get("probe_query") or "breaking bad season 1")
    print(f"indexers scan probe={probe_query!r}", flush=True)
    snapshot = collect_jackett_indexer_snapshot(probe_query)
    try:
        hub_call(
            "/api/torrent/indexers/scan/complete",
            method="POST",
            body=snapshot,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"indexers scan complete failed: {exc}", flush=True)


def _check_url_via_gluetun(url: str, timeout: int = 25) -> dict[str, Any]:
    """HEAD/GET mirror URL through Proton egress; advisory reachability only."""
    started = time.time()
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "container:gluetun",
                "curlimages/curl:latest",
                "-sS",
                "-o",
                "/dev/null",
                "-L",
                "--max-redirs",
                "5",
                "-A",
                "Mozilla/5.0 (compatible; Bucket-HUB/1.0)",
                "--max-time",
                str(timeout),
                "-w",
                "%{http_code}\n%{url_effective}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 20,
        )
        lines = (result.stdout or "").strip().splitlines()
        code_s = lines[0] if lines else "0"
        final = lines[1] if len(lines) > 1 else url
        try:
            code = int(code_s)
        except ValueError:
            code = 0
        ok = 200 <= code < 400
        latency_ms = int((time.time() - started) * 1000)
        err = (result.stderr or "").strip()[:240] if not ok and result.returncode else ""
        return {
            "url": url,
            "http_status": code,
            "final_url": final,
            "ok": ok,
            "latency_ms": latency_ms,
            "error": err or None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "url": url,
            "http_status": 0,
            "final_url": url,
            "ok": False,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc)[:240],
        }


def _normalize_site_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.endswith("/"):
        raw += "/"
    return raw


def consume_indexers_candidates_check_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/torrent/indexers/candidates/check/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"indexers candidates check pending failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    return result


def run_indexers_candidates_check(payload: dict[str, Any]) -> None:
    urls_by_id: dict[str, list[str]] = dict(payload.get("urls_to_check") or {})
    if not urls_by_id:
        by_id = payload.get("candidates") or {}
        for iid, block in by_id.items():
            if not isinstance(block, dict):
                continue
            rows = block.get("candidates") or []
            if not isinstance(rows, list):
                continue
            urls_by_id[str(iid)] = [
                str(r["url"]) for r in rows if isinstance(r, dict) and r.get("url")
            ]
    site_urls = payload.get("jackett_site_urls") or {}
    out_by_id: dict[str, Any] = {}
    for iid, url_list in urls_by_id.items():
        sitelink = str(site_urls.get(iid) or "")
        if not sitelink:
            cfg = _jackett_read_indexer_config(str(iid))
            sitelink = cfg.get("sitelink") or ""
        norm_site = _normalize_site_url(sitelink)
        checks: list[dict[str, Any]] = []
        for url in url_list:
            if not url:
                continue
            chk = _check_url_via_gluetun(str(url))
            final_norm = _normalize_site_url(chk.get("final_url") or url)
            chk["matches_jackett"] = bool(norm_site and final_norm == norm_site)
            checks.append(chk)
        if checks:
            out_by_id[str(iid)] = {"checks": checks}
    body = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by_jackett_id": out_by_id,
    }
    try:
        hub_call(
            "/api/torrent/indexers/candidates/check/complete",
            method="POST",
            body=body,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"indexers candidates check complete failed: {exc}", flush=True)


def _aria2_try_handoff_to_data_gid(
    meta_gid: str,
    magnet_url: str | None,
) -> str | None:
    """While metadata gid is active, attach to the real data download if aria2 created one."""
    child = _aria2_followed_by_gid(meta_gid)
    if child:
        return child
    ih = _info_hash_from_magnet(magnet_url or "") if magnet_url else None
    if ih:
        big_gid = _aria2_find_gid_by_info_hash(ih, min_total_bytes=50_000_000)
        if big_gid and big_gid != meta_gid:
            return big_gid
    return None


# aria2 only returns keys listed here; include downloadSpeed for Hub UI throughput.
ARIA2_PROGRESS_STATUS_KEYS = [
    "status",
    "errorMessage",
    "completedLength",
    "totalLength",
    "files",
    "numSeeders",
    "connections",
    "downloadSpeed",
    "uploadSpeed",
]


def _aria2_progress_payload(st: dict[str, Any], gid: str) -> dict[str, Any]:
    status = str(st.get("status") or "")
    meta = _aria2_metadata_only(st)
    total = int(st.get("totalLength") or 0)
    if meta:
        phase = "metadata"
    elif total > 0:
        phase = "downloading"
    elif status == "waiting":
        phase = "peers"
    else:
        phase = "starting"
    return {
        "gid": gid,
        "completed": st.get("completedLength"),
        "total": st.get("totalLength"),
        "aria2_status": status,
        "phase": phase,
        "metadata_only": meta,
        "seeders": st.get("numSeeders"),
        "connections": st.get("connections"),
        "speed": st.get("downloadSpeed"),
    }


def _aria2_followed_by_gid(meta_gid: str) -> str | None:
    try:
        st = aria2("aria2.tellStatus", [meta_gid, ["followedBy"]])
    except RuntimeError:
        return None
    followed = st.get("followedBy")
    if isinstance(followed, list) and followed:
        return str(followed[0])
    return None


def _aria2_host_download_dir(gid: str) -> Path | None:
    """Map aria2 completed files under /downloads to host …/torrents/…"""
    try:
        st = aria2("aria2.tellStatus", [gid, ["files"]])
    except RuntimeError:
        return None
    files = st.get("files") or []
    if not files:
        return None
    raw = (files[0].get("path") or "").strip()
    prefix = ARIA2_TORRENTS_CONTAINER_DIR.rstrip("/")
    if not raw.startswith(prefix):
        return None
    rel = raw[len(prefix) :].lstrip("/")
    base = Path(MOVIES_USB_TORRENTS)
    return base / rel if rel else base


def _http_post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> None:
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=hdrs,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _looks_like_tv(title: str) -> bool:
    t = title.lower()
    return bool(
        re.search(r"\bs\d{1,2}\b", t)
        or "season" in t
        or re.search(r"s\d{2}e\d{2}", t)
    )


def _looks_like_movie(title: str) -> bool:
    t = title.lower()
    return not _looks_like_tv(title) and bool(
        re.search(r"\b(19|20)\d{2}\b", t) or "bluray" in t or "remux" in t
    )


def _sonarr_lookup_pick(term: str) -> dict[str, Any] | None:
    q = (term or "").strip()
    if not q or not SONARR_API_KEY:
        return None
    data = _http_json(
        f"{SONARR_URL}/api/v3/series/lookup?term={quote(q)}",
        headers={"X-Api-Key": SONARR_API_KEY},
        timeout=20.0,
    )
    if not isinstance(data, list) or not data:
        return None
    pick = data[0]
    q_low = q.lower()
    for row in data:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        if title.lower() == q_low or q_low in title.lower():
            pick = row
            break
    return pick if isinstance(pick, dict) else None


def _sonarr_series_in_library(tvdb_id: Any) -> bool:
    if tvdb_id is None or not SONARR_API_KEY:
        return False
    try:
        lib = _http_json(
            f"{SONARR_URL}/api/v3/series",
            headers={"X-Api-Key": SONARR_API_KEY},
            timeout=20.0,
        )
        if isinstance(lib, list):
            return any(
                isinstance(s, dict) and s.get("tvdbId") == tvdb_id for s in lib
            )
    except Exception:  # noqa: BLE001
        pass
    return False


_sonarr_profile_cache: tuple[int, int] | None = None
_radarr_quality_cache: int | None = None


def _sonarr_profile_ids() -> tuple[int, int]:
    global _sonarr_profile_cache
    if _sonarr_profile_cache is not None:
        return _sonarr_profile_cache
    qp = _http_json(
        f"{SONARR_URL}/api/v3/qualityprofile",
        headers={"X-Api-Key": SONARR_API_KEY},
        timeout=15.0,
    )
    lp = _http_json(
        f"{SONARR_URL}/api/v3/languageprofile",
        headers={"X-Api-Key": SONARR_API_KEY},
        timeout=15.0,
    )
    qid = 1
    lid = 1
    if isinstance(qp, list) and qp:
        qid = int(qp[0].get("id") or 1)
    if isinstance(lp, list) and lp:
        lid = int(lp[0].get("id") or 1)
    _sonarr_profile_cache = (qid, lid)
    return _sonarr_profile_cache


def _radarr_quality_profile_id() -> int:
    global _radarr_quality_cache
    if _radarr_quality_cache is not None:
        return _radarr_quality_cache
    qp = _http_json(
        f"{RADARR_URL}/api/v3/qualityprofile",
        headers={"X-Api-Key": RADARR_API_KEY},
        timeout=15.0,
    )
    qid = 1
    if isinstance(qp, list) and qp:
        qid = int(qp[0].get("id") or 1)
    _radarr_quality_cache = qid
    return _radarr_quality_cache


def _job_torrent_meta(job: dict[str, Any]) -> dict[str, Any]:
    meta = job.get("torrent_meta")
    return meta if isinstance(meta, dict) else {}


def _job_is_tv(job: dict[str, Any]) -> bool:
    meta = _job_torrent_meta(job)
    rk = str(meta.get("release_kind") or "")
    if rk in ("episode", "season", "complete", "full_show"):
        return True
    tags = job.get("tags") or []
    if isinstance(tags, list) and any(str(t).lower() == "tv" for t in tags):
        return True
    return _looks_like_tv(str(job.get("title") or ""))


def _job_is_movie(job: dict[str, Any]) -> bool:
    meta = _job_torrent_meta(job)
    rk = str(meta.get("release_kind") or "")
    if rk == "movie":
        return True
    tags = job.get("tags") or []
    if isinstance(tags, list) and any(str(t).lower() == "movies" for t in tags):
        return True
    title = str(job.get("title") or "")
    return _looks_like_movie(title) and not _job_is_tv(job)


def _wanted_seasons_for_job(job: dict[str, Any]) -> set[int]:
    meta = _job_torrent_meta(job)
    raw = meta.get("season_numbers")
    if isinstance(raw, list) and raw:
        return {int(x) for x in raw if int(x) > 0}
    title = str(job.get("title") or "")
    return set(season_numbers_from_title(title))


def sonarr_series_lookup(term: str) -> dict[str, Any] | None:
    """TVDB/Sonarr metadata for Hub season checklist (lookup, not add to library)."""
    q = (term or "").strip()
    if not q or not SONARR_API_KEY:
        return None
    try:
        pick = _sonarr_lookup_pick(q)
        if not pick:
            return None
        seasons_raw = pick.get("seasons") if isinstance(pick.get("seasons"), list) else []
        season_numbers = sorted(
            {
                int(s.get("seasonNumber"))
                for s in seasons_raw
                if isinstance(s, dict) and int(s.get("seasonNumber") or 0) > 0
            }
        )
        season_count = int(pick.get("seasonCount") or 0)
        if not season_count and season_numbers:
            season_count = max(season_numbers)
        tvdb = pick.get("tvdbId")
        return {
            "title": str(pick.get("title") or q),
            "season_count": season_count,
            "season_numbers": season_numbers,
            "tvdb_id": tvdb,
            "in_library": _sonarr_series_in_library(tvdb),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"sonarr series lookup failed: {exc}", flush=True)
        return None


def ensure_sonarr_series_for_job(job: dict[str, Any]) -> str:
    """Add series to Sonarr when missing so DownloadedEpisodesScan can import."""
    if not SONARR_API_KEY or not _job_is_tv(job):
        return "skipped"
    meta = _job_torrent_meta(job)
    term = str(meta.get("show_title_guess") or "").strip()
    if not term:
        term = guess_show_title(str(job.get("title") or ""))
    if not term:
        return "skipped"
    try:
        pick = _sonarr_lookup_pick(term)
        if not pick:
            return "lookup_failed"
        tvdb = pick.get("tvdbId")
        if _sonarr_series_in_library(tvdb):
            return "exists"
        qp, lp = _sonarr_profile_ids()
        body = sonarr_add_payload_from_lookup(
            pick,
            root_folder="/data/tv",
            wanted_seasons=_wanted_seasons_for_job(job),
            quality_profile_id=qp,
            language_profile_id=lp,
            release_kind=str(meta.get("release_kind") or ""),
        )
        _http_post_json(
            f"{SONARR_URL}/api/v3/series",
            body,
            headers={"X-Api-Key": SONARR_API_KEY},
        )
        print(f"sonarr added series: {body.get('title')}", flush=True)
        return "added"
    except Exception as exc:  # noqa: BLE001
        print(f"sonarr ensure series failed: {exc}", flush=True)
        return "add_failed"


def _radarr_lookup_pick(term: str) -> dict[str, Any] | None:
    q = (term or "").strip()
    if not q or not RADARR_API_KEY:
        return None
    data = _http_json(
        f"{RADARR_URL}/api/v3/movie/lookup?term={quote(q)}",
        headers={"X-Api-Key": RADARR_API_KEY},
        timeout=20.0,
    )
    if not isinstance(data, list) or not data:
        return None
    pick = data[0]
    if isinstance(pick, dict):
        return pick
    return None


def _radarr_movie_in_library(tmdb_id: Any) -> bool:
    if tmdb_id is None or not RADARR_API_KEY:
        return False
    try:
        lib = _http_json(
            f"{RADARR_URL}/api/v3/movie",
            headers={"X-Api-Key": RADARR_API_KEY},
            timeout=20.0,
        )
        if isinstance(lib, list):
            return any(
                isinstance(m, dict) and m.get("tmdbId") == tmdb_id for m in lib
            )
    except Exception:  # noqa: BLE001
        pass
    return False


def ensure_radarr_movie_for_job(job: dict[str, Any]) -> str:
    if not RADARR_API_KEY or not _job_is_movie(job):
        return "skipped"
    title = str(job.get("title") or "")
    term = guess_show_title(title) or title
    try:
        pick = _radarr_lookup_pick(term)
        if not pick:
            return "lookup_failed"
        tmdb = pick.get("tmdbId")
        if _radarr_movie_in_library(tmdb):
            return "exists"
        qid = _radarr_quality_profile_id()
        body = radarr_add_payload_from_lookup(
            pick,
            root_folder="/data/movies",
            quality_profile_id=qid,
        )
        _http_post_json(
            f"{RADARR_URL}/api/v3/movie",
            body,
            headers={"X-Api-Key": RADARR_API_KEY},
        )
        print(f"radarr added movie: {body.get('title')}", flush=True)
        return "added"
    except Exception as exc:  # noqa: BLE001
        print(f"radarr ensure movie failed: {exc}", flush=True)
        return "add_failed"


def notify_media_libraries_after_torrent(job: dict[str, Any], gid: str) -> None:
    """Refresh Jellyfin; nudge Sonarr/Radarr to scan the completed download folder."""
    host_dir = _aria2_host_download_dir(gid)
    if host_dir is None:
        return
    if host_dir.is_file():
        host_dir = host_dir.parent
    if not host_dir.exists():
        return
    try:
        rel = host_dir.relative_to(Path(MOVIES_USB_TORRENTS))
        sonarr_path = f"/data/torrents/{rel}".rstrip("/")
    except ValueError:
        sonarr_path = "/data/torrents"

    pipeline: dict[str, Any] = {}
    jellyfin_refreshed = False

    if SONARR_API_KEY and _job_is_tv(job):
        pipeline["sonarr"] = ensure_sonarr_series_for_job(job)
        try:
            _http_post_json(
                f"{SONARR_URL}/api/v3/command",
                {"name": "DownloadedEpisodesScan", "path": sonarr_path},
                headers={"X-Api-Key": SONARR_API_KEY},
            )
            print(f"sonarr scan requested: {sonarr_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"sonarr scan failed: {exc}", flush=True)

    if RADARR_API_KEY and _job_is_movie(job):
        pipeline["radarr"] = ensure_radarr_movie_for_job(job)
        try:
            _http_post_json(
                f"{RADARR_URL}/api/v3/command",
                {"name": "DownloadedMoviesScan", "path": sonarr_path},
                headers={"X-Api-Key": RADARR_API_KEY},
            )
            print(f"radarr scan requested: {sonarr_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"radarr scan failed: {exc}", flush=True)

    if JELLYFIN_API_KEY:
        try:
            _http_post_json(
                f"{JELLYFIN_URL}/Library/Refresh",
                {},
                headers={"X-MediaBrowser-Token": JELLYFIN_API_KEY},
            )
            jellyfin_refreshed = True
            print("jellyfin library refresh requested", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"jellyfin refresh failed: {exc}", flush=True)
    pipeline["jellyfin_refreshed"] = jellyfin_refreshed

    job_id = str(job.get("id") or "")
    if job_id and pipeline:
        try:
            update_job(job_id, media_pipeline=pipeline)
        except Exception as exc:  # noqa: BLE001
            print(f"media_pipeline patch failed: {exc}", flush=True)

    jellyfin_apply_hub_tags(job, host_dir)


def _is_jackett_torrent_url(url: str) -> bool:
    u = url.lower()
    return ":9117/dl/" in u or ("jackett_apikey" in u and "/dl/" in u)


def _jackett_link_to_magnet(url: str) -> str | None:
    """Jackett /dl/ often 302s to magnet when no .torrent is stored."""
    try:
        proc = subprocess.run(
            ["wget", "-S", "-O", "/dev/null", url],
            capture_output=True,
            timeout=90,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (proc.stderr or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("location:"):
            loc = stripped.split(":", 1)[1].strip()
            if loc.startswith("magnet:"):
                return loc
    return None


def _fetch_torrent_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Bucket-Hub/1.0 (aria2 torrent fetch)"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) < 64:
        raise RuntimeError("torrent download returned empty or invalid data")
    return data


def _aria2_add_torrent_bytes(data: bytes, opts: dict[str, str]) -> str:
    b64 = base64.standard_b64encode(data).decode("ascii")
    gid = aria2("aria2.addTorrent", [b64, [], opts])
    if isinstance(gid, list):
        gid = gid[0]
    return str(gid)


def _aria2_add_uri_magnet(url: str, opts: dict[str, str]) -> str:
    ih = _info_hash_from_magnet(url)
    if ih:
        existing = _aria2_find_gid_by_info_hash(ih)
        if existing:
            return existing
    try:
        gid = aria2("aria2.addUri", [[url], opts])
    except RuntimeError as exc:
        msg = str(exc)
        if "already registered" not in msg.lower():
            raise
        ih = ""
        match = re.search(r"infohash\s+([0-9a-f]{40})", msg, re.I)
        if match:
            ih = match.group(1).lower()
        if not ih:
            ih = _info_hash_from_magnet(url) or ""
        if not ih:
            raise
        existing = None
        for _ in range(20):
            existing = _aria2_find_gid_by_info_hash(ih)
            if existing:
                break
            time.sleep(1)
        if not existing:
            raise RuntimeError(
                f"Magnet already in aria2 but could not resolve gid for {ih[:8]}… "
                "Open aria2 or retry in a minute."
            ) from exc
        gid = existing
    if isinstance(gid, list):
        gid = gid[0]
    return str(gid)


def _aria2_wait_step(track: dict[str, Any]) -> str:
    """One aria2 poll. Returns running, complete, paused, or cancelled."""
    job_id = str(track["job_id"])
    gid = str(track["gid"])
    magnet_url = track.get("magnet_url")
    aria_opts = track.get("aria_opts")
    dest_path = track.get("dest_path")
    meta_retries = int(track.get("meta_retries") or 0)
    try:
        hub_job = hub_get_job(job_id)
    except Exception as exc:  # noqa: BLE001
        print(f"hub poll {job_id}: {exc}", flush=True)
        hub_job = {}
    exit_reason = should_exit_wait_for_job(
        hub_job.get("status"), hub_job.get("error")
    )
    if exit_reason == "paused":
        _aria2_pause_gid(gid)
        return "paused"
    if exit_reason == "cancelled":
        hub_st = str(hub_job.get("status") or "").lower()
        if hub_st != "purging":
            try:
                aria2("aria2.remove", [gid])
            except RuntimeError:
                pass
        return "cancelled"

    st = aria2(
        "aria2.tellStatus",
        [gid, list(ARIA2_PROGRESS_STATUS_KEYS)],
    )
    status = st.get("status")
    if status in ("active", "waiting") and _aria2_metadata_only(st):
        handoff = _aria2_try_handoff_to_data_gid(gid, magnet_url)
        if handoff:
            print(
                f"aria2 handoff metadata→data {handoff} (from gid {gid})",
                flush=True,
            )
            gid = handoff
            track["gid"] = gid
            st = aria2(
                "aria2.tellStatus",
                [gid, list(ARIA2_PROGRESS_STATUS_KEYS)],
            )
            status = st.get("status")
    try:
        progress = _aria2_progress_payload(st, gid)
        update_job(
            job_id,
            status="running",
            error=None,
            progress=progress,
            aria2_gid=gid,
        )
        _maybe_sync_agent_review(job_id, hub_job, progress)
    except Exception as exc:  # noqa: BLE001
        print(f"hub progress {job_id}: {exc}", flush=True)
    if status == "complete":
        total = int(st.get("totalLength") or 0)
        metadata_only = _aria2_metadata_only(st)
        tiny_complete = total < 5_000_000
        if metadata_only or tiny_complete:
            switched_to_data = False
            for _ in range(30):
                handoff = _aria2_try_handoff_to_data_gid(gid, magnet_url)
                if handoff:
                    print(
                        f"aria2 follow-torrent data job {handoff} "
                        f"(after metadata gid {gid})",
                        flush=True,
                    )
                    gid = handoff
                    track["gid"] = gid
                    update_job(job_id, aria2_gid=gid, status="running")
                    switched_to_data = True
                    break
                time.sleep(2)
            if switched_to_data:
                track["meta_retries"] = meta_retries
                return "running"
            scan = Path(dest_path) if dest_path else _aria2_host_download_dir(gid)
            if scan and not scan.is_dir():
                scan = scan.parent
            if (
                magnet_url
                and aria_opts
                and meta_retries < 2
                and scan
                and scan.is_dir()
                and _dir_has_substantial_media(scan)
            ):
                meta_retries += 1
                print(
                    f"aria2 metadata/tiny complete but media exists under {scan}; "
                    f"re-adding magnet (attempt {meta_retries})",
                    flush=True,
                )
                try:
                    aria2("aria2.remove", [gid])
                except RuntimeError:
                    pass
                gid = _aria2_add_uri_magnet(magnet_url, aria_opts)
                track["gid"] = gid
                track["meta_retries"] = meta_retries
                update_job(job_id, aria2_gid=gid, status="running")
                return "running"
            if metadata_only:
                raise RuntimeError(
                    "Magnet finished after metadata only (no data files). "
                    "Retry when VPN is up and peers are available."
                )
            raise RuntimeError(
                f"Download marked complete but size is only {total} bytes — "
                "likely metadata or a failed allocation."
            )
        track["gid"] = gid
        track["meta_retries"] = meta_retries
        return "complete"
    if status in ("error", "removed"):
        raise RuntimeError(st.get("errorMessage") or f"aria2 {status}")
    track["gid"] = gid
    track["meta_retries"] = meta_retries
    return "running"


def wait_aria2(
    gid: str,
    job_id: str,
    *,
    magnet_url: str | None = None,
    aria_opts: dict[str, str] | None = None,
    dest_path: str | None = None,
) -> None:
    track: dict[str, Any] = {
        "job_id": job_id,
        "gid": gid,
        "magnet_url": magnet_url,
        "aria_opts": aria_opts,
        "dest_path": dest_path,
        "meta_retries": 0,
        "job": hub_get_job(job_id) or {"id": job_id},
    }
    last_heartbeat = 0.0
    last_search_poll = 0.0
    while True:
        now = time.monotonic()
        if now - last_search_poll >= SEARCH_POLL_DURING_DOWNLOAD_S:
            last_search_poll = now
            try_run_pending_search(vpn_ok(), context="during-download")
        if now - last_heartbeat >= 12.0:
            send_heartbeat(vpn_ok())
            last_heartbeat = now
        outcome = _aria2_wait_step(track)
        if outcome == "running":
            time.sleep(4)
            continue
        if outcome == "complete":
            return
        return


def _complete_aria2_post_download(job: dict[str, Any], gid: str) -> None:
    dest = str(job.get("destination_path") or "")
    aria_dir, post_copy = aria2_dir_for(dest) if dest else ("", None)
    host_dl = _aria2_host_download_dir(str(gid))
    if host_dl is not None:
        sidecar_dir = host_dl if host_dl.is_dir() else host_dl.parent
        write_hub_job_sidecar(sidecar_dir, job)
    if dest.startswith(MOVIES_USB_ROOT):
        notify_media_libraries_after_torrent(job, str(gid))
    elif post_copy:
        host_stage = Path("/mnt/media/_nas_staging") / dest[len("/mnt/tnas/") :]
        copy_tree(host_stage, Path(post_copy))
        write_tags_sidecar(post_copy, job)
    elif not host_dl and dest:
        write_tags_sidecar(dest, job)


def _begin_aria2_track(job: dict[str, Any]) -> dict[str, Any]:
    """Start or attach aria2 for a claimed job; returns supervisor track dict."""
    dest = job["destination_path"]
    ok, reason = path_ready(dest)
    if not ok:
        raise RuntimeError(reason)
    preflight = validate_aria2_destination(
        dest, mount_ok=lambda p: _safe_check_path(p, "mount")
    )
    if preflight:
        raise RuntimeError(preflight)
    ensure_host_dir(dest)
    if dest.rstrip("/") == "/mnt/external/movies":
        ensure_host_dir("/mnt/external/movies/torrents")
    aria_dir, post_copy = aria2_dir_for(dest)
    if dest.startswith("/mnt/external/movies/torrents"):
        aria_dir = ARIA2_TORRENTS_CONTAINER_DIR
    if post_copy:
        ensure_host_dir("/mnt/media/_nas_staging/" + dest[len("/mnt/tnas/") :])
    opts: dict[str, str] = {
        "dir": aria_dir,
        "file-allocation": "none",
        "bt-metadata-only": "false",
        **ARIA2_LEECH_OPTS,
    }
    _aria2_purge_stopped_results()
    url = str(job["url"])
    magnet_for_wait: str | None = None
    gid: str | None = str(job.get("aria2_gid") or "") or None
    if gid:
        try:
            st_row = aria2("aria2.tellStatus", [gid, ["status"]])
            a2_status = str(st_row.get("status") or "")
            if a2_status == "complete":
                st_full = aria2(
                    "aria2.tellStatus",
                    [gid, ["status", "completedLength", "totalLength", "files"]],
                )
                if not _aria2_status_fully_complete(st_full):
                    a2_status = "incomplete"
            if a2_status == "complete":
                pass
            elif a2_status == "paused":
                _aria2_unpause_gid(gid)
            elif a2_status in ("active", "waiting"):
                if url.startswith("magnet:"):
                    magnet_for_wait = url
            else:
                gid = None
        except RuntimeError:
            gid = None

    if not gid:
        if url.startswith("magnet:"):
            gid = _aria2_add_uri_magnet(url, opts)
            magnet_for_wait = url
        elif _is_jackett_torrent_url(url):
            magnet = _jackett_link_to_magnet(url)
            if magnet:
                gid = _aria2_add_uri_magnet(magnet, opts)
                magnet_for_wait = magnet
            else:
                torrent_bytes = _fetch_torrent_bytes(url)
                gid = _aria2_add_torrent_bytes(torrent_bytes, opts)
        else:
            raw_gid = aria2("aria2.addUri", [[url], opts])
            gid = str(raw_gid[0] if isinstance(raw_gid, list) else raw_gid)

    assert gid
    update_job(job["id"], aria2_gid=gid, status="running")
    st_row = aria2("aria2.tellStatus", [gid, ["status"]])
    if str(st_row.get("status") or "") == "complete":
        if _aria2_status_fully_complete(
            aria2(
                "aria2.tellStatus",
                [gid, ["status", "completedLength", "totalLength", "files"]],
            )
        ):
            _complete_aria2_post_download(job, gid)
            _aria2_detach_completed_gid(gid)
    return {
        "job_id": job["id"],
        "gid": gid,
        "magnet_url": magnet_for_wait,
        "aria_opts": opts if magnet_for_wait else None,
        "dest_path": str(dest),
        "meta_retries": 0,
        "job": job,
    }


def _aria2_supervisor_tick(vpn: tuple[bool, str]) -> None:
    global _supervisor_last_search, _supervisor_last_heartbeat
    if not ARIA2_TRACKS:
        return
    now = time.monotonic()
    if now - _supervisor_last_search >= SEARCH_POLL_DURING_DOWNLOAD_S:
        _supervisor_last_search = now
        try_run_pending_search(vpn, context="during-download")
    if now - _supervisor_last_heartbeat >= 12.0:
        send_heartbeat(vpn)
        _supervisor_last_heartbeat = now
    for job_id in list(ARIA2_TRACKS.keys()):
        track = ARIA2_TRACKS[job_id]
        try:
            outcome = _aria2_wait_step(track)
        except Exception as exc:  # noqa: BLE001
            print(f"aria2 track error {job_id}: {exc}", flush=True)
            if _aria2_rpc_transient(exc):
                continue
            try:
                update_job(job_id, status="error", error=str(exc))
            except Exception as upd:  # noqa: BLE001
                print(f"failed to mark error: {upd}", flush=True)
            ARIA2_TRACKS.pop(job_id, None)
            continue
        if outcome == "running":
            continue
        if outcome == "complete":
            job = track.get("job") or hub_get_job(job_id)
            done_gid = str(track["gid"])
            _complete_aria2_post_download(job, done_gid)
            _aria2_detach_completed_gid(done_gid)
            _finalize_after_aria2(job_id)
            if hub_get_job(job_id).get("status") != "paused":
                print(f"done {job_id}", flush=True)
            ARIA2_TRACKS.pop(job_id, None)
        else:
            ARIA2_TRACKS.pop(job_id, None)


def _video_pool() -> ThreadPoolExecutor:
    global _video_executor
    if _video_executor is None:
        workers = max(1, HUB_SLOT_VIDEO) if HUB_SLOT_VIDEO else 1
        _video_executor = ThreadPoolExecutor(max_workers=workers)
    return _video_executor


def _submit_video_job(job: dict[str, Any]) -> None:
    jid = str(job["id"])

    def _run() -> None:
        try:
            run_video_job(job)
            if hub_get_job(jid).get("status") != "paused":
                update_job(jid, status="done", error=None)
        except Exception as exc:  # noqa: BLE001
            print(f"error {jid}: {exc}", flush=True)
            try:
                update_job(jid, status="error", error=str(exc))
            except Exception as upd:  # noqa: BLE001
                print(f"failed to mark error: {upd}", flush=True)
        finally:
            _video_running.discard(jid)

    _video_running.add(jid)
    _video_pool().submit(_run)


def _attach_resumable_aria2_to_supervisor(job: dict[str, Any]) -> bool:
    jid = str(job.get("id") or "")
    if not jid or jid in ARIA2_TRACKS:
        return False
    if not _aria2_can_dispatch_more():
        return False
    try:
        track = _begin_aria2_track(job)
        if str(
            aria2("aria2.tellStatus", [track["gid"], ["status"]]).get("status") or ""
        ) == "complete":
            _finalize_after_aria2(jid)
            ARIA2_TRACKS.pop(jid, None)
            return True
        ARIA2_TRACKS[jid] = track
        print(f"supervisor attach {jid} gid={track['gid']}", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"attach failed {jid}: {exc}", flush=True)
        return False


def _dispatch_claimed_job(job: dict[str, Any], vpn: tuple[bool, str]) -> None:
    kind = (job.get("kind") or "http").lower()
    jid = str(job["id"])
    if kind in ("http", "magnet"):
        if not _aria2_can_dispatch_more():
            update_job(jid, status="queued", error=None)
            return
        track = _begin_aria2_track(job)
        st = str(
            aria2("aria2.tellStatus", [track["gid"], ["status"]]).get("status") or ""
        )
        if st == "complete":
            done_gid = str(track["gid"])
            _complete_aria2_post_download(job, done_gid)
            _aria2_detach_completed_gid(done_gid)
            _finalize_after_aria2(jid)
            return
        ARIA2_TRACKS[jid] = track
        bps = _aria2_total_download_bps()
        mbps = round(bps * 8 / 1_000_000, 1)
        print(
            f"aria2 slot {len(ARIA2_TRACKS)}/{HUB_ARIA2_MAX_ACTIVE} "
            f"~{mbps} Mbps agg {jid}",
            flush=True,
        )
        return
    if kind == "video" and HUB_SLOT_VIDEO > 0:
        if not vpn[0]:
            update_job(jid, status="queued", error=None)
            return
        _submit_video_job(job)
        return
    if not vpn[0] and kind not in ("sync", "storage_delete"):
        update_job(jid, status="queued", error=None)
        return
    try:
        process(job)
        if hub_get_job(jid).get("status") == "paused":
            print(f"paused {jid}", flush=True)
        else:
            print(f"done {jid}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"error {jid}: {exc}", flush=True)
        update_job(jid, status="error", error=str(exc))


def run_aria2_job(job: dict[str, Any]) -> None:
    """Blocking aria2 path (used when HUB_ARIA2_MAX_ACTIVE=1 or from process())."""
    track = _begin_aria2_track(job)
    gid = str(track["gid"])
    st = str(aria2("aria2.tellStatus", [gid, ["status"]]).get("status") or "")
    if st != "complete":
        wait_aria2(
            gid,
            job["id"],
            magnet_url=track.get("magnet_url"),
            aria_opts=track.get("aria_opts"),
            dest_path=track.get("dest_path"),
        )
        if hub_get_job(job["id"]).get("status") == "paused":
            return
    _complete_aria2_post_download(job, gid)
    _aria2_detach_completed_gid(gid)


def run_video_job(job: dict[str, Any]) -> None:
    dest = job["destination_path"]
    ok, reason = path_ready(dest)
    if not ok:
        raise RuntimeError(reason)
    ensure_host_dir(dest)
    update_job(job["id"], status="running")
    opts = job.get("options") or {}
    url = str(job.get("url") or "")
    out_tpl = "/out/%(title)s.%(ext)s"
    ytdlp = ["yt-dlp", "-o", out_tpl, "--no-progress"]
    if opts.get("extract_audio"):
        ytdlp.extend(["-x", "--audio-format", "mp3"])
    if opts.get("download_playlist"):
        ytdlp.append("--yes-playlist")
    else:
        ytdlp.append("--no-playlist")

    docker_args: list[str] = ["-v", f"{dest}:/out"]
    # YouTube over Proton needs signed-in cookies; MeTube image provides Deno.
    if is_youtube_url(url):
        if not youtube_cookies_ok():
            raise RuntimeError(
                "YouTube cookies missing — export Netscape cookies.txt to "
                f"{YTDLP_COOKIES} (see Hub ytdlp-cookies README). "
                "VPN/Proton IPs require a signed-in session."
            )
        docker_args.extend(["-v", f"{YTDLP_COOKIES}:/cookies/cookies.txt"])
        # Copy to a writable path: yt-dlp refreshes the cookie jar on exit.
        ytdlp_inner = [
            "sh",
            "-c",
            "cp /cookies/cookies.txt /tmp/cookies.txt && yt-dlp "
            + " ".join(shlex.quote(a) for a in ytdlp[1:])
            + " --cookies /tmp/cookies.txt "
            + shlex.quote(url),
        ]
        docker_gluetun(
            [*docker_args, "--entrypoint", "sh", METUBE_IMAGE, "-c", ytdlp_inner[2]],
            timeout=7200,
        )
    else:
        cookie_prefix = ""
        if youtube_cookies_ok():
            docker_args.extend(["-v", f"{YTDLP_COOKIES}:/cookies/cookies.txt:ro"])
            cookie_prefix = (
                "cp /cookies/cookies.txt /tmp/cookies.txt && "
            )
            ytdlp.extend(["--cookies", "/tmp/cookies.txt"])
        script = (
            cookie_prefix
            + "pip install -q -U yt-dlp && "
            + shlex.join(ytdlp + [url])
        )
        docker_gluetun(
            [*docker_args, HF_IMAGE, "bash", "-c", script],
            timeout=7200,
        )
    write_tags_sidecar(dest, job)


def run_github_job(job: dict[str, Any]) -> None:
    dest = job["destination_path"]
    ok, reason = path_ready(dest)
    if not ok:
        raise RuntimeError(reason)
    update_job(job["id"], status="running")
    url = job["url"]
    if not url.endswith(".git"):
        url = url.rstrip("/") + ".git"
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
    target = str(Path(dest) / repo_name)
    ensure_host_dir(target)
    docker_gluetun(
        ["-v", f"{target}:/repo", GIT_IMAGE, "clone", "--depth", "1", url, "/repo"],
        timeout=7200,
    )
    write_tags_sidecar(target, job)


def hf_repo_id(job: dict[str, Any]) -> str:
    hf = job.get("hf") or {}
    if hf.get("repo_id"):
        return hf["repo_id"]
    m = re.search(r"huggingface\.co/([^/]+/[^/\s?#]+)", job.get("url", ""))
    if m:
        return m.group(1)
    raise RuntimeError("HF repo_id missing")


def run_hf_job(job: dict[str, Any]) -> None:
    dest = job["destination_path"]
    ok, reason = path_ready(dest)
    if not ok:
        raise RuntimeError(reason)
    ensure_host_dir(dest)
    update_job(job["id"], status="running")
    repo = hf_repo_id(job)
    hf = job.get("hf") or {}
    mode = hf.get("mode") or "whole"
    files = hf.get("files") or []

    if mode == "files" and files:
        script = (
            "pip install -q huggingface_hub && python - <<'PY'\n"
            "from huggingface_hub import hf_hub_download\n"
            "from pathlib import Path\n"
            f"repo={repo!r}\n"
            f"files={files!r}\n"
            "dest=Path('/data')\n"
            "dest.mkdir(parents=True, exist_ok=True)\n"
            "for f in files:\n"
            "    hf_hub_download(repo_id=repo, filename=f, local_dir=str(dest), local_dir_use_symlinks=False)\n"
            "print('done', len(files))\n"
            "PY"
        )
        docker_gluetun(
            ["-v", f"{dest}:/data", HF_IMAGE, "bash", "-c", script],
            timeout=7200,
        )
    else:
        url = f"https://huggingface.co/{repo}"
        repo_dir = repo.split("/")[-1] or "hf-repo"
        target = str(Path(dest) / repo_dir)
        ensure_host_dir(target)
        docker_gluetun(
            ["-v", f"{target}:/repo", GIT_IMAGE, "clone", "--depth", "1", url, "/repo"],
            timeout=7200,
        )
        write_tags_sidecar(target, job)
        return
    write_tags_sidecar(dest, job)


def run_sync_job(job: dict[str, Any]) -> None:
    src = job.get("source_path") or ""
    dest = job.get("destination_path") or ""
    if not src or not dest:
        raise RuntimeError("sync job missing source_path or destination_path")
    if src.rstrip("/") == dest.rstrip("/"):
        raise RuntimeError("source and destination are the same path")
    ok_src, msg = path_ready(src)
    if not ok_src:
        raise RuntimeError(msg)
    ok_dst, msg = path_ready(dest)
    if not ok_dst:
        raise RuntimeError(msg)
    ensure_host_dir(dest)
    mirror = bool((job.get("options") or {}).get("mirror"))
    args = ["rsync", "-a", "--partial", "--info=progress2"]
    if mirror:
        args.append("--delete")
    src_path = Path(src)
    if src_path.is_file():
        args.extend([src, dest.rstrip("/") + "/"])
    else:
        src_arg = src if src.endswith("/") else src + "/"
        args.extend([src_arg, dest])
    print(f"sync {job['id']}: {' '.join(args)}", flush=True)
    subprocess.check_call(args, timeout=86400 * 7)
    write_tags_sidecar(dest, job)


def _storage_path_allowed(path_str: str) -> Path:
    raw = (path_str or "").strip()
    if not raw or ".." in Path(raw).parts:
        raise RuntimeError("invalid storage path")
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError("storage path must be absolute")
    text = str(path)
    allowed = False
    for root in STORAGE_VOLUME_ROOTS.values():
        root_s = str(root)
        if text == root_s or text.startswith(root_s + "/"):
            allowed = True
            break
    if not allowed:
        raise RuntimeError(f"path outside storage mounts: {text}")
    for root in STORAGE_VOLUME_ROOTS.values():
        if text == str(root) or text == str(root) + "/":
            raise RuntimeError("refusing to delete volume root")
    return path


def run_storage_delete_job(job: dict[str, Any]) -> None:
    path = _storage_path_allowed(job.get("source_path") or "")
    if not path.exists():
        raise RuntimeError(f"path missing: {path}")
    print(f"storage_delete {job['id']}: {path}", flush=True)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _write_netscape_cookies(cookies: list[dict[str, Any]], path: str) -> None:
    """Write cookies to a Netscape-format cookie file for httrack/curl."""
    with open(path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", "")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            cookie_path = c.get("path", "/")
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            expiry = str(int(c.get("expiry", 0)))
            name = c.get("name", "")
            value = c.get("value", "")
            f.write(f"{domain}\t{flag}\t{cookie_path}\t{secure}\t{expiry}\t{name}\t{value}\n")


def _solve_cloudflare(url: str) -> str | None:
    """Call FlareSolverr to solve a Cloudflare challenge.

    Returns the path to a Netscape cookie file on success, None on failure.
    The cookie file is written to /tmp/flaresolverr-cookies/<domain>.txt.
    """
    domain = urlparse(url).netloc or "unknown"
    cookie_dir = "/tmp/flaresolverr-cookies"
    os.makedirs(cookie_dir, exist_ok=True)
    cookie_path = f"{cookie_dir}/{domain}.txt"

    payload = json.dumps({
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }).encode()

    req = urllib.request.Request(
        f"{FLARESOLVERR_URL}/v1",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"FlareSolverr error: {exc}", flush=True)
        return None

    status = data.get("status", "")
    if status != "ok":
        print(f"FlareSolverr non-ok status: {status} — {data.get('message', '')}", flush=True)
        return None

    solution = data.get("solution", {})
    cookies = solution.get("cookies", [])
    if not cookies:
        print("FlareSolverr returned no cookies", flush=True)
        return None

    cf_cookies = [c for c in cookies if "cf_clearance" in c.get("name", "")]
    if not cf_cookies:
        print(f"FlareSolverr: no cf_clearance cookie in {len(cookies)} cookies", flush=True)

    _write_netscape_cookies(cookies, cookie_path)
    print(
        f"FlareSolverr: solved {domain}, {len(cookies)} cookies "
        f"({'cf_clearance found' if cf_cookies else 'no cf_clearance'})",
        flush=True,
    )
    return cookie_path


def _read_flaresolverr_ua() -> str | None:
    """Read the User-Agent that FlareSolverr uses, so httrack can match it."""
    try:
        req = urllib.request.Request(FLARESOLVERR_URL)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("userAgent")
    except Exception:
        return None


def _flaresolverr_request(url: str, session: str | None = None) -> dict[str, Any] | None:
    """Fetch a URL through FlareSolverr's Chrome browser.

    Returns the solution dict on success (keys: url, status, cookies, headers,
    response) or None on failure.
    """
    body: dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }
    if session:
        body["session"] = session
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{FLARESOLVERR_URL}/v1",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"FlareSolverr fetch error for {url}: {exc}", flush=True)
        return None
    if data.get("status") != "ok":
        return None
    return data.get("solution")


def _flaresolverr_session_create() -> str | None:
    """Create a persistent FlareSolverr session (reuses browser across requests)."""
    sid = f"hub-{_uuid.uuid4().hex[:8]}"
    body = json.dumps({"cmd": "sessions.create", "session": sid}).encode()
    req = urllib.request.Request(
        f"{FLARESOLVERR_URL}/v1",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "ok":
            return sid
    except Exception as exc:
        print(f"FlareSolverr session create error: {exc}", flush=True)
    return None


def _flaresolverr_session_destroy(session: str) -> None:
    """Destroy a FlareSolverr session."""
    body = json.dumps({"cmd": "sessions.destroy", "session": session}).encode()
    req = urllib.request.Request(
        f"{FLARESOLVERR_URL}/v1",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as _:
            pass
    except Exception:
        pass


_ARCHIVE_SKIP_PARAMS = {
    "action", "veaction", "oldid", "diff", "undoafter", "undo",
    "wpFormIdentifier", "returnto", "returntoquery", "redlink",
}
_ARCHIVE_SKIP_PATH_SEGMENTS = {
    "Special:", "Special%3A",
    "User:", "User%3A",
    "User_talk:", "User_talk%3A",
    "Talk:", "Talk%3A",
    "Template:", "Template%3A",
}


def _is_archive_junk_url(url: str) -> bool:
    """Return True for wiki/CMS URLs that are useless in a static archive."""
    parsed = urlparse(url)
    if parsed.query:
        params = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
        if params.keys() & _ARCHIVE_SKIP_PARAMS:
            return True
    path = parsed.path
    for seg in _ARCHIVE_SKIP_PATH_SEGMENTS:
        if seg in path:
            return True
    return False


def _extract_links(html: str, base_url: str) -> set[str]:
    """Extract internal links from HTML content, filtering archive junk."""
    parsed_base = urlparse(base_url)
    base_host = parsed_base.netloc
    links: set[str] = set()

    class LinkExtractor(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag != "a":
                return
            for attr, val in attrs:
                if attr == "href" and val:
                    self._process_href(val)

        def _process_href(self, href: str) -> None:
            href = href.split("#")[0].strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                return
            parsed = urlparse(href)
            if parsed.scheme and parsed.scheme not in ("http", "https"):
                return
            if parsed.netloc and parsed.netloc != base_host:
                return
            if not parsed.scheme and not parsed.netloc:
                if href.startswith("/"):
                    href = f"{parsed_base.scheme}://{base_host}{href}"
                else:
                    base_path = parsed_base.path.rsplit("/", 1)[0]
                    href = f"{parsed_base.scheme}://{base_host}{base_path}/{href}"
            elif not parsed.scheme:
                href = f"{parsed_base.scheme}://{href}"
            final = href.rstrip("/")
            if not _is_archive_junk_url(final):
                links.add(final)

    try:
        LinkExtractor().feed(html)
    except Exception:
        pass
    return links


def _extract_assets(html: str, base_url: str) -> set[str]:
    """Extract asset URLs (CSS, JS, images) from HTML."""
    parsed_base = urlparse(base_url)
    base_host = parsed_base.netloc
    assets: set[str] = set()

    class AssetExtractor(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attr_map = dict(attrs)
            src = None
            if tag in ("img", "script", "source", "video", "audio"):
                src = attr_map.get("src")
            elif tag == "link" and attr_map.get("rel") in ("stylesheet", "icon", "shortcut icon"):
                src = attr_map.get("href")
            if src:
                self._process(src)

        def _process(self, href: str) -> None:
            href = href.split("?")[0].split("#")[0].strip()
            if not href or href.startswith("data:"):
                return
            parsed = urlparse(href)
            if parsed.netloc and parsed.netloc != base_host:
                return
            if not parsed.scheme and not parsed.netloc:
                if href.startswith("/"):
                    href = f"{parsed_base.scheme}://{base_host}{href}"
                else:
                    base_path = parsed_base.path.rsplit("/", 1)[0]
                    href = f"{parsed_base.scheme}://{base_host}{base_path}/{href}"
            elif not parsed.scheme:
                href = f"{parsed_base.scheme}://{href}"
            assets.add(href)

    try:
        AssetExtractor().feed(html)
    except Exception:
        pass
    return assets


def _url_to_path(url: str, dest: str) -> str:
    """Convert a URL to a local file path under dest.

    Every page is stored as ``<path>/index.html`` so that a URL can be both
    a page and a directory prefix without filesystem conflicts (e.g. /Foo
    is a page at Foo/index.html and /Foo/Bar is Bar/index.html inside Foo/).
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/") or ""
    if parsed.query:
        safe_query = parsed.query.replace("&", "_").replace("=", "-")
        path = f"{path}__{safe_query}" if path else safe_query

    if path:
        dir_path = os.path.join(dest, parsed.netloc, path)
    else:
        dir_path = os.path.join(dest, parsed.netloc)

    try:
        os.makedirs(dir_path, exist_ok=True)
    except FileExistsError:
        if os.path.isfile(dir_path):
            tmp = dir_path + ".__tmp_rename"
            os.rename(dir_path, tmp)
            os.makedirs(dir_path, exist_ok=True)
            os.rename(tmp, os.path.join(dir_path, "index.html"))
    except NotADirectoryError:
        parts = Path(dir_path).parts
        for i in range(1, len(parts) + 1):
            check = os.path.join(*parts[:i])
            if os.path.isfile(check):
                tmp = check + ".__tmp_rename"
                os.rename(check, tmp)
                os.makedirs(check, exist_ok=True)
                os.rename(tmp, os.path.join(check, "index.html"))
        os.makedirs(dir_path, exist_ok=True)

    return os.path.join(dir_path, "index.html")


def _download_asset_via_gluetun(
    url: str,
    dest_path: str,
    cookies: list[dict[str, Any]] | None = None,
    user_agent: str | None = None,
) -> bool:
    """Download a single asset file via curl through Gluetun."""
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        return False
    cmd = [
        "docker", "run", "--rm", "--network", "container:gluetun",
        "-v", f"{os.path.dirname(dest_path)}:/dl",
        "curlimages/curl:latest",
        "-sL", "-o", f"/dl/{os.path.basename(dest_path)}",
        "--max-time", "30",
    ]
    if user_agent:
        cmd.extend(["-A", user_agent])
    if cookies:
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        cmd.extend(["-b", cookie_str])
    cmd.append(url)
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
        return os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0
    except Exception:
        return False


def _fetch_sitemap_urls(base_url: str) -> list[str]:
    """Discover all URLs from a site's sitemap.xml (fetched through Gluetun).

    Tries /sitemap.xml, then parses robots.txt for Sitemap directives.
    Handles both sitemap index files and regular sitemaps.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    found: set[str] = set()
    sitemap_urls_to_try: list[str] = [f"{origin}/sitemap.xml"]

    try:
        robots_out = subprocess.run(
            [
                "docker", "run", "--rm", "--network", "container:gluetun",
                "curlimages/curl:latest", "-sL", "--max-time", "15",
                "-A", "Mozilla/5.0", f"{origin}/robots.txt",
            ],
            capture_output=True, text=True, timeout=30,
        )
        for line in robots_out.stdout.splitlines():
            if line.strip().lower().startswith("sitemap:"):
                sm_url = line.split(":", 1)[1].strip()
                if sm_url and sm_url not in sitemap_urls_to_try:
                    sitemap_urls_to_try.append(sm_url)
    except Exception:
        pass

    def parse_sitemap(sm_url: str, recurse_depth: int = 0) -> None:
        if recurse_depth > 3:
            return
        try:
            result = subprocess.run(
                [
                    "docker", "run", "--rm", "--network", "container:gluetun",
                    "curlimages/curl:latest", "-sL", "--max-time", "20",
                    "-A", "Mozilla/5.0", sm_url,
                ],
                capture_output=True, text=True, timeout=30,
            )
            xml = result.stdout
        except Exception:
            return

        if "<sitemapindex" in xml:
            for match in re.finditer(r"<loc>\s*(https?://[^<]+)\s*</loc>", xml):
                parse_sitemap(match.group(1).strip(), recurse_depth + 1)
        else:
            for match in re.finditer(r"<loc>\s*(https?://[^<]+)\s*</loc>", xml):
                loc = match.group(1).strip()
                loc_parsed = urlparse(loc)
                if loc_parsed.netloc == parsed.netloc:
                    found.add(loc.rstrip("/"))

    for sm in sitemap_urls_to_try:
        parse_sitemap(sm)

    print(f"sitemap: discovered {len(found)} URLs from {origin}", flush=True)
    return sorted(found)


def _crawl_checkpoint_path(dest: str) -> str:
    return os.path.join(dest, ".crawl-checkpoint.json")


def _save_crawl_checkpoint(
    dest: str, visited: set[str], queue: list[tuple[str, int]], saved_pages: int,
) -> None:
    """Save crawl state to a checkpoint file for resume after failure."""
    data = {
        "visited": sorted(visited),
        "queue": queue[:50000],
        "saved_pages": saved_pages,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _crawl_checkpoint_path(dest)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"  checkpoint save failed: {exc}", flush=True)


def _load_crawl_checkpoint(dest: str) -> dict[str, Any] | None:
    """Load a previous crawl checkpoint if one exists."""
    path = _crawl_checkpoint_path(dest)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        visited = set(data.get("visited", []))
        queue = [tuple(q) for q in data.get("queue", [])]
        saved = data.get("saved_pages", 0)
        print(
            f"  checkpoint loaded: {saved} pages saved, "
            f"{len(visited)} visited, {len(queue)} queued "
            f"(from {data.get('saved_at', '?')})",
            flush=True,
        )
        return {"visited": visited, "queue": queue, "saved_pages": saved}
    except Exception as exc:
        print(f"  checkpoint load failed: {exc}", flush=True)
        return None


def _run_flaresolverr_crawl(dest: str, url: str, depth: int, job_id: str) -> None:
    """Crawl a Cloudflare-protected site using FlareSolverr as the browser engine.

    Each page is fetched through FlareSolverr's Chrome instance, which handles
    TLS fingerprinting and Cloudflare challenges.  Pages are saved as HTML files
    and linked assets are downloaded via curl through Gluetun.

    When depth == 0 (full site), the crawler also fetches sitemap.xml to seed
    the queue with every known page on the domain.

    Supports checkpoint/resume: crawl state is saved every 10 pages so a
    retried job continues where it left off instead of restarting.
    """
    session = _flaresolverr_session_create()
    if not session:
        raise RuntimeError("Failed to create FlareSolverr session")

    try:
        checkpoint = _load_crawl_checkpoint(dest)
        if checkpoint:
            visited = checkpoint["visited"]
            queue = checkpoint["queue"]
            saved_pages = checkpoint["saved_pages"]
            print(f"  RESUMING from checkpoint: {saved_pages} pages", flush=True)
            _dev_ops_log("worker", "INFO", f"Resuming crawl {job_id} from checkpoint ({saved_pages} pages)")
        else:
            visited: set[str] = set()
            queue: list[tuple[str, int]] = [(url.rstrip("/"), 0)]
            saved_pages = 0

        consecutive_errors = 0
        max_consecutive_errors = 20
        last_cookies: list[dict[str, Any]] = []
        user_agent = _read_flaresolverr_ua()

        if depth == 0 and not checkpoint:
            update_job(
                job_id,
                status="running",
                progress={"engine": "flaresolverr", "phase": "discovering sitemap"},
            )
            sitemap_urls = _fetch_sitemap_urls(url)
            for sm_url in sitemap_urls:
                if sm_url not in visited and not any(sm_url == q[0] for q in queue):
                    queue.append((sm_url, 0))

        while queue:
            current_url, current_depth = queue.pop(0)
            if current_url in visited:
                continue
            if _is_archive_junk_url(current_url):
                continue
            visited.add(current_url)

            solution = _flaresolverr_request(current_url, session=session)
            if not solution:
                consecutive_errors += 1
                print(f"  skip (no response): {current_url}", flush=True)
                if consecutive_errors >= max_consecutive_errors:
                    _save_crawl_checkpoint(dest, visited, queue, saved_pages)
                    print(
                        f"  BAIL: {consecutive_errors} consecutive errors, "
                        "FlareSolverr session likely crashed — checkpoint saved",
                        flush=True,
                    )
                    raise RuntimeError(
                        f"FlareSolverr session crashed after {saved_pages} pages "
                        f"({consecutive_errors} consecutive errors) — retry to resume"
                    )
                continue

            response_html = solution.get("response", "")
            sol_status = solution.get("status", 0)
            if sol_status and sol_status >= 400:
                consecutive_errors += 1
                print(f"  skip (HTTP {sol_status}): {current_url}", flush=True)
                if consecutive_errors >= max_consecutive_errors:
                    _save_crawl_checkpoint(dest, visited, queue, saved_pages)
                    print(
                        f"  BAIL: {consecutive_errors} consecutive errors — checkpoint saved",
                        flush=True,
                    )
                    raise RuntimeError(
                        f"FlareSolverr session crashed after {saved_pages} pages "
                        f"({consecutive_errors} consecutive errors) — retry to resume"
                    )
                continue

            consecutive_errors = 0
            last_cookies = solution.get("cookies", [])
            file_path = _url_to_path(current_url, dest)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(response_html)
            saved_pages += 1

            if saved_pages % 3 == 0 or saved_pages < 10:
                update_job(
                    job_id,
                    status="running",
                    progress={
                        "engine": "flaresolverr",
                        "phase": "crawling",
                        "pages": saved_pages,
                        "queued": len(queue),
                        "visited": len(visited),
                    },
                )
            if saved_pages % 10 == 0:
                print(
                    f"  crawl: {saved_pages} pages saved, {len(queue)} queued, "
                    f"{len(visited)} visited",
                    flush=True,
                )
                _save_crawl_checkpoint(dest, visited, queue, saved_pages)

            if depth == 0 or current_depth < depth:
                new_links = _extract_links(response_html, current_url)
                for link in sorted(new_links):
                    if link not in visited:
                        queue.append((link, current_depth + 1))

            assets = _extract_assets(response_html, current_url)
            for asset_url in assets:
                asset_path = _url_to_path(asset_url, dest)
                if not os.path.isfile(asset_path):
                    _download_asset_via_gluetun(
                        asset_url, asset_path, last_cookies, user_agent,
                    )

            time.sleep(0.3)

        cp_path = _crawl_checkpoint_path(dest)
        if os.path.isfile(cp_path):
            os.remove(cp_path)
        print(
            f"FlareSolverr crawl complete: {saved_pages} pages, "
            f"{len(visited)} URLs visited",
            flush=True,
        )
        _dev_ops_log("worker", "INFO", f"Crawl {job_id} complete: {saved_pages} pages")
    finally:
        _flaresolverr_session_destroy(session)


# ---------------------------------------------------------------------------
# MediaWiki API batch archiver
# ---------------------------------------------------------------------------

def _flaresolverr_api_json(
    url: str, session: str, *, retries: int = 2,
) -> dict[str, Any]:
    """Fetch a MediaWiki API URL via FlareSolverr, return parsed JSON.

    FlareSolverr wraps JSON responses in an HTML <pre> tag, so we extract
    the inner content before parsing.
    """
    for attempt in range(retries + 1):
        sol = _flaresolverr_request(url, session=session)
        if not sol:
            if attempt < retries:
                time.sleep(2)
                continue
            raise RuntimeError(f"FlareSolverr returned no response for {url}")

        resp = sol.get("response", "")
        m = re.search(r"<pre[^>]*>(.*?)</pre>", resp, re.DOTALL)
        raw = m.group(1) if m else resp

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if attempt < retries:
                time.sleep(2)
                continue
            raise RuntimeError(f"Invalid JSON from MediaWiki API: {resp[:200]}")

    raise RuntimeError("unreachable")


def _safe_wiki_filename(title: str) -> str:
    """Convert a wiki page title to a safe filename."""
    safe = title.replace("/", "__SLASH__")
    safe = re.sub(r'[<>:"|?*\\]', "_", safe)
    safe = safe.strip(". ")
    return safe or "_untitled"


def _detect_mediawiki(url: str, session: str) -> bool:
    """Check if a URL is a MediaWiki site by probing the API."""
    base = url.rstrip("/")
    api_url = f"{base}/api.php?action=query&meta=siteinfo&format=json"
    try:
        data = _flaresolverr_api_json(api_url, session, retries=1)
        return "query" in data and "general" in data.get("query", {})
    except Exception:
        return False


def _run_mediawiki_api_dump(dest: str, url: str, job_id: str) -> None:
    """Archive a MediaWiki site via its API in batch mode.

    Much faster than page-by-page FlareSolverr crawling because the API
    returns 50 pages of content per request.

    Output structure:
        <dest>/page-index.json        — all article titles + metadata
        <dest>/wikitext/<Title>.wiki  — raw wikitext source
        <dest>/html/<Title>.html      — rendered HTML (server-side)
    """
    session = _flaresolverr_session_create()
    if not session:
        raise RuntimeError("Failed to create FlareSolverr session")

    base = url.rstrip("/")
    api_base = f"{base}/api.php"
    wiki_dir = os.path.join(dest, "wikitext")
    html_dir = os.path.join(dest, "html")
    os.makedirs(wiki_dir, exist_ok=True)
    os.makedirs(html_dir, exist_ok=True)

    checkpoint_path = os.path.join(dest, ".api-dump-checkpoint.json")

    try:
        # ── Load checkpoint if resuming ──
        checkpoint: dict[str, Any] | None = None
        if os.path.isfile(checkpoint_path):
            try:
                with open(checkpoint_path) as f:
                    checkpoint = json.load(f)
                print(f"  RESUMING API dump from checkpoint", flush=True)
            except Exception:
                checkpoint = None

        # ── Phase 1: list all articles ──
        if checkpoint and checkpoint.get("titles"):
            titles: list[str] = checkpoint["titles"]
            print(f"  Loaded {len(titles)} titles from checkpoint", flush=True)
        else:
            update_job(job_id, status="running", progress={
                "engine": "mediawiki-api", "phase": "listing articles",
            })

            titles = []
            apcontinue: str | None = None
            while True:
                params = "action=query&list=allpages&apnamespace=0&aplimit=500&format=json"
                if apcontinue:
                    params += f"&apcontinue={quote(apcontinue, safe='')}"

                data = _flaresolverr_api_json(f"{api_base}?{params}", session)
                pages = data.get("query", {}).get("allpages", [])
                titles.extend(p["title"] for p in pages)

                cont = data.get("continue", {})
                if "apcontinue" in cont:
                    apcontinue = cont["apcontinue"]
                    update_job(job_id, status="running", progress={
                        "engine": "mediawiki-api", "phase": "listing articles",
                        "titles_found": len(titles),
                    })
                else:
                    break

            print(f"  Found {len(titles)} articles", flush=True)
            _dev_ops_log("worker", "INFO", f"API dump {job_id}: {len(titles)} articles found")

        # Save title index
        with open(os.path.join(dest, "page-index.json"), "w") as f:
            json.dump({"total": len(titles), "titles": titles}, f)

        # ── Phase 2: batch wikitext (50 per API call) ──
        wikitext_done = checkpoint.get("wikitext_done", 0) if checkpoint else 0
        gapcontinue = checkpoint.get("wikitext_gapcontinue") if checkpoint else None

        if wikitext_done < len(titles):
            update_job(job_id, status="running", progress={
                "engine": "mediawiki-api", "phase": "downloading wikitext",
                "articles": len(titles), "saved": wikitext_done,
            })

            batch_size = 50
            while True:
                params = (
                    f"action=query&generator=allpages&gapnamespace=0"
                    f"&gaplimit={batch_size}"
                    f"&prop=revisions&rvprop=content|timestamp&rvslots=main"
                    f"&format=json"
                )
                if gapcontinue:
                    params += f"&gapcontinue={quote(gapcontinue, safe='')}"

                data = _flaresolverr_api_json(f"{api_base}?{params}", session)
                pages = data.get("query", {}).get("pages", {})

                for _pid, info in pages.items():
                    title = info.get("title", "")
                    revisions = info.get("revisions", [])
                    if not revisions:
                        continue
                    content = revisions[0].get("slots", {}).get("main", {}).get("*", "")
                    safe = _safe_wiki_filename(title)
                    with open(os.path.join(wiki_dir, f"{safe}.wiki"), "w", encoding="utf-8") as f:
                        f.write(content)
                    wikitext_done += 1

                cont = data.get("continue", {})
                if "gapcontinue" in cont:
                    gapcontinue = cont["gapcontinue"]
                else:
                    gapcontinue = None
                    break

                if wikitext_done % 50 == 0:
                    print(f"  wikitext: {wikitext_done}/{len(titles)}", flush=True)
                    update_job(job_id, status="running", progress={
                        "engine": "mediawiki-api", "phase": "downloading wikitext",
                        "articles": len(titles), "saved": wikitext_done,
                    })
                    # Checkpoint every batch
                    with open(checkpoint_path, "w") as f:
                        json.dump({
                            "titles": titles,
                            "wikitext_done": wikitext_done,
                            "wikitext_gapcontinue": gapcontinue,
                            "html_done": 0,
                        }, f)

            print(f"  Wikitext complete: {wikitext_done} articles", flush=True)
            _dev_ops_log("worker", "INFO", f"API dump {job_id}: wikitext done ({wikitext_done})")

        # ── Phase 3: rendered HTML via action=parse (1 per call, slower) ──
        html_done = checkpoint.get("html_done", 0) if checkpoint else 0
        already_rendered = set()
        if html_done > 0:
            already_rendered = {
                f.replace(".html", "") for f in os.listdir(html_dir) if f.endswith(".html")
            }

        update_job(job_id, status="running", progress={
            "engine": "mediawiki-api", "phase": "rendering HTML",
            "articles": len(titles), "rendered": html_done,
        })

        for title in titles:
            safe = _safe_wiki_filename(title)
            if safe in already_rendered:
                continue

            params = f"action=parse&page={quote(title, safe='')}&prop=text&format=json"
            try:
                data = _flaresolverr_api_json(f"{api_base}?{params}", session)
            except Exception as exc:
                print(f"  skip HTML ({title}): {exc}", flush=True)
                continue

            html = data.get("parse", {}).get("text", {}).get("*", "")
            if html:
                with open(os.path.join(html_dir, f"{safe}.html"), "w", encoding="utf-8") as f:
                    f.write(html)
            html_done += 1

            if html_done % 10 == 0:
                print(f"  html: {html_done}/{len(titles)}", flush=True)
                update_job(job_id, status="running", progress={
                    "engine": "mediawiki-api", "phase": "rendering HTML",
                    "articles": len(titles), "rendered": html_done,
                })
            if html_done % 50 == 0:
                with open(checkpoint_path, "w") as f:
                    json.dump({
                        "titles": titles,
                        "wikitext_done": wikitext_done,
                        "wikitext_gapcontinue": None,
                        "html_done": html_done,
                    }, f)

        # Clean up checkpoint
        if os.path.isfile(checkpoint_path):
            os.remove(checkpoint_path)

        print(
            f"MediaWiki API dump complete: {wikitext_done} wikitext + {html_done} HTML",
            flush=True,
        )
        _dev_ops_log(
            "worker", "INFO",
            f"API dump {job_id} complete: {wikitext_done} wikitext, {html_done} HTML",
        )

    finally:
        _flaresolverr_session_destroy(session)


def _site_needs_browser(url: str) -> bool:
    """Quick probe: returns True if the site returns 403 (Cloudflare JS challenge)."""
    try:
        code = subprocess.run(
            [
                "docker", "run", "--rm", "--network", "container:gluetun",
                "curlimages/curl:latest",
                "-sI", "-o", "/dev/null", "-w", "%{http_code}",
                "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
                url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        return code.stdout.strip() == "403"
    except Exception:
        return True


def _run_httrack(
    dest: str,
    url: str,
    depth: int,
    cookie_file: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Mirror with httrack (lightweight, works on unprotected sites).

    When *cookie_file* is given (Netscape format on the host), it is mounted
    into the container and passed to httrack via the cookie-injection flags.
    """
    depth_flag = f"-r{depth}" if depth > 0 else "-r99"

    extra_docker: list[str] = []
    extra_httrack: list[str] = []
    if cookie_file and os.path.isfile(cookie_file):
        extra_docker.extend(["-v", f"{cookie_file}:/cookies.txt:ro"])
        extra_httrack.extend(["-b", "/cookies.txt"])
    if user_agent:
        extra_httrack.extend([f'-F "User-Agent: {user_agent}"'])

    docker_gluetun(
        [
            "-v", f"{dest}:/output",
            *extra_docker,
            HTTRACK_IMAGE,
            url,
            "-O", "/output",
            depth_flag,
            "+*.css", "+*.js", "+*.png", "+*.jpg", "+*.jpeg", "+*.gif",
            "+*.svg", "+*.webp", "+*.ico", "+*.woff", "+*.woff2",
            "+*.mp4", "+*.webm", "+*.ogg", "+*.mp3",
            "+*.pdf", "+*.json",
            "--mirror",
            "--near",
            "--robots=0",
            "--keep-alive",
            "--connection-per-second=2",
            "--max-rate=500000",
            "--timeout=60",
            "--retries=3",
            "--sockets=4",
            *extra_httrack,
        ],
        timeout=86400,
    )


def _run_browsertrix(dest: str, url: str, depth: int) -> None:
    """Mirror with browsertrix-crawler (headless Chromium, handles Cloudflare).

    Chromium cannot run on a CIFS mount (file locking), so stage locally
    on /mnt/media/_nas_staging/ when the destination is TNAS, then copy.
    """
    stage_dir = f"/tmp/hub-archive-staging/{Path(dest).name}"
    ensure_host_dir(stage_dir)

    scope = "host"
    args = [
        "--shm-size=1g",
        "-v", f"{stage_dir}:/crawls",
        BROWSERTRIX_IMAGE,
        "crawl",
        "--url", url,
        "--scopeType", scope,
        "--generateWACZ",
        "--behaviors", "autoscroll,autoplay,autofetch,siteSpecific",
        "--workers", "1",
        "--waitUntil", "load,networkidle0",
        "--pageLoadTimeout", "90",
        "--timeout", "300",
        "--combineWARC",
    ]
    if depth > 0:
        args.extend(["--depth", str(depth)])
    docker_gluetun(args, timeout=86400)

    ensure_host_dir(dest)
    copy_tree(Path(stage_dir), Path(dest))


def run_website_archive_job(job: dict[str, Any]) -> None:
    """Mirror a full website.

    Flow:
    1. Probe the URL through Gluetun.
    2. If 403 (Cloudflare), call FlareSolverr for bypass cookies, then use
       httrack with those cookies.  FlareSolverr also runs through Gluetun so
       the cf_clearance cookie is bound to the same egress IP.
    3. If the probe succeeds, use plain httrack (no cookies needed).
    4. Fallback to browsertrix only when explicitly requested via engine option.
    """
    dest = job["destination_path"]
    ok, reason = path_ready(dest)
    if not ok:
        raise RuntimeError(reason)
    ensure_host_dir(dest)
    update_job(job["id"], status="running")

    url = job["url"]
    opts = job.get("options") or {}
    depth = int(opts.get("depth", 0))
    engine = opts.get("engine", "auto")

    if engine == "auto":
        needs_browser = _site_needs_browser(url)
        if needs_browser:
            # Check if it's a MediaWiki site — API batch is much faster
            probe_session = _flaresolverr_session_create()
            if probe_session:
                try:
                    if _detect_mediawiki(url, probe_session):
                        engine = "mediawiki-api"
                        print(f"archive {job['id']}: MediaWiki detected → API batch dump", flush=True)
                    else:
                        engine = "flaresolverr"
                        print(f"archive {job['id']}: 403 detected → FlareSolverr crawl", flush=True)
                finally:
                    _flaresolverr_session_destroy(probe_session)
            else:
                engine = "flaresolverr"
                print(f"archive {job['id']}: 403 detected → FlareSolverr crawl", flush=True)
        else:
            engine = "httrack"
            print(f"archive {job['id']}: probe OK → httrack", flush=True)

    update_job(
        job["id"],
        status="running",
        progress={"engine": engine, "phase": "crawling"},
    )

    if engine == "mediawiki-api":
        _run_mediawiki_api_dump(dest, url, job["id"])
    elif engine == "flaresolverr":
        _run_flaresolverr_crawl(dest, url, depth, job["id"])
    elif engine == "browsertrix":
        _run_browsertrix(dest, url, depth)
    else:
        _run_httrack(dest, url, depth)

    file_count = 0
    byte_total = 0
    try:
        dest_path = Path(dest)
        for f in dest_path.rglob("*"):
            if f.is_file():
                file_count += 1
                try:
                    byte_total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    update_job(
        job["id"],
        progress={
            "engine": engine,
            "phase": "complete",
            "files": file_count,
            "bytes": byte_total,
        },
    )
    write_tags_sidecar(dest, job)


def build_storage_tree(
    root: Path,
    *,
    max_depth: int = STORAGE_MAX_DEPTH,
    max_children: int = STORAGE_MAX_CHILDREN,
) -> dict[str, Any]:
    """Bottom-up size tree for Hub treemap (no Proton)."""

    def walk(path: Path, depth: int) -> dict[str, Any]:
        name = path.name if path != root else (path.name or str(path))
        try:
            is_dir = path.is_dir() and not path.is_symlink()
        except OSError:
            return {
                "name": name,
                "path": str(path),
                "size": 0,
                "type": "file",
                "children": [],
            }
        if not is_dir:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            return {
                "name": name,
                "path": str(path),
                "size": size,
                "type": "file",
                "children": [],
            }
        children: list[dict[str, Any]] = []
        if depth >= max_depth:
            try:
                out = subprocess.check_output(
                    ["du", "-sb", str(path)],
                    text=True,
                    timeout=600,
                    stderr=subprocess.DEVNULL,
                )
                size = int(out.split()[0])
            except Exception:  # noqa: BLE001
                size = 0
            return {
                "name": name,
                "path": str(path),
                "size": size,
                "type": "dir",
                "children": [],
                "truncated": True,
            }
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            try:
                children.append(walk(entry, depth + 1))
            except OSError:
                continue
        children.sort(key=lambda c: int(c.get("size") or 0), reverse=True)
        if len(children) > max_children:
            rest = children[max_children:]
            children = children[:max_children]
            children.append(
                {
                    "name": f"+{len(rest)} more",
                    "path": "",
                    "size": sum(int(c.get("size") or 0) for c in rest),
                    "type": "other",
                    "children": [],
                }
            )
        return {
            "name": name,
            "path": str(path),
            "size": sum(int(c.get("size") or 0) for c in children),
            "type": "dir",
            "children": children,
        }

    return walk(root, 0)


def consume_archive_probe_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/archive/probe/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"archive probe pending check failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    return result.get("request") or {}


def run_archive_probe(req: dict[str, Any]) -> None:
    url = str(req.get("url") or "").strip()
    if not url:
        hub_call(
            "/api/archive/probe/complete",
            method="POST",
            body={"ok": False, "error": "missing url", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        )
        return
    engine = "httrack"
    detail = "Unprotected site — httrack mirror"
    cloudflare = False
    try:
        cloudflare = _site_needs_browser(url)
        if cloudflare:
            session = _flaresolverr_session_create()
            try:
                if session and _detect_mediawiki(url, session):
                    engine = "mediawiki-api"
                    detail = "MediaWiki — API batch via FlareSolverr"
                else:
                    engine = "flaresolverr"
                    detail = "Cloudflare — Chrome crawl via FlareSolverr"
            finally:
                if session:
                    _flaresolverr_session_destroy(session)
    except Exception as exc:  # noqa: BLE001
        hub_call(
            "/api/archive/probe/complete",
            method="POST",
            body={
                "ok": False,
                "url": url,
                "error": str(exc),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        return
    hub_call(
        "/api/archive/probe/complete",
        method="POST",
        body={
            "ok": True,
            "url": url,
            "engine": engine,
            "detail": detail,
            "cloudflare": cloudflare,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def consume_storage_scan_request() -> dict[str, Any] | None:
    try:
        result = hub_call("/api/storage/scan/pending", method="GET")
    except Exception as exc:  # noqa: BLE001
        print(f"storage scan pending check failed: {exc}", flush=True)
        return None
    if not result.get("pending"):
        return None
    return result.get("request") or {}


def run_storage_scan(req: dict[str, Any]) -> None:
    volume = str(req.get("volume") or "media")
    targets = (
        list(STORAGE_VOLUME_ROOTS.keys())
        if volume == "all"
        else [volume]
    )
    ensure_mounts()
    for vid in targets:
        root = STORAGE_VOLUME_ROOTS.get(vid)
        if root is None:
            continue
        print(f"storage scan {vid} → {root}", flush=True)
        error = None
        tree = None
        try:
            if vid == "media" and not root.is_mount():
                raise RuntimeError("Media HDD is not mounted")
            if vid == "tnas" and not root.is_mount():
                raise RuntimeError("NAS is not mounted")
            if vid == "usb":
                if not root.exists():
                    raise RuntimeError("USB mount base missing")
                # Prefer first mounted child under /mnt/external
                kids = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
                scan_root = kids[0] if kids else root
            else:
                scan_root = root
            if not scan_root.exists():
                raise RuntimeError(f"path missing: {scan_root}")
            tree = build_storage_tree(scan_root)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            print(f"storage scan {vid} failed: {exc}", flush=True)
        try:
            hub_call(
                "/api/storage/scan/complete",
                method="POST",
                body={
                    "volume": vid,
                    "scanned_at": time.time(),
                    "tree": tree,
                    "error": error,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"storage scan complete ack failed: {exc}", flush=True)


def process(job: dict[str, Any]) -> None:
    kind = job.get("kind") or "http"
    if kind == "sync":
        run_sync_job(job)
    elif kind == "storage_delete":
        run_storage_delete_job(job)
    elif kind in ("http", "magnet"):
        run_aria2_job(job)
        _finalize_after_aria2(job["id"])
        return
    elif kind == "video":
        run_video_job(job)
    elif kind == "github":
        run_github_job(job)
    elif kind == "hf":
        run_hf_job(job)
    elif kind == "website_archive":
        run_website_archive_job(job)
    else:
        raise RuntimeError(f"unsupported kind: {kind}")
    update_job(job["id"], status="done", error=None)


def main() -> None:
    if not WORKER_TOKEN:
        raise SystemExit("HUB_WORKER_TOKEN is required")
    print(
        f"hub-worker v2.2 starting; hubs={hub_urls()} poll={POLL_S}s "
        f"aria2_max={HUB_ARIA2_MAX_ACTIVE} aria2_target_mbps={HUB_ARIA2_TARGET_MBPS} "
        f"video_slots={HUB_SLOT_VIDEO}",
        flush=True,
    )
    while True:
        try:
            ensure_mounts()
            vpn = vpn_ok()
            try_run_pending_search(vpn, context="idle")
            research_req = consume_research_request()
            if research_req:
                print(
                    f"research via Proton: {research_req.get('preset')!r}",
                    flush=True,
                )
                run_private_research(research_req, vpn)
            hf_req = consume_hf_files_request()
            if hf_req:
                print(
                    f"HF files via Proton: {hf_req.get('repo_id')!r}",
                    flush=True,
                )
                run_private_hf_files(hf_req, vpn)
            search_busy = False
            try:
                search_busy = bool(
                    hub_call("/api/search/pending", method="GET").get("pending")
                )
            except Exception:
                search_busy = False
            indexers_scan_req = consume_indexers_scan_request()
            if indexers_scan_req is not None and not search_busy:
                run_indexers_scan(indexers_scan_req)
            candidates_check_req = consume_indexers_candidates_check_request()
            if candidates_check_req is not None and not search_busy:
                print("indexers mirror URL check via Gluetun", flush=True)
                run_indexers_candidates_check(candidates_check_req)
            archive_probe_req = consume_archive_probe_request()
            if archive_probe_req is not None:
                print(f"archive probe: {archive_probe_req.get('url')!r}", flush=True)
                run_archive_probe(archive_probe_req)
            storage_scan_req = consume_storage_scan_request()
            if storage_scan_req is not None:
                print(
                    f"storage scan requested: {storage_scan_req.get('volume')!r}",
                    flush=True,
                )
                run_storage_scan(storage_scan_req)
            scan_requested = consume_scan_request()
            if scan_requested:
                print("on-demand drive scan requested", flush=True)
                remount_media_if_stale()
                ensure_mounts()
            drives = send_heartbeat(vpn, force_remount=scan_requested)
            if scan_requested:
                acknowledge_scan(True, f"found {len(drives)} drive(s)", drives)
            purge_job = consume_purge_request()
            if purge_job:
                try:
                    run_purge_job(purge_job)
                except Exception as exc:  # noqa: BLE001
                    print(f"purge failed {purge_job.get('id')}: {exc}", flush=True)
                continue
            _recover_claimed_aria2_jobs(vpn)
            _aria2_supervisor_tick(vpn)
            resume = find_resumable_aria2_job()
            if resume and str(resume.get("id") or "") not in ARIA2_TRACKS:
                _attach_resumable_aria2_to_supervisor(resume)
            while _aria2_can_dispatch_more():
                slot_job = claim_next()
                if not slot_job:
                    break
                slot_kind = (slot_job.get("kind") or "http").lower()
                if slot_kind not in ("http", "magnet"):
                    update_job(slot_job["id"], status="queued", error=None)
                    break
                print(
                    f"claimed {slot_job['id']} {slot_kind} -> "
                    f"{slot_job.get('destination_path') or slot_job.get('source_path')}",
                    flush=True,
                )
                _dispatch_claimed_job(slot_job, vpn)
            if HUB_SLOT_VIDEO > 0 and len(_video_running) < HUB_SLOT_VIDEO:
                video_job = claim_next()
                if video_job and (video_job.get("kind") or "").lower() == "video":
                    print(
                        f"claimed {video_job['id']} video -> "
                        f"{video_job.get('destination_path')}",
                        flush=True,
                    )
                    _dispatch_claimed_job(video_job, vpn)
                elif video_job:
                    update_job(video_job["id"], status="queued", error=None)
            if ARIA2_TRACKS or _video_running:
                time.sleep(4)
                continue
            job = claim_next()
            if job:
                kind = job.get("kind") or "http"
                print(
                    f"claimed {job['id']} {kind} -> "
                    f"{job.get('destination_path') or job.get('source_path')}",
                    flush=True,
                )
                _dispatch_claimed_job(job, vpn)
                continue
            if not vpn[0]:
                print(f"waiting for vpn: {vpn[1]}", flush=True)
            time.sleep(POLL_S)
        except urllib.error.HTTPError as exc:
            print(f"hub http {exc.code}: {exc.read()[:200]!r}", flush=True)
            time.sleep(POLL_S)
        except Exception as exc:  # noqa: BLE001
            print(f"loop error: {exc}", flush=True)
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()
