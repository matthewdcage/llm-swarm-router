"""Agent service — discovery, pool, routing orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from netllm_core.models import Backend, NetllmConfig
from netllm_core.pool import RouterPool
from netllm_discovery.local import scan_local_providers, scan_results_to_backends
from netllm_discovery.swarm import PeerRecord, SwarmRegistry
from netllm_sdk_openai.client import OpenAIUpstream, OpenAIUpstreamError

from netllm_agent.metrics import (
    BACKEND_HEALTH,
    BACKEND_IN_FLIGHT,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
)

logger = logging.getLogger(__name__)


class AgentService:
    """Core agent state shared by HTTP handlers."""

    def __init__(self, config: NetllmConfig) -> None:
        self.config = config
        self.pool = RouterPool(allow_remote=config.routing.allow_remote)
        self.swarm = SwarmRegistry(config)
        self._mdns_advertiser = None
        self._mdns_browser = None
        self._request_count = 0
        self.startup_warnings: list[str] = []

    async def refresh_local_backends(self) -> list[Backend]:
        results = await scan_local_providers(self.config)
        local = scan_results_to_backends(
            results,
            agent_id=self.config.agent.agent_id,
            local=True,
            config=self.config,
        )
        for override in self.config.routing.backends:
            if not override.enabled:
                continue
            key = override.resolve_api_key()
            found = False
            for b in local:
                if b.base_url.rstrip("/") == override.base_url.rstrip("/"):
                    b.api_key = key
                    found = True
            if not found:
                local.append(
                    Backend(
                        id=override.base_url,
                        base_url=override.base_url.rstrip("/"),
                        provider=override.provider,
                        api_key=key,
                        enabled=True,
                        local=override.local,
                        agent_id=self.config.agent.agent_id,
                    )
                )
        remote = self.swarm.peer_backends() if self.config.routing.allow_remote else []
        self.pool.merge_backends(local + remote)
        self._update_health_metrics()
        return local

    def _update_health_metrics(self) -> None:
        for b in self.pool.backends:
            healthy = 1 if self.pool.is_healthy(b) else 0
            BACKEND_HEALTH.labels(backend=b.base_url, provider=b.provider).set(healthy)
            BACKEND_IN_FLIGHT.labels(backend=b.base_url).set(b.in_flight)

    def status_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.config.agent.agent_id,
            "hostname": self.config.agent.hostname,
            "role": self.config.agent.role,
            "listen_url": self.swarm.local_agent_url(),
            "backends": [b.model_dump(mode="json") for b in self.pool.backends],
            "peers": self.swarm.all_peer_urls(),
            "routing_strategy": self.config.routing.default_strategy,
        }

    async def handle_heartbeat(self, payload: dict[str, Any]) -> None:
        agent_id = payload.get("agent_id", "")
        if not agent_id or agent_id == self.config.agent.agent_id:
            return
        self.swarm.register_peer(
            PeerRecord(
                agent_id=agent_id,
                listen_url=payload.get("listen_url", ""),
                role=payload.get("role", "peer"),
                hostname=payload.get("hostname", ""),
                backends=payload.get("backends", []),
            )
        )
        await self.refresh_local_backends()

    async def list_models_aggregated(self) -> dict[str, Any]:
        await self.refresh_local_backends()
        seen: dict[str, dict[str, Any]] = {}
        for b in self.pool.backends:
            if not b.enabled:
                continue
            self.pool.is_healthy(b)
            for mid in b.health.models:
                if mid not in seen:
                    seen[mid] = {
                        "id": mid,
                        "object": "model",
                        "owned_by": b.provider,
                    }
        return {"object": "list", "data": list(seen.values())}

    def _select_with_failover(
        self,
        model: str,
        strategy: str,
        attempt: int,
    ) -> Backend | None:
        use_strategy = strategy if attempt == 1 else "failover"
        if strategy == "batch_shard":
            use_strategy = "round_robin" if attempt == 1 else "failover"
        return self.pool.select_backend(
            model,
            use_strategy,  # type: ignore[arg-type]
            attempt=attempt,
        )

    async def proxy_chat_completion(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        model = payload.get("model", "")
        strategy = self.config.routing.default_strategy

        await self.refresh_local_backends()
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)

        while attempt < max_attempts:
            attempt += 1
            backend = self._select_with_failover(model, strategy, attempt)
            if backend is None:
                break
            backend.in_flight += 1
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            t0 = time.monotonic()
            try:
                client = OpenAIUpstream(
                    backend.base_url,
                    api_key=backend.api_key or "netllm-local",
                )
                result = await client.chat_completion(payload)
                latency = time.monotonic() - t0
                self.pool.mark_success(backend, latency * 1000)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="ok"
                ).inc()
                REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
                self._request_count += 1
                return result
            except OpenAIUpstreamError as exc:
                last_error = exc
                self.pool.mark_failure(backend)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="error"
                ).inc()
                logger.warning(
                    "backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
            finally:
                backend.in_flight = max(0, backend.in_flight - 1)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        raise OpenAIUpstreamError("No healthy backends available for model")

    async def proxy_chat_completion_stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        model = payload.get("model", "")
        strategy = self.config.routing.default_strategy

        await self.refresh_local_backends()
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)

        while attempt < max_attempts:
            attempt += 1
            backend = self._select_with_failover(model, strategy, attempt)
            if backend is None:
                break
            backend.in_flight += 1
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            try:
                client = OpenAIUpstream(
                    backend.base_url,
                    api_key=backend.api_key or "netllm-local",
                )
                async for chunk in self._stream_with_metrics(
                    client, payload, backend, model
                ):
                    yield chunk
                return
            except OpenAIUpstreamError as exc:
                last_error = exc
                logger.warning(
                    "stream backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
            finally:
                backend.in_flight = max(0, backend.in_flight - 1)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        raise OpenAIUpstreamError("No healthy backends available for model")

    async def _stream_with_metrics(
        self,
        client: OpenAIUpstream,
        payload: dict[str, Any],
        backend: Backend,
        model: str,
    ) -> AsyncIterator[str]:
        t0 = time.monotonic()
        try:
            async for chunk in client.chat_completion_stream(payload):
                yield chunk
            latency = time.monotonic() - t0
            self.pool.mark_success(backend, latency * 1000)
            REQUESTS_TOTAL.labels(
                backend=backend.base_url, model=model, status="ok"
            ).inc()
            REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
        except OpenAIUpstreamError:
            self.pool.mark_failure(backend)
            REQUESTS_TOTAL.labels(
                backend=backend.base_url, model=model, status="error"
            ).inc()
            raise

    def start_background(self) -> list[str]:
        warnings: list[str] = []
        import asyncio

        loop = asyncio.get_running_loop()

        if self.config.agent.advertise and self.config.swarm.mdns:
            try:
                from netllm_discovery.mdns import MdnsAdvertiser, MdnsBrowser

                self._mdns_advertiser = MdnsAdvertiser(
                    self.config.agent.listen,
                    self.config.agent.agent_id,
                    self.config.agent.role,
                )
                self._mdns_advertiser.start()

                async def on_peer(url: str, props: dict[str, str]) -> None:
                    agent_id = props.get("agent_id", url)
                    if agent_id == self.config.agent.agent_id:
                        return
                    record = await self.swarm.fetch_peer(url)
                    if record:
                        self.swarm.register_peer(record)
                    else:
                        self.swarm.register_peer(
                            PeerRecord(
                                agent_id=agent_id,
                                listen_url=url,
                                role=props.get("role", "peer"),
                            )
                        )
                    await self.refresh_local_backends()

                def on_peer_sync(url: str, props: dict[str, str]) -> None:
                    loop.call_soon_threadsafe(asyncio.create_task, on_peer(url, props))

                self._mdns_browser = MdnsBrowser(on_peer_sync)
                self._mdns_browser.start()
            except Exception as exc:
                warnings.append(
                    f"Swarm mDNS disabled ({exc}). "
                    "Static peers in swarm.peers still work."
                )
                logger.warning("mDNS startup failed: %s", exc, exc_info=True)
                if self._mdns_advertiser:
                    self._mdns_advertiser.stop()
                    self._mdns_advertiser = None
                if self._mdns_browser:
                    self._mdns_browser.stop()
                    self._mdns_browser = None
        elif self.config.swarm.mdns and not self.config.agent.advertise:
            warnings.append(
                "swarm.mdns is true but agent.advertise is false — "
                "this agent will not broadcast on the LAN."
            )

        if self.config.swarm.subnet_scan:
            warnings.append(
                "subnet_scan enabled — probing LAN for agents on :11400 at startup."
            )

        self.swarm.start_gossip(lambda: self.status_payload())
        if self.config.swarm.subnet_scan:
            asyncio.create_task(self._discover_subnet_peers())
        self.startup_warnings = warnings
        return warnings

    async def _discover_subnet_peers(self) -> None:
        from netllm_discovery.lan import discover_lan_agents

        try:
            peers = await discover_lan_agents(
                self.config,
                use_mdns=False,
                use_subnet=True,
                timeout_s=0,
            )
            for data in peers:
                self.swarm.register_peer(
                    PeerRecord(
                        agent_id=data.get("agent_id", ""),
                        listen_url=data.get("listen_url", ""),
                        role=data.get("role", "peer"),
                        hostname=data.get("hostname", ""),
                        backends=data.get("backends", []),
                    )
                )
            if peers:
                await self.refresh_local_backends()
                logger.info("subnet scan found %s peer agent(s)", len(peers))
        except Exception as exc:
            logger.warning("subnet peer scan failed: %s", exc)

    def stop_background(self) -> None:
        self.swarm.stop_gossip()
        if self._mdns_advertiser:
            self._mdns_advertiser.stop()
        if self._mdns_browser:
            self._mdns_browser.stop()
