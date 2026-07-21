"""Runtime helpers: port preflight, netllm process detection, graceful replace."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass

import httpx
from netllm_core.models import NetllmConfig

from netllm_discovery.mdns import parse_listen_host_port
from netllm_discovery.process_util import port_owner_pid, terminate_pid

logger = logging.getLogger(__name__)


@dataclass
class PortConflict:
    port: int
    pid: int | None
    url: str
    occupied_by_netllm: bool
    agent_id: str | None = None
    hostname: str | None = None


def is_port_in_use(host: str, port: int) -> bool:
    """Return True if something is accepting connections on the port."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "127.0.0.1") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe_host, port)) == 0


def probe_netllm_agent(url: str, *, timeout_s: float = 2.0) -> dict | None:
    """Return status payload if url is a healthy netllm agent."""
    base = url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout_s, verify=False) as client:
            health = client.get(f"{base}/health")
            if health.status_code != 200:
                return None
            status = client.get(f"{base}/netllm/v1/status")
            if status.status_code == 200:
                return status.json()
            # Degraded agent: /health OK but /status fails (e.g. bundled SSL scan).
            return {"agent_id": None, "hostname": None, "degraded": True}
    except Exception as exc:
        logger.debug("probe_netllm_agent %s: %s", url, exc)
        return None


def check_listen_port(config: NetllmConfig) -> PortConflict | None:
    """Return conflict details when the configured listen port is occupied."""
    host, port = parse_listen_host_port(config.agent.listen)
    if not is_port_in_use(host, port):
        return None

    client_url = f"http://127.0.0.1:{port}"
    status = probe_netllm_agent(client_url)
    pid = port_owner_pid(port)
    return PortConflict(
        port=port,
        pid=pid,
        url=client_url,
        occupied_by_netllm=status is not None,
        agent_id=status.get("agent_id") if status else None,
        hostname=status.get("hostname") if status else None,
    )


def format_port_conflict_message(conflict: PortConflict) -> str:
    lines = [f"Port {conflict.port} is already in use."]
    if conflict.occupied_by_netllm:
        lines.append("Another netllm agent is listening on this port.")
        if conflict.agent_id:
            lines.append(f"  agent_id: {conflict.agent_id}")
        if conflict.hostname:
            lines.append(f"  hostname: {conflict.hostname}")
    if conflict.pid:
        lines.append(f"  pid: {conflict.pid}")
    lines.append(f"  url: {conflict.url}")
    return "\n".join(lines)


def port_conflict_hints(conflict: PortConflict, *, replace_flag: str) -> list[str]:
    hints = [
        f"Check status: curl -sf {conflict.url}/health",
        f"Restart and replace: {replace_flag}",
    ]
    if conflict.pid:
        hints.append(f"Stop manually: kill {conflict.pid}")
    if not conflict.occupied_by_netllm:
        hints.insert(0, "Another process owns this port — pick a different --port")
    return hints


def _health_responds(url: str, *, timeout_s: float = 2.0) -> bool:
    try:
        with httpx.Client(timeout=timeout_s, verify=False) as client:
            return client.get(f"{url.rstrip('/')}/health").status_code == 200
    except Exception:
        return False


def stop_netllm_on_port(port: int, *, wait_s: float = 5.0) -> bool:
    """Stop the netllm agent on port and wait for the PROCESS to exit.

    Waiting only for the port to free is not enough: a SIGTERM'd uvicorn
    releases its listener immediately but keeps running until in-flight
    LLM requests drain — minutes, sometimes never. That half-dead
    instance keeps its mDNS registration and gossip loop alive, so the
    replacement hits an mDNS name collision and starts with LAN
    advertising permanently disabled (observed in the field). Escalate
    to SIGKILL when the process outlives the grace window.
    """
    from netllm_discovery.process_util import force_kill_pid, pid_alive

    url = f"http://127.0.0.1:{port}"
    if probe_netllm_agent(url) is None and not _health_responds(url):
        return False
    pid = port_owner_pid(port)
    if pid is None:
        return False
    if not terminate_pid(pid):
        return False

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not pid_alive(pid) and not is_port_in_use("127.0.0.1", port):
            return True
        time.sleep(0.2)
    if pid_alive(pid):
        force_kill_pid(pid)
        kill_deadline = time.monotonic() + 3.0
        while time.monotonic() < kill_deadline:
            if not pid_alive(pid):
                break
            time.sleep(0.1)
    return not is_port_in_use("127.0.0.1", port)
