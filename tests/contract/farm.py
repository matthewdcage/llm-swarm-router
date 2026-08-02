"""FakeFarm — in-process scriptable fake backends for the contract harness.

Speaks BOTH wire dialects (OpenAI chat/embeddings JSON + SSE; Anthropic
messages JSON + SSE) and is injected BELOW the SDK clients by patching the
httpx transport classes (``httpx.AsyncHTTPTransport.handle_async_request``
and ``httpx.HTTPTransport.handle_request``). That is the F-30 lesson: the
real ``openai`` / ``anthropic`` SDK request-build and response-parse paths
must execute — the fake replaces only the network.

Everything the router's process sends over httpx lands here: SDK inference
calls, health probes (GET .../models, POST .../v1/messages), and any stray
traffic (unknown hosts get a ``httpx.ConnectError``, exactly like a dead
port). Starlette's TestClient and ASGITransport are unaffected — they never
touch the patched transport classes.

Behavior scripts (JSON-friendly dicts, so vectors can carry them):

- ``{"behavior": "ok"}`` — dialect-appropriate success; streams when the
  request asked for ``stream`` (default chunks), JSON otherwise.
- ``{"behavior": "ok_stream", "chunks": [...], "usage_in_final": true}`` —
  explicit stream success; ``usage_in_final`` controls whether the final
  frame carries a usage block.
- ``{"behavior": "http", "status": 500}`` — error status (409/429/500/502/
  503/507 all exercised by vectors) with a dialect-shaped error body.
- ``{"behavior": "capacity_body_marker", "marker": "memory pressure"}`` —
  a non-capacity status (default 500) whose *body* carries a capacity
  marker, exercising ``is_capacity_error``'s body-marker arm (pool.py).
- ``{"behavior": "midstream_drop", "after_n": 2}`` — stream that emits N
  frames then raises ``httpx.ReadError`` mid-body.
- ``{"behavior": "bad_json"}`` — HTTP 200 with an unparseable JSON body.

Determinism contract: no real sleeps, no wall-clock content (fixed ids and
timestamps), and every scripted error response carries ``x-should-retry:
false`` + ``retry-after: 0`` so the official SDKs' internal retry loop
(which would add exponential-backoff sleeps and duplicate upstream calls)
is switched off by the server, a mechanism both SDKs implement. One router
attempt therefore equals exactly one recorded upstream call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import httpx

FARM_API_KEY = "farm-key"

# Fixed, wall-clock-free response identity — canonical.py normalizes these
# anyway, but fixing them keeps raw (pre-canonical) diffs readable.
_FIXED_CREATED = 1700000000
_CHAT_ID = "chatcmpl-farm"
_MSG_ID = "msg_farm"

# Recorded-request header allowlist: routing-relevant headers only, so
# vectors pin the peer loop-guard / auth surface without dragging along
# volatile SDK platform headers (user-agent, x-stainless-*).
_RECORD_HEADERS = (
    "authorization",
    "x-api-key",
    "anthropic-version",
    "anthropic-beta",
    "x-netllm-local-only",
    "x-netllm-hops",
    "x-netllm-source",
    "x-netllm-strategy",
    "x-netllm-backend",
)


def ok(**kw: Any) -> dict[str, Any]:
    return {"behavior": "ok", **kw}


def ok_stream(
    chunks: list[str] | None = None, *, usage_in_final: bool = True
) -> dict[str, Any]:
    out: dict[str, Any] = {"behavior": "ok_stream", "usage_in_final": usage_in_final}
    if chunks is not None:
        out["chunks"] = chunks
    return out


def http(status: int, **kw: Any) -> dict[str, Any]:
    return {"behavior": "http", "status": status, **kw}


def capacity_body_marker(
    marker: str = "memory pressure", status: int = 500
) -> dict[str, Any]:
    return {"behavior": "capacity_body_marker", "marker": marker, "status": status}


def midstream_drop(after_n: int) -> dict[str, Any]:
    return {"behavior": "midstream_drop", "after_n": after_n}


def bad_json() -> dict[str, Any]:
    return {"behavior": "bad_json"}


@dataclass
class RecordedCall:
    """One inbound inference request, verbatim."""

    backend: str
    method: str
    path: str
    model: str | None
    headers: dict[str, str]
    body: Any


@dataclass
class FarmBackend:
    name: str
    api_format: str  # "openai" | "anthropic"
    models: list[str]
    script: list[dict[str, Any]] = field(default_factory=list)
    local: bool = True
    # When False the host 404s every GET .../models, so the local-provider
    # scan never discovers it and the router only knows it through its
    # routing.backends override (a blind candidate). Needed to exercise
    # `local: false` override rows: a scan-discovered row is always
    # local=True and wins the pool merge over the override's flag.
    scan_visible: bool = True
    # Explicit base URL, for hosts that must answer at a specific address
    # rather than at "<name>.farm" — the only real case is standing in for
    # a cloud endpoint (OPENAI_CLOUD_BASE_URL / ANTHROPIC_CLOUD_BASE_URL) so
    # a request-scoped legacy cloud row is reachable and can pass the pool's
    # health gate. None keeps the "<name>.farm" convention.
    url: str | None = None
    # False keeps the host out of routing.backends: it exists on the wire
    # but the router does not know it as a configured pool row. Required
    # for cloud stand-ins — a configured row at the same URL suppresses the
    # legacy inject entirely (service._legacy_openai_cloud_backend).
    configured: bool = True
    _cursor: int = 0

    @property
    def base_url(self) -> str:
        # OpenAI rows carry ".../v1" (the SDK appends /chat/completions);
        # Anthropic rows carry the bare root (the SDK appends /v1/messages).
        if self.url is not None:
            return self.url
        if self.api_format == "anthropic":
            return f"http://{self.name}.farm"
        return f"http://{self.name}.farm/v1"

    @property
    def host(self) -> str:
        return httpx.URL(self.base_url).host

    def next_behavior(self) -> dict[str, Any]:
        if not self.script:
            return {"behavior": "ok"}
        idx = min(self._cursor, len(self.script) - 1)
        self._cursor += 1
        return self.script[idx]


class _FarmByteStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    """Deterministic body stream; optionally raises mid-body."""

    def __init__(self, frames: list[bytes], *, drop_after: int | None = None) -> None:
        self._frames = frames
        self._drop_after = drop_after

    def _emit(self) -> Iterator[bytes]:
        for i, frame in enumerate(self._frames):
            if self._drop_after is not None and i >= self._drop_after:
                raise httpx.ReadError("farm: midstream drop")
            yield frame
        if self._drop_after is not None and self._drop_after >= len(self._frames):
            raise httpx.ReadError("farm: midstream drop")

    def __iter__(self) -> Iterator[bytes]:
        yield from self._emit()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._emit():
            yield chunk

    def close(self) -> None:  # pragma: no cover - protocol hook
        pass

    async def aclose(self) -> None:  # pragma: no cover - protocol hook
        pass


# Headers that stop the official SDKs from retrying scripted failures
# in-process (see module docstring).
_NO_SDK_RETRY = {"x-should-retry": "false", "retry-after": "0"}


class FakeFarm:
    """Scriptable fake backend farm behind the patched httpx transports."""

    def __init__(self) -> None:
        self._by_host: dict[str, FarmBackend] = {}
        self.requests: list[RecordedCall] = []
        self.probes: list[tuple[str, str, str]] = []  # (backend, method, path)

    # -- setup ---------------------------------------------------------

    def add_backend(
        self,
        name: str,
        *,
        api_format: str = "openai",
        models: list[str] | None = None,
        script: list[dict[str, Any]] | None = None,
        local: bool = True,
        scan_visible: bool = True,
        url: str | None = None,
        configured: bool = True,
    ) -> FarmBackend:
        backend = FarmBackend(
            name=name,
            api_format=api_format,
            models=list(models or []),
            script=list(script or []),
            local=local,
            scan_visible=scan_visible,
            url=url,
            configured=configured,
        )
        self._by_host[backend.host] = backend
        return backend

    @property
    def backends(self) -> list[FarmBackend]:
        return list(self._by_host.values())

    @contextmanager
    def installed(self) -> Iterator[FakeFarm]:
        """Patch the httpx transport classes for the duration.

        Class-method patches so even pre-existing client instances (e.g.
        netllm_core.health's cached module-level sync client) route here.
        """
        farm = self

        async def _async_handle(
            _self: httpx.AsyncHTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            return farm.handle(request)

        def _sync_handle(
            _self: httpx.HTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            return farm.handle(request)

        with (
            mock.patch.object(
                httpx.AsyncHTTPTransport, "handle_async_request", _async_handle
            ),
            mock.patch.object(httpx.HTTPTransport, "handle_request", _sync_handle),
        ):
            yield self

    # -- request log ---------------------------------------------------

    def inference_calls(self) -> list[dict[str, Any]]:
        """Ordered upstream inference calls in vector-friendly shape."""
        return [
            {
                "backend": call.backend,
                "method": call.method,
                "path": call.path,
                "model": call.model,
                "headers": call.headers,
            }
            for call in self.requests
        ]

    # -- transport handler ---------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        backend = self._by_host.get(host)
        if backend is None:
            raise httpx.ConnectError(f"farm: no backend at {host}", request=request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/models"):
            self.probes.append((backend.name, request.method, path))
            if not backend.scan_visible:
                return httpx.Response(404, json={"error": "not found"})
            if backend.api_format == "anthropic" and "x-api-key" not in request.headers:
                # Anthropic-surface hosts expose no anonymous OpenAI-style
                # catalog. Without this, the local-provider scan (which
                # probes every routing.backends override at the
                # OpenAI-normalized {root}/v1/models URL) would discover a
                # second, openai-format pool row for the same host and the
                # Messages paths would silently exercise the translation
                # arm instead of the real Anthropic arm.
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": m} for m in backend.models]},
            )
        if request.method == "GET" and path.endswith("/health"):
            self.probes.append((backend.name, request.method, path))
            return httpx.Response(200, json={"status": "ok"})

        body: Any = None
        raw = request.read()
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode("utf-8", "replace")
        model = body.get("model") if isinstance(body, dict) else None
        self.requests.append(
            RecordedCall(
                backend=backend.name,
                method=request.method,
                path=path,
                model=model,
                headers={
                    k: request.headers[k]
                    for k in _RECORD_HEADERS
                    if k in request.headers
                },
                body=body,
            )
        )
        endpoint = self._endpoint(path)
        behavior = backend.next_behavior()
        return self._respond(backend, endpoint, behavior, body)

    @staticmethod
    def _endpoint(path: str) -> str:
        if path.endswith("/chat/completions"):
            return "chat"
        if path.endswith("/embeddings"):
            return "embeddings"
        if path.endswith("/messages"):
            return "messages"
        return "unknown"

    # -- behavior execution --------------------------------------------

    def _respond(
        self,
        backend: FarmBackend,
        endpoint: str,
        behavior: dict[str, Any],
        body: Any,
    ) -> httpx.Response:
        kind = behavior.get("behavior", "ok")
        wants_stream = bool(isinstance(body, dict) and body.get("stream"))
        served = (
            model
            if isinstance(body, dict) and (model := body.get("model"))
            else (backend.models[0] if backend.models else "farm-model")
        )

        if kind == "http":
            status = int(behavior.get("status", 500))
            message = behavior.get("message", f"farm scripted HTTP {status}")
            return self._error(backend, status, message)
        if kind == "capacity_body_marker":
            status = int(behavior.get("status", 500))
            marker = behavior.get("marker", "memory pressure")
            return self._error(backend, status, f"farm rejected: {marker}")
        if kind == "bad_json":
            return httpx.Response(
                200,
                content=b"{this is not json",
                headers={"content-type": "application/json"},
            )
        if kind == "midstream_drop":
            frames = self._stream_frames(
                backend, endpoint, served, behavior.get("chunks"), usage_in_final=True
            )
            return httpx.Response(
                200,
                stream=_FarmByteStream(
                    frames, drop_after=int(behavior.get("after_n", 1))
                ),
                headers={"content-type": "text/event-stream"},
            )
        if kind == "ok_stream" or (kind == "ok" and wants_stream):
            frames = self._stream_frames(
                backend,
                endpoint,
                served,
                behavior.get("chunks"),
                usage_in_final=bool(behavior.get("usage_in_final", True)),
            )
            return httpx.Response(
                200,
                stream=_FarmByteStream(frames),
                headers={"content-type": "text/event-stream"},
            )
        if kind == "ok":
            return httpx.Response(200, json=self._ok_body(backend, endpoint, served))
        raise AssertionError(f"farm: unknown behavior {kind!r}")

    def _error(self, backend: FarmBackend, status: int, message: str) -> httpx.Response:
        if backend.api_format == "anthropic":
            payload: dict[str, Any] = {
                "type": "error",
                "error": {"type": "api_error", "message": message},
            }
        else:
            payload = {
                "error": {
                    "message": message,
                    "type": "farm_error",
                    "param": None,
                    "code": None,
                }
            }
        return httpx.Response(status, json=payload, headers=dict(_NO_SDK_RETRY))

    @staticmethod
    def _ok_body(backend: FarmBackend, endpoint: str, served: str) -> dict[str, Any]:
        if endpoint == "embeddings":
            return {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}
                ],
                "model": served,
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            }
        if endpoint == "messages" and backend.api_format == "anthropic":
            return {
                "id": _MSG_ID,
                "type": "message",
                "role": "assistant",
                "model": served,
                "content": [{"type": "text", "text": "farm reply"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 5},
            }
        return {
            "id": _CHAT_ID,
            "object": "chat.completion",
            "created": _FIXED_CREATED,
            "model": served,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "farm reply"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }

    def _stream_frames(
        self,
        backend: FarmBackend,
        endpoint: str,
        served: str,
        chunks: list[str] | None,
        *,
        usage_in_final: bool,
    ) -> list[bytes]:
        texts = chunks if chunks is not None else ["farm ", "reply"]
        if backend.api_format == "anthropic" and endpoint == "messages":
            return self._anthropic_frames(served, texts, usage_in_final=usage_in_final)
        return self._openai_frames(served, texts, usage_in_final=usage_in_final)

    @staticmethod
    def _openai_frames(
        served: str, texts: list[str], *, usage_in_final: bool
    ) -> list[bytes]:
        def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
            return {
                "id": _CHAT_ID,
                "object": "chat.completion.chunk",
                "created": _FIXED_CREATED,
                "model": served,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        frames = [chunk({"role": "assistant", "content": ""})]
        frames += [chunk({"content": t}) for t in texts]
        final = chunk({}, finish="stop")
        if usage_in_final:
            final["usage"] = {
                "prompt_tokens": 3,
                "completion_tokens": 5,
                "total_tokens": 8,
            }
        frames.append(final)
        out = [f"data: {json.dumps(f)}\n\n".encode() for f in frames]
        out.append(b"data: [DONE]\n\n")
        return out

    @staticmethod
    def _anthropic_frames(
        served: str, texts: list[str], *, usage_in_final: bool
    ) -> list[bytes]:
        events: list[tuple[str, dict[str, Any]]] = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": _MSG_ID,
                        "type": "message",
                        "role": "assistant",
                        "model": served,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
        ]
        for t in texts:
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": t},
                    },
                )
            )
        events.append(
            ("content_block_stop", {"type": "content_block_stop", "index": 0})
        )
        message_delta: dict[str, Any] = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        }
        if usage_in_final:
            message_delta["usage"] = {"output_tokens": 5}
        events.append(("message_delta", message_delta))
        events.append(("message_stop", {"type": "message_stop"}))
        return [
            f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
            for name, data in events
        ]
