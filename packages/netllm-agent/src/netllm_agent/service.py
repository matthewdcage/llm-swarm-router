"""Agent service — discovery, pool, routing orchestration."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from netllm_core.anthropic_bridge import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    translate_openai_stream_to_anthropic,
)
from netllm_core.models import (
    ANTHROPIC_CLOUD_BASE_URL,
    LOCAL_ONLY_HEADER,
    OPENAI_CLOUD_BASE_URL,
    Backend,
    NetllmConfig,
)
from netllm_core.pool import RouterPool
from netllm_core.routing_policy import ResolvedRouting, resolve_routing
from netllm_discovery.local import (
    find_omlx_admin_url,
    probe_omlx_admin_for_backends,
    scan_local_providers,
    scan_results_to_backends,
)
from netllm_discovery.swarm import PeerRecord, SwarmRegistry
from netllm_sdk_anthropic.client import AnthropicUpstream, AnthropicUpstreamError
from netllm_sdk_openai.client import OpenAIUpstream, OpenAIUpstreamError

from netllm_agent.metrics import (
    BACKEND_HEALTH,
    BACKEND_IN_FLIGHT,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
)
from netllm_agent.shard import (
    BatchRequestLedger,
    ShardContext,
    backend_for_url,
    extract_shard_context,
)

logger = logging.getLogger(__name__)


class AgentService:
    """Core agent state shared by HTTP handlers."""

    def __init__(self, config: NetllmConfig) -> None:
        self.config = config
        self.pool = RouterPool(
            allow_remote=config.routing.allow_remote,
            spillover_max_local_in_flight=(
                config.routing.spillover_max_local_in_flight
            ),
        )
        self.swarm = SwarmRegistry(config)
        self._mdns_advertiser = None
        self._mdns_browser = None
        self._request_count = 0
        self._batch_ledger = BatchRequestLedger()
        self.startup_warnings: list[str] = []

    async def refresh_local_backends(
        self,
        *,
        persist_provider_urls: bool = False,
        config_path: Path | None = None,
    ) -> list[Backend]:
        from netllm_core.models import save_config
        from netllm_discovery.local import merge_discovered_provider_urls

        results = await scan_local_providers(self.config)
        if persist_provider_urls and config_path is not None:
            before = dict(self.config.discovery.provider_urls)
            merge_discovered_provider_urls(self.config, results)
            if self.config.discovery.provider_urls != before:
                save_config(self.config, config_path)
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
                    b.api_format = override.resolved_api_format()
                    found = True
            if not found:
                local.append(
                    Backend(
                        id=override.base_url,
                        base_url=override.base_url.rstrip("/"),
                        provider=override.provider,
                        api_format=override.resolved_api_format(),
                        api_key=key,
                        enabled=True,
                        local=override.local,
                        agent_id=self.config.agent.agent_id,
                    )
                )
        remote = (
            self.swarm.peer_agent_backends() if self.config.routing.allow_remote else []
        )
        self.pool.merge_backends(local + remote)
        self._update_health_metrics()
        return local

    def _update_health_metrics(self) -> None:
        for b in self.pool.backends:
            healthy = 1 if self.pool.is_healthy(b) else 0
            BACKEND_HEALTH.labels(backend=b.base_url, provider=b.provider).set(healthy)
            BACKEND_IN_FLIGHT.labels(backend=b.base_url).set(b.in_flight)

    def status_payload(self) -> dict[str, Any]:
        omlx_admin = find_omlx_admin_url(self.pool.backends)
        payload: dict[str, Any] = {
            "agent_id": self.config.agent.agent_id,
            "hostname": self.config.agent.hostname,
            "role": self.config.agent.role,
            "listen_url": self.swarm.local_agent_url(),
            "backends": [b.model_dump(mode="json") for b in self.pool.backends],
            "peers": self.swarm.all_peer_urls(),
            "routing_strategy": self.config.routing.default_strategy,
        }
        if omlx_admin:
            payload["omlx_admin_url"] = omlx_admin
        return payload

    async def status_payload_enriched(self) -> dict[str, Any]:
        payload = self.status_payload()
        omlx_stats = await probe_omlx_admin_for_backends(self.pool.backends)
        if omlx_stats:
            payload["omlx_stats"] = omlx_stats
        return payload

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
            # Force-probe local providers only. Peer-agent rows are kept
            # fresh by heartbeats; probing them from a catalog handler
            # recurses (the peer's handler would probe us back).
            if b.local:
                self.pool.is_healthy(b, force_refresh=True)
            for mid in b.health.models:
                if mid not in seen:
                    seen[mid] = {
                        "id": mid,
                        "object": "model",
                        "owned_by": b.provider,
                    }
        return {"object": "list", "data": list(seen.values())}

    @staticmethod
    def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        if not headers:
            return {}
        return {str(k).lower(): str(v) for k, v in headers.items()}

    @staticmethod
    def _wants_local_only(headers: Mapping[str, str] | None) -> bool:
        hdrs = AgentService._normalize_headers(headers)
        raw = hdrs.get(LOCAL_ONLY_HEADER, "")
        return raw.strip().lower() in ("1", "true", "yes")

    @staticmethod
    def _peer_forward_headers(backend: Backend) -> dict[str, str] | None:
        """Loop guard: agent-hop forwards must terminate at the peer.

        Without this header a peer running a distributing strategy
        (round_robin, least_load, ...) could bounce the request back,
        ping-ponging it across the mesh.
        """
        if backend.id.startswith("peer:"):
            return {LOCAL_ONLY_HEADER: "1"}
        return None

    def _resolved_routing(
        self,
        model: str,
        *,
        api_format: str,
        headers: Mapping[str, str] | None,
    ) -> ResolvedRouting:
        return resolve_routing(
            self.config.routing,
            model=model,
            api_format=api_format,  # type: ignore[arg-type]
            header_local_only=self._wants_local_only(headers),
        )

    def _select_backend_for_request(
        self,
        model: str,
        strategy: str,
        attempt: int,
        shard: ShardContext | None,
        *,
        local_only: bool = False,
        prefer_provider: str | None = None,
    ) -> Backend | None:
        if strategy == "batch_shard":
            if shard and shard.batch_id is not None and shard.index is not None:
                candidates = self.pool.backends_for_model(model)
                if attempt == 1:
                    url = self._batch_ledger.assign(
                        shard.batch_id, shard.index, candidates
                    )
                else:
                    current = self._batch_ledger.assignments.get(
                        (shard.batch_id, shard.index), ""
                    )
                    url = self._batch_ledger.reassign_failed(
                        shard.batch_id,
                        shard.index,
                        candidates,
                        current_url=current,
                    )
                if url:
                    return backend_for_url(url, candidates)
                return None

            shard_key = shard.shard_key if shard else None
            if shard_key is None and shard and shard.index is not None:
                shard_key = str(shard.index)
            if shard_key:
                use_strategy = "batch_shard" if attempt == 1 else "failover"
                return self.pool.select_backend(
                    model,
                    use_strategy,  # type: ignore[arg-type]
                    shard_key=shard_key,
                    attempt=attempt,
                    local_only=local_only,
                    prefer_provider=prefer_provider,
                )

            if attempt == 1:
                logger.warning(
                    "batch_shard without shard context — falling back to round_robin"
                )
                return self.pool.select_backend(
                    model,
                    "round_robin",
                    local_only=local_only,
                    prefer_provider=prefer_provider,
                )
            return self.pool.select_backend(
                model,
                "failover",
                attempt=attempt,
                local_only=local_only,
                prefer_provider=prefer_provider,
            )

        use_strategy = strategy if attempt == 1 else "failover"
        shard_key = shard.shard_key if shard else None
        return self.pool.select_backend(
            model,
            use_strategy,  # type: ignore[arg-type]
            shard_key=shard_key,
            attempt=attempt,
            local_only=local_only,
            prefer_provider=prefer_provider,
        )

    def _mark_shard_success(self, shard: ShardContext | None) -> None:
        if shard and shard.batch_id is not None and shard.index is not None:
            self._batch_ledger.mark_done(shard.batch_id, shard.index)

    async def proxy_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        routing = self._resolved_routing(model, api_format="openai", headers=hdrs)
        shard = extract_shard_context(payload, hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_openai_cloud_backend(self._openai_api_key(hdrs))
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)

        while attempt < max_attempts:
            attempt += 1
            backend = self._select_backend_for_request(
                model,
                routing.strategy,
                attempt,
                shard,
                local_only=routing.local_only,
                prefer_provider=routing.prefer_provider,
            )
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            t0 = time.monotonic()
            try:
                client = OpenAIUpstream(
                    backend.base_url,
                    api_key=backend.api_key or "netllm-local",
                    default_headers=self._peer_forward_headers(backend),
                )
                result = await client.chat_completion(payload)
                latency = time.monotonic() - t0
                self.pool.mark_success(backend, latency * 1000)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="ok"
                ).inc()
                REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
                self._request_count += 1
                self._mark_shard_success(shard)
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
                self.pool.release(backend)
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
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        routing = self._resolved_routing(model, api_format="openai", headers=hdrs)
        shard = extract_shard_context(payload, hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_openai_cloud_backend(self._openai_api_key(hdrs))
        attempt = 0
        last_error: Exception | None = None
        max_attempts = max(len(self.pool.backends), 1)

        while attempt < max_attempts:
            attempt += 1
            backend = self._select_backend_for_request(
                model,
                routing.strategy,
                attempt,
                shard,
                local_only=routing.local_only,
                prefer_provider=routing.prefer_provider,
            )
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            try:
                client = OpenAIUpstream(
                    backend.base_url,
                    api_key=backend.api_key or "netllm-local",
                    default_headers=self._peer_forward_headers(backend),
                )
                async for chunk in self._stream_with_metrics(
                    client, payload, backend, model, shard
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
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        raise OpenAIUpstreamError("No healthy backends available for model")

    @staticmethod
    def _anthropic_api_key(headers: Mapping[str, str]) -> str:
        return headers.get("x-api-key") or os.environ.get("ANTHROPIC_API_KEY", "")

    @staticmethod
    def _openai_api_key(headers: Mapping[str, str]) -> str:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token and token != "netllm-local":
                return token
        env_key = os.environ.get("OPENAI_API_KEY", "")
        if env_key and env_key != "netllm-local":
            return env_key
        return ""

    @staticmethod
    def _anthropic_default_headers(headers: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("anthropic-version", "anthropic-beta"):
            if key in headers:
                out[key] = headers[key]
        return out

    def _inject_anthropic_cloud_backend(self, api_key: str) -> None:
        if not api_key or api_key == "netllm-local":
            return
        if any(b.api_format == "anthropic" for b in self.pool.backends):
            return
        self.pool.merge_backends(
            [
                Backend(
                    id="anthropic-cloud",
                    base_url=ANTHROPIC_CLOUD_BASE_URL,
                    provider="anthropic",
                    api_format="anthropic",
                    api_key=api_key,
                    enabled=True,
                    local=False,
                    agent_id=self.config.agent.agent_id,
                )
            ]
        )

    def _inject_openai_cloud_backend(self, api_key: str) -> None:
        if not api_key:
            return
        cloud_url = OPENAI_CLOUD_BASE_URL.rstrip("/")
        if any(
            b.api_format == "openai" and b.base_url.rstrip("/") == cloud_url
            for b in self.pool.backends
        ):
            return
        self.pool.merge_backends(
            [
                Backend(
                    id="openai-cloud",
                    base_url=cloud_url,
                    provider="openai",
                    api_format="openai",
                    api_key=api_key,
                    enabled=True,
                    local=False,
                    agent_id=self.config.agent.agent_id,
                )
            ]
        )

    def _order_message_candidates(
        self, candidates: list[Backend], routing: ResolvedRouting
    ) -> list[Backend]:
        ordered = candidates
        if routing.prefer_provider:
            preferred = [b for b in ordered if b.provider == routing.prefer_provider]
            if preferred:
                others = [b for b in ordered if b.provider != routing.prefer_provider]
                ordered = preferred + others
        if routing.strategy in ("local_first", "local_spillover"):
            local = [b for b in ordered if b.local]
            remote = [b for b in ordered if not b.local]
            if local:
                ordered = local + remote
            if routing.strategy == "local_spillover" and local and remote:
                best_local = min(local, key=lambda b: b.in_flight)
                best_remote = min(remote, key=lambda b: b.in_flight)
                threshold = self.pool.spillover_max_local_in_flight
                if (
                    best_local.in_flight >= threshold
                    and best_remote.in_flight < best_local.in_flight
                ):
                    ordered = remote + local
        return ordered

    def _message_backend_candidates(
        self, model: str, *, local_only: bool = False
    ) -> list[Backend]:
        openai_backends: list[Backend] = []
        anthropic_backends: list[Backend] = []
        for b in self.pool.backends:
            if not b.enabled:
                continue
            if not b.local and not self.pool.allow_remote:
                continue
            if local_only and not b.local:
                continue
            if b.api_format == "anthropic":
                anthropic_backends.append(b)
                continue
            models = b.health.models
            if not models or any(
                m == model or m.startswith(model + ":") for m in models
            ):
                openai_backends.append(b)
        local = [b for b in openai_backends if b.local]
        remote = [b for b in openai_backends if not b.local]
        strategy = self.config.routing.default_strategy
        if strategy == "local_first":
            ordered = local + remote
        else:
            ordered = openai_backends
        if not ordered:
            ordered = [
                b
                for b in self.pool.backends
                if b.enabled
                and b.api_format == "openai"
                and (not local_only or b.local)
                and (b.local or self.pool.allow_remote)
            ]
        healthy = [b for b in ordered if self.pool.is_healthy(b)]
        return (healthy or ordered) + anthropic_backends

    async def proxy_messages(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        api_key = self._anthropic_api_key(hdrs)
        routing = self._resolved_routing(model, api_format="anthropic", headers=hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_anthropic_cloud_backend(api_key)

        candidates = self._message_backend_candidates(
            model, local_only=routing.local_only
        )
        candidates = self._order_message_candidates(candidates, routing)
        last_error: Exception | None = None
        max_attempts = max(len(candidates), 1)

        for attempt in range(1, max_attempts + 1):
            backend = candidates[attempt - 1] if attempt <= len(candidates) else None
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            t0 = time.monotonic()
            try:
                result = await self._messages_on_backend(
                    backend, payload, model, hdrs, api_key
                )
                latency = time.monotonic() - t0
                self.pool.mark_success(backend, latency * 1000)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="ok"
                ).inc()
                REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency)
                self._request_count += 1
                return result
            except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                last_error = exc
                self.pool.mark_failure(backend)
                REQUESTS_TOTAL.labels(
                    backend=backend.base_url, model=model, status="error"
                ).inc()
                logger.warning(
                    "messages backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
            finally:
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        if not api_key:
            raise AnthropicUpstreamError(
                "ANTHROPIC_API_KEY required for cloud Messages API",
                status_code=401,
            )
        raise AnthropicUpstreamError("No healthy backends available for model")

    async def proxy_messages_stream(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[str]:
        hdrs = self._normalize_headers(headers)
        model = payload.get("model", "")
        api_key = self._anthropic_api_key(hdrs)
        routing = self._resolved_routing(model, api_format="anthropic", headers=hdrs)

        await self.refresh_local_backends()
        if routing.allow_cloud_inject:
            self._inject_anthropic_cloud_backend(api_key)

        candidates = self._message_backend_candidates(
            model, local_only=routing.local_only
        )
        candidates = self._order_message_candidates(candidates, routing)
        last_error: Exception | None = None
        max_attempts = max(len(candidates), 1)

        for attempt in range(1, max_attempts + 1):
            backend = candidates[attempt - 1] if attempt <= len(candidates) else None
            if backend is None:
                break
            self.pool.acquire(backend)
            BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
            try:
                async for chunk in self._messages_stream_on_backend(
                    backend, payload, model, hdrs, api_key
                ):
                    yield chunk
                return
            except (AnthropicUpstreamError, OpenAIUpstreamError) as exc:
                last_error = exc
                self.pool.mark_failure(backend)
                logger.warning(
                    "messages stream backend %s failed (attempt %s): %s",
                    backend.base_url,
                    attempt,
                    exc,
                )
            finally:
                self.pool.release(backend)
                BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(
                    backend.in_flight
                )
                self._update_health_metrics()

        if last_error:
            raise last_error
        if not api_key:
            raise AnthropicUpstreamError(
                "ANTHROPIC_API_KEY required for cloud Messages API",
                status_code=401,
            )
        raise AnthropicUpstreamError("No healthy backends available for model")

    async def _messages_on_backend(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        headers: Mapping[str, str],
        fallback_api_key: str,
    ) -> dict[str, Any]:
        if backend.api_format == "anthropic":
            key = backend.resolve_api_key() or fallback_api_key
            if not key:
                raise AnthropicUpstreamError(
                    "ANTHROPIC_API_KEY required", status_code=401
                )
            client = AnthropicUpstream(
                key,
                base_url=backend.base_url,
                default_headers=self._anthropic_default_headers(headers),
            )
            return await client.messages_create(payload)
        oai_payload = anthropic_to_openai_request(payload)
        client = OpenAIUpstream(
            backend.base_url,
            api_key=backend.api_key or "netllm-local",
            default_headers=self._peer_forward_headers(backend),
        )
        result = await client.chat_completion(oai_payload)
        return openai_to_anthropic_response(result, model=model)

    async def _messages_stream_on_backend(
        self,
        backend: Backend,
        payload: dict[str, Any],
        model: str,
        headers: Mapping[str, str],
        fallback_api_key: str,
    ) -> AsyncIterator[str]:
        if backend.api_format == "anthropic":
            key = backend.resolve_api_key() or fallback_api_key
            if not key:
                raise AnthropicUpstreamError(
                    "ANTHROPIC_API_KEY required", status_code=401
                )
            client = AnthropicUpstream(
                key,
                base_url=backend.base_url,
                default_headers=self._anthropic_default_headers(headers),
            )
            async for chunk in client.messages_stream(payload):
                yield chunk
            return
        oai_payload = anthropic_to_openai_request(payload)
        client = OpenAIUpstream(
            backend.base_url,
            api_key=backend.api_key or "netllm-local",
            default_headers=self._peer_forward_headers(backend),
        )
        async for chunk in translate_openai_stream_to_anthropic(
            client.chat_completion_stream(oai_payload),
            model=model,
        ):
            yield chunk

    async def _stream_with_metrics(
        self,
        client: OpenAIUpstream,
        payload: dict[str, Any],
        backend: Backend,
        model: str,
        shard: ShardContext | None = None,
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
            self._mark_shard_success(shard)
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
                    "A prior netllm serve may still be registered — try "
                    "netllm serve --replace. Static peers in swarm.peers still work."
                )
                logger.warning("mDNS startup failed: %s", exc)
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
