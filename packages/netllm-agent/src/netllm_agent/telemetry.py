"""Unified telemetry for dashboard and macOS menubar."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from netllm_core.models import default_config_path
from netllm_discovery.local import probe_omlx_telemetry

_HISTORY_LEN = 60
_STATS_FILE = default_config_path().parent / "stats.json"


@dataclass
class _RouterCounters:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_prefill_duration: float = 0.0
    total_generation_duration: float = 0.0
    started_at: float = field(default_factory=time.time)

    def avg_prefill_tps(self) -> float:
        if self.total_prefill_duration <= 0:
            return 0.0
        return self.prompt_tokens / self.total_prefill_duration

    def avg_generation_tps(self) -> float:
        if self.total_generation_duration <= 0:
            return 0.0
        return self.completion_tokens / self.total_generation_duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "avg_prefill_tps": round(self.avg_prefill_tps(), 2),
            "avg_generation_tps": round(self.avg_generation_tps(), 2),
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _RouterCounters:
        counter = cls()
        counter.requests = int(data.get("requests") or 0)
        counter.prompt_tokens = int(data.get("prompt_tokens") or 0)
        counter.completion_tokens = int(data.get("completion_tokens") or 0)
        counter.total_prefill_duration = float(
            data.get("total_prefill_duration") or 0.0
        )
        counter.total_generation_duration = float(
            data.get("total_generation_duration") or 0.0
        )
        counter.started_at = float(data.get("started_at") or time.time())
        return counter

    def persist_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_prefill_duration": self.total_prefill_duration,
            "total_generation_duration": self.total_generation_duration,
            "started_at": self.started_at,
        }


class _RingBuffer:
    def __init__(self, maxlen: int = _HISTORY_LEN) -> None:
        self._values: deque[float] = deque(maxlen=maxlen)

    def append(self, value: float) -> None:
        self._values.append(value)

    def as_list(self) -> list[float]:
        return list(self._values)


class TelemetryService:
    """Router + oMLX telemetry with lazy oMLX admin probing."""

    def __init__(self, *, stats_path: Path | None = None) -> None:
        self._stats_path = stats_path or _STATS_FILE
        self._lock = asyncio.Lock()
        self._subscribers = 0
        self._session = _RouterCounters()
        self._alltime = _RouterCounters()
        self._load_alltime()
        self._last_omlx_probe: dict[str, Any] | None = None
        self._last_omlx_probe_at = 0.0
        self._omlx_probe_interval_s = 1.0
        self._history_router_rps = _RingBuffer()
        self._history_omlx_pp = _RingBuffer()
        self._history_omlx_tg = _RingBuffer()
        self._last_router_rps_at = 0.0
        self._requests_since_sample = 0
        self._http_client: Any | None = None

    def _load_alltime(self) -> None:
        if not self._stats_path.is_file():
            return
        try:
            data = json.loads(self._stats_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._alltime = _RouterCounters.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _save_alltime(self) -> None:
        try:
            self._stats_path.parent.mkdir(parents=True, exist_ok=True)
            self._stats_path.write_text(
                json.dumps(self._alltime.persist_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def subscribe(self) -> None:
        self._subscribers += 1

    def unsubscribe(self) -> None:
        self._subscribers = max(0, self._subscribers - 1)

    @property
    def has_subscribers(self) -> bool:
        return self._subscribers > 0

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def record_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        prefill_duration: float = 0.0,
        generation_duration: float = 0.0,
    ) -> None:
        for counter in (self._session, self._alltime):
            counter.requests += 1
            counter.prompt_tokens += max(0, prompt_tokens)
            counter.completion_tokens += max(0, completion_tokens)
            counter.total_prefill_duration += max(0.0, prefill_duration)
            counter.total_generation_duration += max(0.0, generation_duration)
        self._save_alltime()
        self._requests_since_sample += 1
        now = time.time()
        if now - self._last_router_rps_at >= 1.0:
            self._history_router_rps.append(float(self._requests_since_sample))
            self._requests_since_sample = 0
            self._last_router_rps_at = now

    def record_request(self) -> None:
        """Count a routed request without token usage metadata."""
        self.record_usage()

    async def _get_client(self) -> Any:
        if self._http_client is None:
            import httpx

            self._http_client = httpx.AsyncClient(timeout=2.0)
        return self._http_client

    async def _probe_omlx(self, backends: list[Any]) -> dict[str, Any] | None:
        if not self.has_subscribers:
            return self._last_omlx_probe
        now = time.monotonic()
        if (
            self._last_omlx_probe is not None
            and now - self._last_omlx_probe_at < self._omlx_probe_interval_s
        ):
            return self._last_omlx_probe
        client = await self._get_client()
        stats = await probe_omlx_telemetry(backends, client)
        self._last_omlx_probe = stats
        self._last_omlx_probe_at = now
        if stats and stats.get("available"):
            live = stats.get("live") or {}
            self._history_omlx_pp.append(float(live.get("prefill_tps") or 0.0))
            self._history_omlx_tg.append(float(live.get("generation_tps") or 0.0))
        return stats

    def _router_block(self, pool: Any) -> dict[str, Any]:
        in_flight = sum(b.in_flight for b in pool.backends if b.enabled)
        return {
            "session": self._session.to_dict(),
            "alltime": self._alltime.to_dict(),
            "routed_requests": dict(pool.routed_counts),
            "capacity_rejections": dict(pool.capacity_rejections),
            "shardless_fallbacks": getattr(pool, "shardless_fallbacks", 0),
            "in_flight_total": in_flight,
            "backends": [
                {
                    "id": b.id,
                    "provider": b.provider,
                    "base_url": b.base_url,
                    "health": b.health.status,
                    "in_flight": b.in_flight,
                }
                for b in pool.backends
                if b.enabled
            ],
        }

    async def build_payload(
        self,
        service: Any,
        *,
        scopes: set[str] | None = None,
        include_history: bool = True,
    ) -> dict[str, Any]:
        active_scopes = scopes or {"router", "omlx"}
        payload: dict[str, Any] = {
            "schema_version": 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if "router" in active_scopes:
            shardless = getattr(service, "_shardless_fallbacks", 0)
            router = self._router_block(service.pool)
            router["shardless_fallbacks"] = shardless
            payload["router"] = router
        if "omlx" in active_scopes:
            omlx = await self._probe_omlx(service.pool.backends)
            payload["omlx"] = omlx or {"available": False}
        payload["host"] = self._host_block()
        if include_history:
            payload["history"] = {
                "router_rps": self._history_router_rps.as_list(),
                "omlx_pp_tps": self._history_omlx_pp.as_list(),
                "omlx_tg_tps": self._history_omlx_tg.as_list(),
            }
        return payload

    @staticmethod
    def _host_block() -> dict[str, Any] | None:
        try:
            import psutil
        except ImportError:
            return None
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_used_gb": round(vm.used / (1024**3), 2),
            "memory_total_gb": round(vm.total / (1024**3), 2),
            "memory_percent": vm.percent,
        }
