"""Discover netllm agents and inference servers on the LAN."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Any

import httpx
from netllm_core.models import NetllmConfig

logger = logging.getLogger(__name__)

DEFAULT_AGENT_PORT = 11400

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_url(url: str) -> bool:
    """True when the URL host is loopback (unreachable from other LAN hosts)."""
    from urllib.parse import urlparse

    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_lan_reachable_agent_url(url: str) -> bool:
    """True when peer listen_url is usable from another host on the LAN."""
    return bool(url) and not is_loopback_url(url)


def own_agent_urls(listen: str) -> set[str]:
    """Normalized agent URLs that refer to this host (for self-peer filtering)."""
    urls: set[str] = set()
    primary = agent_url_from_listen(listen).rstrip("/")
    urls.add(primary)
    if listen.startswith("http"):
        return urls
    port = listen.rpartition(":")[2] if ":" in listen else str(DEFAULT_AGENT_PORT)
    port = port or str(DEFAULT_AGENT_PORT)
    urls.add(f"http://127.0.0.1:{port}")
    lan = local_lan_ip()
    if lan:
        urls.add(f"http://{lan}:{port}")
    return urls


def filter_own_peer_urls(peers: list[str], listen: str) -> tuple[list[str], list[str]]:
    """Drop swarm.peers entries that point at this agent. Returns kept, rejected."""
    own = own_agent_urls(listen)
    kept: list[str] = []
    rejected: list[str] = []
    for peer in peers:
        norm = peer.rstrip("/")
        if norm in own:
            rejected.append(norm)
            continue
        kept.append(peer)
    return kept, rejected


def local_lan_ip() -> str | None:
    """Best-effort primary IPv4 address for this host on the LAN."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def agent_url_from_listen(listen: str, *, lan_ip: str | None = None) -> str:
    """Turn agent.listen into a URL clients on the LAN can use."""
    if listen.startswith("http"):
        return listen.rstrip("/")
    host, _, port = listen.partition(":")
    port = port or str(DEFAULT_AGENT_PORT)
    if not host or host in ("0.0.0.0", ""):
        host = lan_ip or local_lan_ip() or "127.0.0.1"
    return f"http://{host}:{port}"


def default_subnet_cidrs() -> list[str]:
    """Infer /24 CIDRs from local interfaces (typical home LAN)."""
    ip = local_lan_ip()
    if not ip:
        return []
    try:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        return [str(net)]
    except ValueError:
        return []


