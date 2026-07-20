"""Shard context extraction for batch_shard routing on chat completions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from netllm_core.models import Backend

_USER_NETLLM = re.compile(r"^netllm:(?P<batch>[^:]+):(?P<index>\d+)$")


@dataclass(frozen=True)
class ShardContext:
    batch_id: str | None = None
    index: int | None = None
    shard_key: str | None = None


_LEDGER_MAX_ENTRIES = 8192


@dataclass
class BatchRequestLedger:
    """Tracks (batch_id, index) → backend URL for connector-style batch sharding.

    Bounded: oldest assignments are evicted past _LEDGER_MAX_ENTRIES so a
    long-running agent doesn't grow the ledger without limit.
    """

    assignments: dict[tuple[str, int], str] = field(default_factory=dict)
    completed: set[tuple[str, int]] = field(default_factory=set)

    def _evict_if_full(self) -> None:
        if len(self.assignments) < _LEDGER_MAX_ENTRIES:
            return
        drop = len(self.assignments) // 2
        for key in list(self.assignments)[:drop]:
            del self.assignments[key]
            self.completed.discard(key)

    def assign(self, batch_id: str, index: int, backends: list[Backend]) -> str | None:
        key = (batch_id, index)
        if key in self.assignments:
            return self.assignments[key]
        urls = [b.base_url for b in backends if b.enabled]
        if not urls:
            return None
        url = urls[index % len(urls)]
        self._evict_if_full()
        self.assignments[key] = url
        return url

    def reassign_failed(
        self,
        batch_id: str,
        index: int,
        backends: list[Backend],
        *,
        current_url: str,
    ) -> str | None:
        urls = [b.base_url for b in backends if b.enabled]
        if not urls:
            return None
        try:
            pos = urls.index(current_url)
        except ValueError:
            pos = -1
        for url in urls[pos + 1 :]:
            if url != current_url:
                self.assignments[(batch_id, index)] = url
                return url
        return None

    def mark_done(self, batch_id: str, index: int) -> None:
        self.completed.add((batch_id, index))


def extract_shard_context(
    payload: dict[str, Any],
    headers: Mapping[str, str],
) -> ShardContext | None:
    normalized = {k.lower(): v for k, v in headers.items()}
    batch_id = (normalized.get("x-netllm-batch-id") or "").strip() or None
    index: int | None = None
    shard_key = (normalized.get("x-netllm-shard-key") or "").strip() or None

    index_raw = normalized.get("x-netllm-shard-index")
    if index_raw is not None:
        raw = index_raw.strip()
        if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
            index = int(raw)
        else:
            shard_key = shard_key or raw

    if batch_id or index is not None or shard_key:
        return ShardContext(batch_id=batch_id, index=index, shard_key=shard_key)

    user = payload.get("user")
    if user is not None:
        # Only the explicit netllm:<batch>:<index> convention opts a
        # request into sharding. A bare OpenAI `user` field (which many
        # SDKs set for abuse tracking) must not silently pin routing.
        match = _USER_NETLLM.match(str(user))
        if match:
            return ShardContext(
                batch_id=match.group("batch"),
                index=int(match.group("index")),
            )

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        raw_index = metadata.get("netllm_shard_index")
        if isinstance(raw_index, int):
            raw_batch = metadata.get("netllm_batch_id")
            return ShardContext(
                batch_id=str(raw_batch) if raw_batch is not None else None,
                index=raw_index,
            )
        if isinstance(raw_index, str) and raw_index.isdigit():
            raw_batch = metadata.get("netllm_batch_id")
            return ShardContext(
                batch_id=str(raw_batch) if raw_batch is not None else None,
                index=int(raw_index),
            )

    return None


def backend_for_url(url: str, candidates: list[Backend]) -> Backend | None:
    target = url.rstrip("/")
    for backend in candidates:
        if backend.base_url.rstrip("/") == target:
            return backend
    return None
