"""Unified health probes for OpenAI-compatible inference servers."""

from __future__ import annotations

import time
from typing import Any

import httpx

DEFAULT_TIMEOUT = 5.0
INFERENCE_TIMEOUT = 10.0
SLOW_THRESHOLD_MS = 5000


def status_from_response(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code == 200:
        body = resp.json()
        model_ids = [m.get("id", "") for m in body.get("data", [])]
        return {
            "status": "online",
            "http_status": resp.status_code,
            "model_count": len(model_ids),
            "models": model_ids,
        }
    if resp.status_code in (401, 403):
        return {
            "status": "online",
            "http_status": resp.status_code,
            "model_count": 0,
            "models": [],
            "detail": "authentication required",
        }
    return {
        "status": "error",
        "http_status": resp.status_code,
        "detail": resp.text[:200],
    }


def status_from_exception(
    exc: BaseException, timeout_s: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    if isinstance(exc, httpx.ConnectError):
        return {"status": "offline", "detail": "Connection refused"}
    if isinstance(exc, httpx.TimeoutException):
        return {
            "status": "timeout",
            "detail": f"No response within {timeout_s}s",
        }
    return {"status": "error", "detail": str(exc)}


async def probe_openai_compat(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """GET /v1/models — 401/403 treated as online (reachable)."""
    models_url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = await client.get(models_url, headers=headers, timeout=timeout_s)
        return status_from_response(resp)
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
        return status_from_exception(exc, timeout_s)


def probe_openai_compat_sync(
    base_url: str,
    *,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    models_url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(models_url, headers=headers)
        return status_from_response(resp)
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
        return status_from_exception(exc, timeout_s)


def is_online(status: dict[str, Any]) -> bool:
    return status.get("status") == "online"


async def diagnose_backend(
    base_url: str,
    client: httpx.AsyncClient,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Models list + optional 1-token completion latency test."""
    health = await probe_openai_compat(base_url, client, api_key=api_key)
    result: dict[str, Any] = {
        **health,
        "latency_ms": None,
        "inference_status": None,
    }
    if not is_online(health):
        return result

    models: list[str] = health.get("models") or []
    test_model = model
    if test_model and test_model not in models:
        if not any(m == test_model or m.startswith(test_model + ":") for m in models):
            result["inference_status"] = "model_not_found"
            return result
    if not test_model and models:
        test_model = models[0]
    if not test_model:
        result["inference_status"] = "no_models"
        return result

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": test_model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }
    t0 = time.monotonic()
    try:
        resp = await client.post(
            url, json=payload, headers=headers, timeout=INFERENCE_TIMEOUT
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        result["latency_ms"] = latency_ms
        if resp.status_code == 200:
            result["inference_status"] = (
                "online_slow" if latency_ms > SLOW_THRESHOLD_MS else "online"
            )
        elif resp.status_code in (401, 403):
            result["inference_status"] = "auth_required"
        elif resp.status_code == 404:
            result["inference_status"] = "model_not_found"
        else:
            result["inference_status"] = "inference_failed"
            result["detail"] = resp.text[:200]
    except httpx.TimeoutException:
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        result["inference_status"] = "timeout"
    except httpx.ConnectError:
        result["inference_status"] = "offline"
    except Exception as exc:
        result["inference_status"] = "inference_failed"
        result["detail"] = str(exc)
    return result