def _auth_headers(token: str) -> dict[str, str]:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def fetch_agent_status(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    cluster_token: str = "",
) -> dict[str, Any] | None:
    url = base_url.rstrip("/") + "/netllm/v1/status"
    try:
        resp = await client.get(url, headers=_auth_headers(cluster_token), timeout=3.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Probe URL is what LAN clients must use (status may report loopback).
        data["listen_url"] = base_url.rstrip("/")
        return data
    except Exception as exc:
        logger.debug("agent status failed %s: %s", base_url, exc)
        return None


async def probe_agent_port(
    host: str,
    port: int,
    client: httpx.AsyncClient,
    *,
    cluster_token: str = "",
) -> dict[str, Any] | None:
    base = f"http://{host}:{port}"
    try:
        health = await client.get(f"{base}/health", timeout=1.5)
        if health.status_code != 200:
            return None
    except Exception:
        return None
    return await fetch_agent_status(base, client, cluster_token=cluster_token)


async def subnet_scan_agents(
    cidrs: list[str],
    *,
    port: int = DEFAULT_AGENT_PORT,
    cluster_token: str = "",
    concurrency: int = 64,
) -> list[dict[str, Any]]:
    """Probe netllm agent port across CIDR ranges."""
    hosts: set[str] = set()
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            logger.debug("skip invalid cidr: %s", cidr)
            continue
        for host in network.hosts():
            hosts.add(str(host))

    found: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:

        async def check(host: str) -> None:
            async with sem:
                status = await probe_agent_port(
                    host, port, client, cluster_token=cluster_token
                )
                if status:
                    found.append(status)

        await asyncio.gather(*(check(h) for h in hosts))

    return found


def browse_mdns_peers(timeout_s: float = 3.0) -> list[dict[str, str]]:
    """Synchronously browse for netllm agents via mDNS."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError as exc:
        raise RuntimeError(
            "LAN mDNS browse requires zeroconf — reinstall netllm (uv sync)"
        ) from exc

    peers: dict[str, dict[str, str]] = {}

    class Listener:
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if not info:
                return
            props = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in (info.properties or {}).items()
            }
            url = props.get("listen_url", "")
            if not url and info.addresses:
                addr = socket.inet_ntoa(info.addresses[0])
                url = f"http://{addr}:{info.port or DEFAULT_AGENT_PORT}"
            agent_id = props.get("agent_id", name)
            if url:
                peers[agent_id] = {**props, "listen_url": url, "source": "mdns"}

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

    from netllm_discovery.mdns import SERVICE_TYPE

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICE_TYPE, Listener())
        time.sleep(timeout_s)
    finally:
        zc.close()

    return list(peers.values())


async def discover_lan_agents(
    config: NetllmConfig | None = None,
    *,
    use_mdns: bool = True,
    use_subnet: bool | None = None,
    timeout_s: float = 3.0,
) -> list[dict[str, Any]]:
    """
    Find netllm agents on the LAN via mDNS, config peers, and optional subnet scan.

    Returns enriched status payloads (same shape as /netllm/v1/status).
    """
    cfg = config or NetllmConfig()
    token = cfg.swarm.cluster_token
    by_url: dict[str, dict[str, Any]] = {}

    if use_mdns and cfg.swarm.mdns:
        try:
            for props in browse_mdns_peers(timeout_s):
                url = props.get("listen_url", "").rstrip("/")
                if url:
                    by_url[url] = {
                        "listen_url": url,
                        "agent_id": props.get("agent_id", ""),
                        "role": props.get("role", "peer"),
                        "source": "mdns",
                        "_props": props,
                    }
        except RuntimeError as exc:
            logger.info("mDNS browse skipped: %s", exc)

    for url in cfg.swarm.peers:
        by_url[url.rstrip("/")] = {
            "listen_url": url.rstrip("/"),
            "source": "config",
        }

    do_subnet = use_subnet if use_subnet is not None else cfg.swarm.subnet_scan
    cidrs = cfg.swarm.subnet_cidrs or default_subnet_cidrs()
    if do_subnet and cidrs:
        for status in await subnet_scan_agents(cidrs, cluster_token=token):
            url = status.get("listen_url", "").rstrip("/")
            if url:
                status["source"] = "subnet"
                by_url[url] = status

    async with httpx.AsyncClient() as client:
        enriched: list[dict[str, Any]] = []
        for entry in by_url.values():
            url = entry.get("listen_url", "")
            if entry.get("backends") is not None and entry.get("agent_id"):
                entry.setdefault("source", entry.get("source", "scan"))
                enriched.append(entry)
                continue
            status = await fetch_agent_status(url, client, cluster_token=token)
            if status:
                status["source"] = entry.get("source", "scan")
                enriched.append(status)

    local_id = cfg.agent.agent_id
    enriched = [p for p in enriched if p.get("agent_id", "") != local_id]
    enriched.sort(key=lambda p: (p.get("hostname", ""), p.get("agent_id", "")))
    return enriched


def models_from_status(status: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten backends from an agent status payload into model rows."""
    rows: list[dict[str, str]] = []
    agent_id = status.get("agent_id", "")
    hostname = status.get("hostname", "")
    listen = status.get("listen_url", "")
    for backend in status.get("backends") or []:
        provider = backend.get("provider", "?")
        base_url = backend.get("base_url", "")
        scope = "local" if backend.get("local") else "remote"
        host_label = hostname or agent_id or listen
        for model_id in backend.get("health", {}).get("models") or []:
            rows.append(
                {
                    "model": model_id,
                    "provider": provider,
                    "backend": base_url,
                    "scope": scope,
                    "host": host_label,
                    "agent_id": agent_id,
                }
            )
    return rows
