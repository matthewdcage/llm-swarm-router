"""Machines wake (WoL) / sleep (SSH) helpers for Bucket-HUB."""
from __future__ import annotations

import json
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

MACHINES_FILE = Path(
    # Overridden by app via set_paths
)
STATE_FILE = Path()


def set_paths(machines_file: Path, state_file: Path) -> None:
    global MACHINES_FILE, STATE_FILE
    MACHINES_FILE = machines_file
    STATE_FILE = state_file


def default_machines() -> dict[str, Any]:
    return {
        "broadcast": "192.168.50.255",
        "machines": [
            {
                "id": "pi",
                "label": "Pi",
                "lan_ip": "192.168.50.138",
                "mac": "d8:3a:dd:d1:27:aa",
                "probe_url": "http://192.168.50.138:3000/",
                "wake": False,
                "sleep": False,
                "hub_host": True,
            },
            {
                "id": "mac",
                "label": "Mac",
                "lan_ip": "192.168.50.19",
                "mac": "f0:2f:4b:00:da:27",
                "probe_url": "http://192.168.50.19:11400/ui/",
                "wake": True,
                "sleep": True,
                "ssh": {
                    "host": "192.168.50.19",
                    "user": "finn.negrello",
                    "sleep_cmd": "pmset sleepnow",
                },
            },
            {
                "id": "ubuntu",
                "label": "Ubuntu",
                "lan_ip": "192.168.50.65",
                "mac": "2c:f0:5d:6f:3f:f1",
                "probe_url": "http://192.168.50.65:11400/ui/",
                "wake": True,
                "sleep": True,
                "ssh": {
                    "host": "192.168.50.65",
                    "user": "bucket",
                    "sleep_cmd": "dbus-send --system --print-reply --dest=org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager.Suspend boolean:true",
                },
            },
            {
                "id": "tnas",
                "label": "TNAS",
                "lan_ip": "192.168.50.186",
                "mac": "6c:bf:b5:04:69:66",
                "probe_url": "http://192.168.50.186/",
                "wake": True,
                "sleep": False,
            },
            {
                "id": "bucket",
                "label": "Bucket",
                "lan_ip": "192.168.50.222",
                "mac": "c8:7f:54:01:48:ad",
                "probe_url": "http://192.168.50.222:11400/ui/",
                "wake": True,
                "sleep": True,
                "ssh": {
                    "host": "192.168.50.222",
                    "user": "bucket",
                    "sleep_cmd": "dbus-send --system --print-reply --dest=org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager.Suspend boolean:true",
                },
            },
        ],
    }


def load_config() -> dict[str, Any]:
    if not MACHINES_FILE.exists():
        cfg = default_machines()
        MACHINES_FILE.parent.mkdir(parents=True, exist_ok=True)
        MACHINES_FILE.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        return cfg
    return json.loads(MACHINES_FILE.read_text(encoding="utf-8"))


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"machines": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"machines": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def get_machine(machine_id: str) -> dict[str, Any]:
    cfg = load_config()
    for m in cfg.get("machines") or []:
        if m.get("id") == machine_id:
            return m
    raise KeyError(machine_id)


