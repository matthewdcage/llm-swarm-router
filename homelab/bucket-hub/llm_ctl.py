"""On-demand LLM (vLLM on Ubuntu ai-server) control via SSH."""
from __future__ import annotations

import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import machines_ctl

LLM_MACHINE_ID = "ubuntu"
LLM_SCRIPT = "~/homelab/scripts/ai-server-llm.sh"
LLM_PROBE_URL = "http://192.168.50.65:8000/v1/models"
LLM_MODEL_ID = "qwen2.5-coder-14b"


def _ssh_run(
    cmd: str,
    identity: str | None = None,
    timeout: float = 620.0,
) -> tuple[int, str, str]:
    machine = machines_ctl.get_machine(LLM_MACHINE_ID)
    ssh_cfg = machine.get("ssh") or {}
    host = ssh_cfg.get("host") or machine.get("lan_ip")
    user = ssh_cfg.get("user")
    if not host or not user:
        raise RuntimeError("Ubuntu SSH not configured in machines.json")
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if identity:
        args.extend(["-i", identity])
    args.append(f"{user}@{host}")
    args.append(cmd)
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _probe_vllm(timeout: float = 2.0) -> tuple[bool, str | None]:
    try:
        req = Request(LLM_PROBE_URL, headers={"User-Agent": "bucket-hub-llm/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if LLM_MODEL_ID in body:
            return True, LLM_MODEL_ID
        return False, None
    except (URLError, OSError, TimeoutError):
        return False, None


def _parse_status_line(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"state": "off", "model": None, "vram_used_mib": None, "vram_total_mib": None}
    for part in text.strip().split():
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key == "state":
            out["state"] = val
        elif key == "model" and val:
            out["model"] = val
        elif key == "vram_used_mib" and val.isdigit():
            out["vram_used_mib"] = int(val)
        elif key == "vram_total_mib" and val.isdigit():
            out["vram_total_mib"] = int(val)
    return out


def llm_status(identity: str | None = None) -> dict[str, Any]:
    """Return LLM pool state (local probe + optional SSH status)."""
    del identity
    ready, model = _probe_vllm(timeout=1.5)
    state = "ready" if ready else "off"
    vram_used = None
    vram_total = None
    if ready:
        state = "ready"
    else:
        # Container may be up but model loading
        try:
            machine = machines_ctl.get_machine(LLM_MACHINE_ID)
            ip = machine.get("lan_ip")
            if ip:
                import socket

                with socket.create_connection((ip, 8000), timeout=1.0):
                    state = "starting"
        except OSError:
            state = "off"
    return {
        "state": state,
        "model": model if ready else (LLM_MODEL_ID if state == "starting" else None),
        "vram_used_mib": vram_used,
        "vram_total_mib": vram_total,
        "probe_url": LLM_PROBE_URL,
        "chat_url": "https://chat.bucket-hub.stackrise.com.au",
    }


def llm_start(identity: str | None = None) -> dict[str, Any]:
    code, out, err = _ssh_run(f"bash {LLM_SCRIPT} start", identity=identity)
    parsed = _parse_status_line(out)
    if code != 0 and parsed.get("state") != "ready":
        raise RuntimeError((err or out or "LLM start failed").strip())
    # Poll until ready or timeout
    for _ in range(120):
        st = llm_status()
        if st["state"] == "ready":
            return {"ok": True, **st}
        if st["state"] == "off":
            break
        time.sleep(3)
    st = llm_status()
    if st["state"] != "ready":
        raise RuntimeError(err or out or "LLM did not become ready")
    return {"ok": True, **st}


def llm_stop(identity: str | None = None) -> dict[str, Any]:
    code, out, err = _ssh_run(f"bash {LLM_SCRIPT} stop", identity=identity, timeout=120.0)
    if code != 0:
        raise RuntimeError((err or out or "LLM stop failed").strip())
    parsed = _parse_status_line(out)
    return {"ok": True, "state": parsed.get("state") or "off", "model": None}
