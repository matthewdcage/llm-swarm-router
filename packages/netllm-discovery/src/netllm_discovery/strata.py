"""Strata-specific discovery and load helpers (Niko1221/Strata)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

DEFAULT_LOAD_TIMEOUT_S = 120.0
DEFAULT_LOAD_POLL_S = 0.5


def service_root_from_v1_base(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    if raw.endswith("/v1"):
        return raw[:-3].rstrip("/")
    return raw.rstrip("/")


async def probe_strata_identity(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    api_key: str | None = None,
    timeout_s: float = 2.0,
) -> bool:
    """True when the server root looks like Strata (GET /health with loaded key)."""
    root = service_root_from_v1_base(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = await client.get(f"{root}/health", headers=headers, timeout=timeout_s)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data, dict) and "loaded" in data
    except Exception:
        return False


async def fetch_strata_loaded(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    api_key: str | None = None,
    timeout_s: float = 2.0,
) -> bool | None:
    """Return load state from /health, or None when not Strata / unreachable."""
    root = service_root_from_v1_base(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = await client.get(f"{root}/health", headers=headers, timeout=timeout_s)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict) or "loaded" not in data:
            return None
        return bool(data.get("loaded"))
    except Exception:
        return None


def parse_strata_models_payload(body: dict[str, Any]) -> tuple[list[str], bool]:
    """Extract model ids and whether the engine reports loaded.

    Strata may list catalog entries while unloaded; we still advertise ids for
    routing while setting loaded=False on the probe result.
    """
    loaded = True
    ids: list[str] = []
    for item in body.get("data") or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if mid:
            ids.append(str(mid))
        state = str(item.get("state") or "").lower()
        if state == "unloaded":
            loaded = False
    return ids, loaded


async def post_strata_load(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_LOAD_TIMEOUT_S,
) -> httpx.Response:
    root = service_root_from_v1_base(base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return await client.post(
        f"{root}/load", headers=headers, json={}, timeout=timeout_s
    )


async def ensure_strata_loaded(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_LOAD_TIMEOUT_S,
) -> None:
    """Wake Strata when /health reports unloaded. No-op when already loaded."""
    loaded = await fetch_strata_loaded(base_url, client, api_key=api_key)
    if loaded is True:
        return
    if loaded is None:
        return
    resp = await post_strata_load(
        base_url, client, api_key=api_key, timeout_s=timeout_s
    )
    if resp.status_code == 503:
        retry = resp.headers.get("Retry-After", "15")
        raise StrataCapacityError(
            f"Strata GPU busy (503); retry after {retry}s", retry_after_s=retry
        )
    if resp.status_code >= 400:
        raise StrataLoadError(
            f"Strata load failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = await fetch_strata_loaded(base_url, client, api_key=api_key)
        if state is True:
            return
        await asyncio.sleep(DEFAULT_LOAD_POLL_S)
    raise StrataLoadError("Strata did not report loaded within timeout")


class StrataLoadError(RuntimeError):
    """Strata POST /load or load wait failed."""


class StrataCapacityError(StrataLoadError):
    """Strata refused load (GPU in use)."""

    def __init__(self, message: str, *, retry_after_s: str = "15") -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
