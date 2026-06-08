"""Optional mDNS advertisement and discovery for netllm agents."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_netllm._tcp.local."
SERVICE_NAME = "netllm-agent"


def parse_listen_host_port(listen: str) -> tuple[str, int]:
    if listen.startswith("http"):
        from urllib.parse import urlparse

        parsed = urlparse(listen)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11400
        return host, port
    host, _, port_str = listen.partition(":")
    return host or "127.0.0.1", int(port_str or "11400")


def _advertise_address(listen: str) -> tuple[str, bytes]:
    """Resolve host + packed IPv4 for mDNS when binding 0.0.0.0."""
    from netllm_discovery.lan import local_lan_ip

    host, port = parse_listen_host_port(listen)
    if host in ("0.0.0.0", ""):
        lan = local_lan_ip() or "127.0.0.1"
        return lan, socket.inet_aton(lan)
    try:
        return host, socket.inet_aton(host)
    except OSError:
        lan = local_lan_ip() or "127.0.0.1"
        return lan, socket.inet_aton(lan)


class MdnsAdvertiser:
    """Advertise this agent on the local network via zeroconf (background thread)."""

    def __init__(
        self,
        listen: str,
        agent_id: str,
        role: str,
        version: str = "0.2.3",
    ) -> None:
        self.listen = listen
        self.agent_id = agent_id
        self.role = role
        self.version = version
        self._zeroconf = None
        self._info = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self, *, timeout_s: float = 10.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name="netllm-mdns-advertise",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout_s):
            raise TimeoutError("mDNS advertiser did not start in time")
        if self._error is not None:
            raise RuntimeError("mDNS advertiser failed") from self._error

    def _run(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError as exc:
            self._error = exc
            self._ready.set()
            return

        try:
            from zeroconf import NonUniqueNameException
        except ImportError:
            NonUniqueNameException = type(  # type: ignore[misc, assignment]
                "NonUniqueNameException",
                (Exception,),
                {},
            )

        try:
            host, port = parse_listen_host_port(self.listen)
            advertise_host, addr = _advertise_address(self.listen)
            listen_url = f"http://{advertise_host}:{port}"
            props = {
                "agent_id": self.agent_id,
                "role": self.role,
                "version": self.version,
                "listen_url": listen_url,
            }
            zc = Zeroconf()
            info = ServiceInfo(
                SERVICE_TYPE,
                f"{SERVICE_NAME}-{self.agent_id}.{SERVICE_TYPE}",
                addresses=[addr],
                port=port,
                properties={k: v.encode() for k, v in props.items()},
                server=f"{self.agent_id}.local.",
            )

            def _register() -> None:
                try:
                    zc.unregister_service(info)
                except Exception:
                    pass
                zc.register_service(info)

            try:
                _register()
            except NonUniqueNameException:
                logger.warning(
                    "mDNS name collision for %s — retrying after unregister",
                    self.agent_id,
                )
                try:
                    zc.unregister_service(info)
                except Exception:
                    pass
                time.sleep(0.5)
                _register()

            self._zeroconf = zc
            self._info = info
            logger.info("mDNS advertised %s on port %s", listen_url, port)
            self._ready.set()
            self._stop.wait()
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            logger.warning("mDNS advertiser error: %s", exc)
            return

        if self._zeroconf and self._info:
            try:
                self._zeroconf.unregister_service(self._info)
                self._zeroconf.close()
            except Exception as exc:
                logger.debug("mDNS advertiser shutdown: %s", exc)
        self._zeroconf = None
        self._info = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None


class MdnsBrowser:
    """Discover peer agents on the LAN (background thread)."""

    def __init__(self, on_peer: Callable[[str, dict[str, str]], None]) -> None:
        self.on_peer = on_peer
        self._zeroconf = None
        self._browser = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self, *, timeout_s: float = 10.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name="netllm-mdns-browse",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout_s):
            raise TimeoutError("mDNS browser did not start in time")
        if self._error is not None:
            raise RuntimeError("mDNS browser failed") from self._error

    def _run(self) -> None:
        try:
            from zeroconf import ServiceBrowser, Zeroconf
        except ImportError as exc:
            self._error = exc
            self._ready.set()
            return

        outer = self

        class Listener:
            def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                info = zc.get_service_info(type_, name)
                if not info:
                    return
                props = {
                    k.decode() if isinstance(k, bytes) else k: (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in (info.properties or {}).items()
                }
                url = props.get("listen_url", "")
                if not url and info.addresses:
                    addr = socket.inet_ntoa(info.addresses[0])
                    url = f"http://{addr}:{info.port or 11400}"
                if url:
                    outer.on_peer(url, props)

            def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                pass

            def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                self.add_service(zc, type_, name)

        try:
            zc = Zeroconf()
            self._zeroconf = zc
            self._browser = ServiceBrowser(zc, SERVICE_TYPE, Listener())
            logger.info("mDNS browser started for %s", SERVICE_TYPE)
            self._ready.set()
            self._stop.wait()
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            logger.warning("mDNS browser error: %s", exc)
            return

        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception as exc:
                logger.debug("mDNS browser shutdown: %s", exc)
        self._zeroconf = None
        self._browser = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