def probe_reachable(machine: dict[str, Any], timeout: float = 2.0) -> tuple[bool, int | None]:
    """Return (reachable, latency_ms). Prefer probe_url; fall back to TCP ports then ping."""
    url = (machine.get("probe_url") or "").strip()
    start = time.monotonic()
    if url:
        try:
            req = Request(url, method="GET", headers={"User-Agent": "bucket-hub-machines/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                resp.read(64)
            ms = int((time.monotonic() - start) * 1000)
            return True, ms
        except Exception:
            pass
    ip = machine.get("lan_ip")
    if not ip:
        return False, None
    for port in (22, 80, 443, 11400, 3000, 445, 548):
        try:
            t0 = time.monotonic()
            with socket.create_connection((ip, port), timeout=timeout):
                ms = int((time.monotonic() - t0) * 1000)
                return True, ms
        except OSError:
            continue
    try:
        t0 = time.monotonic()
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip],
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
        if proc.returncode == 0:
            return True, int((time.monotonic() - t0) * 1000)
    except Exception:
        pass
    return False, None


def probe_reachable_quick(
    machine: dict[str, Any], timeout: float = 0.7
) -> tuple[bool, int | None]:
    """Fast reachability for status lists: HTTP probe_url, else one TCP port (22)."""
    url = (machine.get("probe_url") or "").strip()
    start = time.monotonic()
    if url:
        try:
            req = Request(url, method="GET", headers={"User-Agent": "bucket-hub-machines/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                resp.read(64)
            ms = int((time.monotonic() - start) * 1000)
            return True, ms
        except Exception:
            pass
    ip = machine.get("lan_ip")
    if ip:
        try:
            t0 = time.monotonic()
            with socket.create_connection((ip, 22), timeout=timeout):
                ms = int((time.monotonic() - t0) * 1000)
                return True, ms
        except OSError:
            pass
    return False, None


def send_wol(mac: str, broadcast: str = "192.168.50.255", port: int = 9) -> None:
    mac_clean = mac.replace(":", "").replace("-", "").lower()
    if len(mac_clean) != 12 or any(c not in "0123456789abcdef" for c in mac_clean):
        raise ValueError(f"Invalid MAC address: {mac}")
    payload = bytes.fromhex("ff" * 6 + mac_clean * 16)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, (broadcast, port))
        sock.sendto(payload, (broadcast, 7))
    finally:
        sock.close()


def ssh_sleep(machine: dict[str, Any], identity: str | None = None) -> None:
    ssh_cfg = machine.get("ssh") or {}
    host = ssh_cfg.get("host") or machine.get("lan_ip")
    user = ssh_cfg.get("user")
    cmd = ssh_cfg.get("sleep_cmd") or "systemctl suspend"
    if not host or not user:
        raise RuntimeError("SSH host/user not configured for this machine")
    identity = identity or ssh_cfg.get("identity") or ""
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if identity:
        args.extend(["-i", identity])
    args.append(f"{user}@{host}")
    args.append(cmd)
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    # suspend often closes the connection with non-zero; treat common cases as ok
    if proc.returncode == 0:
        return
    err = (proc.stderr or proc.stdout or "").strip()
    if "Connection closed" in err or "closed by remote host" in err.lower():
        return
    if proc.returncode in (255,) and not err:
        return
    raise RuntimeError(err or f"ssh exit {proc.returncode}")


def _machine_status_row(
    m: dict[str, Any],
    state: dict[str, Any],
    reachable: bool,
    latency_ms: int | None,
) -> dict[str, Any]:
    mid = m["id"]
    st = (state.get("machines") or {}).get(mid) or {}
    can_wake = bool(m.get("wake") and m.get("mac") and not m.get("hub_host"))
    can_sleep = bool(
        m.get("sleep") and (m.get("ssh") or {}).get("user") and not m.get("hub_host")
    )
    return {
        "id": mid,
        "label": m.get("label") or mid,
        "lan_ip": m.get("lan_ip"),
        "hub_host": bool(m.get("hub_host")),
        "reachable": reachable,
        "latency_ms": latency_ms,
        "power_on": reachable,
        "can_wake": can_wake,
        "can_sleep": can_sleep,
        "last_error": st.get("last_error"),
        "last_action": st.get("last_action"),
        "last_action_at": st.get("last_action_at"),
    }


def list_machines_status(identity: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    state = load_state()
    machines: list[dict[str, Any]] = list(cfg.get("machines") or [])
    rows: list[dict[str, Any] | None] = [None] * len(machines)
    if not machines:
        return {
            "machines": [],
            "ssh_identity_configured": bool(identity),
            "broadcast": cfg.get("broadcast") or "192.168.50.255",
        }
    workers = min(8, len(machines))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(probe_reachable_quick, m, 0.7): i for i, m in enumerate(machines)
        }
        for fut in as_completed(futures):
            i = futures[fut]
            reachable, latency_ms = fut.result()
            rows[i] = _machine_status_row(machines[i], state, reachable, latency_ms)
    return {
        "machines": [r for r in rows if r is not None],
        "ssh_identity_configured": bool(identity),
        "broadcast": cfg.get("broadcast") or "192.168.50.255",
    }


def record_action(machine_id: str, action: str, ok: bool, error: str | None = None) -> None:
    from datetime import datetime, timezone

    state = load_state()
    state.setdefault("machines", {})
    state["machines"][machine_id] = {
        "last_action": action,
        "last_action_at": datetime.now(timezone.utc).isoformat(),
        "last_error": None if ok else (error or "failed"),
    }
    save_state(state)


def wake_machine(machine_id: str, broadcast: str | None = None) -> dict[str, Any]:
    m = get_machine(machine_id)
    if m.get("hub_host") or not m.get("wake"):
        raise RuntimeError("Wake not enabled for this machine")
    mac = m.get("mac")
    if not mac:
        raise RuntimeError("MAC address not configured")
    cfg = load_config()
    bcast = broadcast or cfg.get("broadcast") or "192.168.50.255"
    send_wol(mac, bcast)
    # brief poll
    reachable = False
    latency = None
    for _ in range(8):
        time.sleep(1.5)
        reachable, latency = probe_reachable(m, timeout=1.5)
        if reachable:
            break
    record_action(machine_id, "wake", True, None)
    return {"ok": True, "reachable": reachable, "latency_ms": latency}


def sleep_machine(machine_id: str, identity: str | None = None) -> dict[str, Any]:
    m = get_machine(machine_id)
    if m.get("hub_host") or not m.get("sleep"):
        raise RuntimeError("Sleep not enabled for this machine")
    try:
        ssh_sleep(m, identity=identity)
    except Exception as exc:
        record_action(machine_id, "sleep", False, str(exc))
        raise
    # poll until unreachable (best effort)
    for _ in range(10):
        time.sleep(1.0)
        reachable, _ = probe_reachable(m, timeout=1.0)
        if not reachable:
            break
    record_action(machine_id, "sleep", True, None)
    reachable, latency = probe_reachable(m, timeout=1.0)
    return {"ok": True, "reachable": reachable, "latency_ms": latency}
