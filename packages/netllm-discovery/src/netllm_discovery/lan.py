"""Discover netllm agents and inference servers on the LAN."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Iterable
from typing import Any

import httpx
from netllm_core.models import NetllmConfig

logger = logging.getLogger(__name__)

DEFAULT_AGENT_PORT = 11400

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Wall clock of the last completed LAN agent scan (UI-3). Stamped by the scan
# itself, not by the route handler that happened to trigger it: the subnet
# scan also runs from startup, the mDNS fallback and the rediscovery loop, and
# a UI that read the handler's clock would report "last scan 2m ago" for a
# scan that ran ten seconds ago from a timer.
_last_peer_scan_at: float = 0.0


def last_peer_scan_at() -> float:
    """Epoch seconds of the last completed LAN agent scan (0.0 = never)."""
    return _last_peer_scan_at


def _stamp_peer_scan() -> float:
    global _last_peer_scan_at
    _last_peer_scan_at = time.time()
    return _last_peer_scan_at


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


# How an address on THIS host relates to whoever is trying to reach us, in
# descending order of usefulness. A wildcard bind answers on every local
# IPv4, and a flat list of them buried the one address a LAN peer can dial
# under five Docker bridge gateways.
#
# The test is the INTERFACE, not the address. 10.0.0.29 and 172.17.0.1 are
# both RFC1918; only the interface says which one is a container bridge.
# Ordering — a LAN address works for any peer; a VPN address works for peers
# on that VPN; a container-bridge address works only from a container on this
# host (real, and what the Integrations page's "Docker" location needs on
# Linux); link-local is a DHCP failure; loopback resolves to the *reader's*
# machine and is never put on the wire.
ADDRESS_KINDS = ("lan", "vpn", "container", "link_local", "loopback")

_ADDRESS_KIND_RANK = {kind: index for index, kind in enumerate(ADDRESS_KINDS)}
_UNKNOWN_KIND_RANK = len(ADDRESS_KINDS)

# Container/VM bridge gateways. `br-<hash>` is what Docker names a compose
# network; plain `br0`/`bridge0` is deliberately absent, because a Linux host
# that bridges its own NIC (libvirt, Proxmox) carries its real LAN address
# there and would be misfiled as noise.
_CONTAINER_IFACE_PREFIXES = (
    "docker",
    "br-",
    "podman",
    "cni",
    "cbr",
    "lxcbr",
    "lxdbr",
    "virbr",
    "vmnet",
    "vboxnet",
    "veth",
    "flannel",
    "weave",
    "kube",
    "antrea",
    "cali",
)

# Tunnels. Reachable by a peer that is on the same tunnel, so these rank
# above container bridges and below the LAN proper.
_VPN_IFACE_PREFIXES = ("tun", "tap", "utun", "wg", "ppp", "tailscale", "ipsec", "zt")


def classify_interface_address(interface: str, ip: str) -> str:
    """Which of ``ADDRESS_KINDS`` an IPv4 on ``interface`` is.

    Loopback and link-local are properties of the address itself (127/8,
    169.254/16 are definitional). Everything else is decided by the interface
    name, because no address range distinguishes a Docker bridge gateway from
    the LAN — both are RFC1918. An unrecognised interface is ``"lan"``: the
    failure mode of showing a real address is far cheaper than hiding one.
    """
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None = (
            ipaddress.ip_address(ip)
        )
    except ValueError:
        addr = None
    if addr is not None:
        if addr.is_loopback:
            return "loopback"
        if addr.is_link_local:
            return "link_local"
    name = str(interface or "").lower()
    if name == "lo" or name.startswith("lo0"):
        return "loopback"
    if any(name.startswith(prefix) for prefix in _VPN_IFACE_PREFIXES):
        return "vpn"
    if any(name.startswith(prefix) for prefix in _CONTAINER_IFACE_PREFIXES):
        return "container"
    return "lan"


def address_kind_rank(kind: str) -> int:
    """Sort position for a kind; an unknown kind sorts last, never crashes."""
    return _ADDRESS_KIND_RANK.get(str(kind or ""), _UNKNOWN_KIND_RANK)


def local_ipv4_interfaces() -> list[tuple[str, str]]:
    """``[(interface, ipv4)]`` for this host — ``[]`` when unavailable.

    Interface enumeration is a nicety (it needs ``psutil``); a platform that
    refuses it leaves the caller with whatever it already knew.
    """
    rows: list[tuple[str, str]] = []
    try:
        import psutil

        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                ip = str(addr.address or "").strip()
                if ip:
                    rows.append((str(name), ip))
    except Exception:
        logger.debug("interface enumeration unavailable", exc_info=True)
        return []
    return rows


def classified_agent_endpoints(
    port: int | str,
    *,
    interfaces: Iterable[tuple[str, str]] | None = None,
    primary_url: str = "",
) -> list[dict[str, str]]:
    """Every local address this agent answers on, classified and ordered.

    Returns ``[{"url", "kind", "interface"}]`` — ``primary_url`` first (it is
    what peers were told to dial, whatever kind it turns out to be), then by
    ``ADDRESS_KINDS`` order, then by URL so the list is stable across polls.

    Classifying here rather than in each client is the point: the dashboard,
    the macOS app and the CLI would otherwise each have to re-derive "is
    172.x a bridge?", which is exactly the mirror drift the registry rules
    exist to prevent — and none of them can, since only this host can see its
    own interface names.
    """
    rows = list(interfaces) if interfaces is not None else local_ipv4_interfaces()
    primary = str(primary_url or "").rstrip("/")
    best: dict[str, dict[str, str]] = {}
    for name, raw_ip in rows:
        ip = str(raw_ip or "").strip()
        if not ip:
            continue
        host = f"[{ip}]" if ":" in ip else ip
        url = f"http://{host}:{port}"
        kind = classify_interface_address(str(name), ip)
        prior = best.get(url)
        # One address can appear on several interfaces (an alias, a bond).
        # Keep the most useful reading of it.
        if prior is None or address_kind_rank(kind) < address_kind_rank(prior["kind"]):
            best[url] = {"url": url, "kind": kind, "interface": str(name or "")}
    if primary and primary not in best:
        best[primary] = {"url": primary, "kind": "lan", "interface": ""}
    return sorted(
        best.values(),
        key=lambda e: (
            0 if e["url"] == primary else 1,
            address_kind_rank(e["kind"]),
            e["url"],
        ),
    )


def own_agent_urls(listen: str) -> set[str]:
    """Normalized agent URLs that refer to this host (for self-peer filtering)."""
    urls: set[str] = set()
    primary = agent_url_from_listen(listen).rstrip("/")
    urls.add(primary)
    if listen.startswith("http"):
        return urls
    from netllm_core.models import split_listen

    port = str(split_listen(listen)[1])
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
    from netllm_core.models import split_listen

    host, port = split_listen(listen)
    if not host or host in ("0.0.0.0", "::"):
        # Wildcard bind: advertise a concrete address peers can dial.
        host = lan_ip or local_lan_ip() or "127.0.0.1"
    if ":" in host:  # bare IPv6 needs brackets inside a URL authority
        host = f"[{host}]"
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
        reported = str(data.get("listen_url", "")).rstrip("/")
        if reported:
            data["reported_listen_url"] = reported
        data["listen_url"] = base_url.rstrip("/")
        # UI-3: when *this row* was probed. Per-row rather than per-scan
        # because dedupe_agents_by_id collapses several probes into one row
        # and a scan may be minutes wide on a /16.
        data["probed_at"] = time.time()
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


def dedupe_agents_by_id(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse scan rows that belong to the same agent.

    A multi-homed host (Wi-Fi + Ethernet, DHCP drift) answers on several
    LAN IPs with the same agent_id; showing one row per probed IP looks
    like duplicate agents. Keep the row matching the agent's own reported
    listen_url and record the other URLs in ``also_reachable_at``.
    """
    by_id: dict[str, list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in found:
        agent_id = str(row.get("agent_id", ""))
        if agent_id:
            by_id.setdefault(agent_id, []).append(row)
        else:
            passthrough.append(row)

    deduped: list[dict[str, Any]] = []
    for rows in by_id.values():
        primary = next(
            (
                r
                for r in rows
                if r.get("listen_url")
                and r.get("listen_url") == r.get("reported_listen_url")
            ),
            rows[0],
        )
        extras = sorted(
            {
                str(r.get("listen_url", ""))
                for r in rows
                if r is not primary and r.get("listen_url")
            }
            - {str(primary.get("listen_url", ""))}
        )
        if extras:
            primary["also_reachable_at"] = extras
        deduped.append(primary)
    deduped.extend(passthrough)
    deduped.sort(key=lambda p: (p.get("hostname", ""), p.get("agent_id", "")))
    return deduped


async def subnet_scan_agents(
    cidrs: list[str],
    *,
    port: int = DEFAULT_AGENT_PORT,
    cluster_token: str = "",
    concurrency: int = 64,
) -> list[dict[str, Any]]:
    """Probe netllm agent port across CIDR ranges (one row per agent_id)."""
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

    _stamp_peer_scan()
    return dedupe_agents_by_id(found)


def browse_mdns_peers(timeout_s: float = 3.0) -> list[dict[str, str]]:
    """Synchronously browse for netllm agents via mDNS."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError as exc:
        raise RuntimeError(
            "LAN mDNS browse requires zeroconf — reinstall netllm (uv sync)"
        ) from exc

    peers: dict[str, dict[str, str]] = {}

    from netllm_discovery.mdns import decode_service_info

    class Listener:
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if not info:
                return
            url, props = decode_service_info(info, default_port=DEFAULT_AGENT_PORT)
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
                        "unreachable": props.get("reachable") == "false",
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
            if entry.get("unreachable"):
                # Loopback-bound peer: its advertised URL is not routable
                # from this host (fetching it would hit our own agent).
                # Keep the row so callers can explain the rebind fix.
                enriched.append(entry)
                continue
            if entry.get("backends") is not None and entry.get("agent_id"):
                entry.setdefault("source", entry.get("source", "scan"))
                enriched.append(entry)
                continue
            status = await fetch_agent_status(url, client, cluster_token=token)
            if status:
                status["source"] = entry.get("source", "scan")
                enriched.append(status)

    _stamp_peer_scan()
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
