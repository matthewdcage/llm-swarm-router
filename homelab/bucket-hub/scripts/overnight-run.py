#!/usr/bin/env python3
"""Execute Bucket-HUB overnight schedule (complete-or-timeout per step)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HUB_BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:3020").rstrip("/")
JOBS_DIR = Path(os.environ.get("JOBS_DIR", os.path.expanduser("~/homelab/data/hub-jobs")))
SCHEDULE_FILE = JOBS_DIR / "overnight-schedule.json"
RUNS_DIR = JOBS_DIR / "overnight-runs"
STATE_FILE = JOBS_DIR / "overnight-run-state.json"
TZ = ZoneInfo(os.environ.get("HUB_TZ", "Australia/Brisbane"))
UBUNTU_SSH = os.environ.get("OVERNIGHT_UBUNTU_SSH", "bucket@192.168.50.65")
SSH_IDENTITY = os.environ.get("MACHINES_SSH_IDENTITY", "").strip()
DSH_SCRIPT = os.environ.get("OVERNIGHT_DSH_SCRIPT", "~/homelab/scripts/overnight-dsh-task.sh")


def log(msg: str) -> None:
    print(f"[overnight-run] {msg}", flush=True)


def write_state(state: dict[str, Any]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def hub_json(method: str, path: str, body: dict | None = None, timeout: float = 600.0) -> dict:
    url = f"{HUB_BASE}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def append_run(entry: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    path = RUNS_DIR / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def load_schedule() -> dict:
    if SCHEDULE_FILE.is_file():
        return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    default = Path(__file__).with_name("overnight-schedule.default.json")
    if default.is_file():
        return json.loads(default.read_text(encoding="utf-8"))
    raise SystemExit(f"No schedule at {SCHEDULE_FILE}")


def step_timeout_min(step: dict) -> int:
    return int(step.get("timeout_min") or step.get("duration_min") or 5)


def ssh_ubuntu(cmd: str, timeout: float = 600.0) -> tuple[int, str]:
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if SSH_IDENTITY:
        args.extend(["-i", SSH_IDENTITY])
    args.append(UBUNTU_SSH)
    args.append(cmd)
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode, out


def poll_until(
    deadline: float,
    interval: float,
    check,
) -> tuple[bool, Any]:
    """Return (completed, result) when check() is truthy, or (False, None) at deadline."""
    while time.time() < deadline:
        result = check()
        if result:
            return True, result
        time.sleep(interval)
    return False, None


def run_step_body(step: dict) -> dict:
    stype = step.get("type") or ""

    if stype == "machine_wake":
        mid = step.get("target") or "tnas"
        return hub_json("POST", f"/api/machines/{mid}/wake")
    if stype == "machine_sleep":
        mid = step.get("target") or "ubuntu"
        return hub_json("POST", f"/api/machines/{mid}/sleep")
    if stype == "mount_check":
        st = hub_json("GET", "/api/status")
        w = st.get("worker") or {}
        if not w.get("media_mounted"):
            raise RuntimeError("Media HDD not mounted on worker")
        return {"ok": True, "worker": w}
    if stype == "llm_start":
        return hub_json("POST", "/api/llm/start", timeout=720.0)
    if stype == "llm_stop":
        return hub_json("POST", "/api/llm/stop", timeout=180.0)
    if stype in ("research_synthesis", "dsh_research"):
        hub_json("POST", "/api/research", {"preset": "ai_ml"})
        return {"ok": True, "research_queued": True}
    if stype in ("log_review", "dsh_log_review"):
        jobs = hub_json("GET", "/api/jobs/summary/last-night")
        path = Path.home() / "homelab/data/logs/overnight-log-input.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        return {"ok": True, "input": str(path)}
    if stype == "sync_job":
        log("seedvault sync disabled unless configured")
        return {"ok": True, "skipped": True}
    if stype == "queue_drain":
        try:
            jobs_data = hub_json("GET", "/api/jobs?limit=50")
            all_jobs = jobs_data if isinstance(jobs_data, list) else jobs_data.get("jobs") or []
        except Exception:
            all_jobs = []
        active = [
            j
            for j in all_jobs
            if j.get("status") in ("queued", "running", "claimed")
        ]
        log(f"Queue drain: {len(active)} active jobs to process (paused jobs do not block)")
        return {"ok": True, "active": len(active), "total_jobs": len(all_jobs)}
    if stype == "storage_scan":
        hub_json("POST", "/api/storage/scan", {"volume": "all"})
        return {"ok": True}
    raise RuntimeError(f"Unknown step type: {stype}")


def run_step_poll(step: dict, deadline: float) -> dict:
    """Poll for completion after run_step_body kick-off; returns when done or raises on hard fail."""
    stype = step.get("type") or ""

    if stype in ("research_synthesis", "dsh_research"):
        def check_research():
            try:
                data = hub_json("GET", "/api/research?preset=ai_ml")
                if data.get("results"):
                    return data
            except urllib.error.HTTPError:
                pass
            return None

        done, data = poll_until(deadline, 5.0, check_research)
        if not done:
            return {"ok": True, "timed_out": True, "note": "research fetch incomplete"}
        # DeepSeek headless synthesis on Ubuntu
        ctx_path = Path.home() / "homelab/data/logs/overnight-research-context.json"
        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text(json.dumps(data, indent=2)[:120000], encoding="utf-8")
        remain = max(30, int(deadline - time.time()))
        rc, out = ssh_ubuntu(
            f"bash {DSH_SCRIPT} research {remain} ~/homelab/data/logs/overnight-research.md {ctx_path}",
            timeout=remain + 30,
        )
        return {
            "ok": rc in (0, 124),
            "research_count": len(data.get("results") or []),
            "dsh_rc": rc,
            "dsh_out_tail": out[-500:],
            "timed_out": rc == 124,
        }

    if stype in ("log_review", "dsh_log_review"):
        remain = max(30, int(deadline - time.time()))
        in_path = Path.home() / "homelab/data/logs/overnight-log-input.json"
        rc, out = ssh_ubuntu(
            f"bash {DSH_SCRIPT} logs {remain} ~/homelab/data/logs/overnight-log-review.md {in_path}",
            timeout=remain + 30,
        )
        return {"ok": rc in (0, 124), "dsh_rc": rc, "dsh_out_tail": out[-500:], "timed_out": rc == 124}

    if stype == "queue_drain":
        completed_count = 0
        last_log = 0.0

        def check_queue():
            nonlocal completed_count, last_log
            try:
                jobs_data = hub_json("GET", "/api/jobs?limit=50")
                all_jobs = jobs_data if isinstance(jobs_data, list) else jobs_data.get("jobs") or []
            except Exception:
                return None
            active = [
                j
                for j in all_jobs
                if j.get("status") in ("queued", "running", "claimed")
            ]
            done_jobs = [j for j in all_jobs if j.get("status") in ("done", "error")]
            completed_count = len(done_jobs)
            now = time.time()
            if now - last_log >= 60:
                log(f"  drain poll: {len(active)} active, {completed_count} done/error")
                last_log = now
            if not active:
                return {"active": 0, "completed": completed_count}
            return None

        done, result = poll_until(deadline, 15.0, check_queue)
        if done and result:
            result["ok"] = True
            result["timed_out"] = False
            return result
        return {"ok": True, "timed_out": True, "active": "unknown", "completed": completed_count}

    return {"ok": True}


def run_step(step: dict, dry_run: bool) -> dict:
    label = step.get("label") or step.get("type")
    timeout_s = step_timeout_min(step) * 60
    log(f"{'[dry-run] ' if dry_run else ''}{label} (≤{step_timeout_min(step)}m, advance when done or timeout)")
    if dry_run:
        return {"ok": True, "dry_run": True, "timeout_min": step_timeout_min(step)}

    deadline = time.time() + timeout_s
    t0 = time.time()
    result_holder: dict[str, Any] = {}
    error_holder: list[Exception] = []

    def worker() -> None:
        try:
            result_holder["result"] = run_step_body(step)
        except Exception as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=max(1.0, deadline - time.time()))

    if error_holder:
        raise error_holder[0]
    if thread.is_alive():
        # Body still running — poll path for long steps or mark timeout
        stype = step.get("type") or ""
        if stype in ("dsh_research", "research_synthesis", "dsh_log_review", "log_review", "queue_drain"):
            poll_result = run_step_poll(step, deadline)
            elapsed = round(time.time() - t0, 1)
            poll_result["elapsed_s"] = elapsed
            poll_result["advance_reason"] = "timeout" if poll_result.get("timed_out") else "complete"
            return poll_result
        log(f"Step exceeded timeout ({timeout_s}s) — advancing")
        return {"ok": True, "timed_out": True, "elapsed_s": round(time.time() - t0, 1)}

    elapsed = round(time.time() - t0, 1)
    base = result_holder.get("result") or {"ok": True}
    stype = step.get("type") or ""
    if stype in ("dsh_research", "research_synthesis", "dsh_log_review", "log_review", "queue_drain"):
        poll_result = run_step_poll(step, deadline)
        poll_result["elapsed_s"] = elapsed
        poll_result["advance_reason"] = "timeout" if poll_result.get("timed_out") else "complete"
        return poll_result
    base["elapsed_s"] = elapsed
    base["advance_reason"] = "complete"
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    schedule = load_schedule()
    steps = [s for s in schedule.get("steps") or [] if s.get("enabled", True)]
    run_id = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    write_state(
        {
            "run_id": run_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "current_step": None,
            "steps_total": len(steps),
            "steps_done": 0,
            "advance_mode": schedule.get("advance_mode", "complete_or_timeout"),
        }
    )
    log(f"Run {run_id}: {len(steps)} steps (mode={schedule.get('advance_mode')})")
    for i, step in enumerate(steps):
        write_state(
            {
                "run_id": run_id,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "current_step": step.get("id"),
                "current_label": step.get("label"),
                "steps_total": len(steps),
                "steps_done": i,
                "advance_mode": schedule.get("advance_mode", "complete_or_timeout"),
            }
        )
        t0 = time.time()
        entry = {
            "run_id": run_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "step_id": step.get("id"),
            "type": step.get("type"),
            "label": step.get("label"),
            "timeout_min": step_timeout_min(step),
        }
        try:
            result = run_step(step, args.dry_run)
            entry["ok"] = True
            entry["result"] = result
            entry["duration_s"] = round(time.time() - t0, 1)
            entry["advance_reason"] = result.get("advance_reason", "complete")
            append_run(entry)
            log(f"OK {step.get('label')} ({entry['duration_s']}s, {entry['advance_reason']})")
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            entry["duration_s"] = round(time.time() - t0, 1)
            append_run(entry)
            log(f"FAIL {step.get('label')}: {exc}")
            write_state(
                {
                    "run_id": run_id,
                    "status": "failed",
                    "error": str(exc),
                    "steps_done": i,
                    "steps_total": len(steps),
                }
            )
            if step.get("critical"):
                return 1
    write_state(
        {
            "run_id": run_id,
            "status": "done",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "steps_done": len(steps),
            "steps_total": len(steps),
        }
    )
    log(f"Done run {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
