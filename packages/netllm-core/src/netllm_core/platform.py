"""Cross-platform paths and defaults."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def is_darwin() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def default_hostname() -> str:
    return socket.gethostname()


_local_admin_hosts: frozenset[str] | None = None


def local_admin_client_hosts() -> frozenset[str]:
    """Hosts that may call loopback-gated admin routes from this machine."""
    global _local_admin_hosts
    if _local_admin_hosts is not None:
        return _local_admin_hosts
    hosts: set[str] = {"127.0.0.1", "::1", "localhost", "testclient"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if isinstance(addr, str):
                hosts.add(addr.lower())
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            hosts.add(sock.getsockname()[0].lower())
    except OSError:
        pass
    _local_admin_hosts = frozenset(hosts)
    return _local_admin_hosts


def _host_only(netloc: str) -> str:
    """Hostname from a ``Host`` header or URL netloc (port and brackets off)."""
    from urllib.parse import urlsplit

    try:
        return (urlsplit(f"//{netloc.strip()}").hostname or "").lower()
    except ValueError:
        return ""


def _is_ip_literal(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def local_host_names() -> frozenset[str]:
    """DNS names that legitimately address this machine in a ``Host`` header."""
    names = {"localhost"}
    try:
        hostname = socket.gethostname().lower()
    except OSError:
        hostname = ""
    if hostname:
        names.update({hostname, f"{hostname}.local", hostname.split(".")[0]})
        names.add(f"{hostname.split('.')[0]}.local")
    return frozenset(names)


def is_browser_safe_local_request(
    *,
    client_host: str,
    host_header: str,
    origin: str | None,
    sec_fetch_site: str | None,
) -> bool:
    """Whether a request from a local IP may be trusted as "this machine".

    A local source address proves only that the *browser* is local, not that
    the page driving it is ours. Two attacks ride on that gap:

    - **Cross-site request forgery** — any page can POST to
      ``http://127.0.0.1:11400``. Browsers attach ``Origin`` to such requests,
      and it will not match our ``Host``.
    - **DNS rebinding** — a page rebinds its own domain to 127.0.0.1 so its
      requests are same-origin. ``Origin`` then matches ``Host``, but ``Host``
      is the attacker's domain rather than an IP literal or one of our names.

    Non-browser clients (CLI, menubar app, harnesses, peers) send neither
    header and address the agent by IP or a local name, so they are unaffected.
    """
    host = _host_only(host_header)
    if not host:
        return False
    # Starlette's TestClient: never the peer address of a real socket.
    in_process_test = client_host == "testclient" and host == "testserver"
    if not (in_process_test or _is_ip_literal(host) or host in local_host_names()):
        return False
    if (sec_fetch_site or "").lower() in ("cross-site", "same-site"):
        return False
    if origin is not None:
        from urllib.parse import urlsplit

        try:
            origin_netloc = urlsplit(origin.strip()).netloc.lower()
        except ValueError:
            return False
        if not origin_netloc or origin_netloc != host_header.strip().lower():
            return False
    return True


def default_log_dir() -> Path:
    if is_darwin():
        return Path.home() / "Library" / "Application Support" / "netllm" / "logs"
    if is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "netllm" / "logs"
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "netllm" / "logs"
    return Path.home() / ".local" / "state" / "netllm" / "logs"


def default_discovery_providers() -> list[str]:
    """Providers enabled by default on this platform.

    Derived from each spec's `platforms` (PROGRAM.md Axis B). The darwin
    branch this replaced existed solely because oMLX is Apple-Silicon only;
    that fact now lives on the oMLX spec, so a provider with different
    platform support costs a registry field rather than a branch here.
    """
    from netllm_core.local_providers import providers_for_platform

    return providers_for_platform(sys.platform)
