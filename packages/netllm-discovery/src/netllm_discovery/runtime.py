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
        with httpx.Client(timeout=timeout_s) as client:
            health = client.get(f"{base}/health")
            if health.status_code != 200:
                return None
            status = client.get(f"{base}/netllm/v1/status")
            if status.status_code != 200:
                return None
            return status.json()
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


def stop_netllm_on_port(port: int, *, wait_s: float = 5.0) -> bool:
    """SIGTERM the process on port if it is a netllm agent; wait until port is free."""
    url = f"http://127.0.0.1:{port}"
    if probe_netllm_agent(url) is None:
        return False
    pid = port_owner_pid(port)
    if pid is None:
        return False
    if not terminate_pid(pid):
        return False

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not is_port_in_use("127.0.0.1", port):
            return True
        time.sleep(0.2)
    return not is_port_in_use("127.0.0.1", port)
