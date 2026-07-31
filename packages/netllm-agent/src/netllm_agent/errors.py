"""Per-surface HTTP error envelopes (F-38).

FastAPI's default renders every HTTPException as ``{"detail": "..."}``,
which no OpenAI or Anthropic client understands. Handlers installed here
key on the request path:

- ``/v1/messages*`` — Anthropic shape:
  ``{"type": "error", "error": {"type": ..., "message": ...}}``
- other ``/v1/*`` — OpenAI shape:
  ``{"error": {"message": ..., "type": ..., "param": ..., "code": ...}}``
- everything else (``/netllm/*``, ``/health``, ...) keeps FastAPI's
  ``{"detail": ...}``.

Status codes are preserved exactly as raised — routes that forward
upstream statuses (429/401/404) keep doing so; only the body is shaped.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

_ANTHROPIC_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    529: "overloaded_error",
}

_OPENAI_ERROR_TYPES = {
    400: ("invalid_request_error", None),
    401: ("authentication_error", None),
    403: ("permission_error", None),
    404: ("invalid_request_error", "not_found"),
    405: ("invalid_request_error", "method_not_allowed"),
    413: ("invalid_request_error", "request_too_large"),
    429: ("rate_limit_error", "rate_limit_exceeded"),
}


def anthropic_error_body(status_code: int, message: str) -> dict[str, object]:
    default = "api_error" if status_code >= 500 else "invalid_request_error"
    return {
        "type": "error",
        "error": {
            "type": _ANTHROPIC_ERROR_TYPES.get(status_code, default),
            "message": message,
        },
    }


def openai_error_body(status_code: int, message: str) -> dict[str, object]:
    default = (
        ("api_error", None) if status_code >= 500 else ("invalid_request_error", None)
    )
    err_type, code = _OPENAI_ERROR_TYPES.get(status_code, default)
    return {
        "error": {
            "message": message,
            "type": err_type,
            "param": None,
            "code": code,
        }
    }


def _is_messages_path(path: str) -> bool:
    return path == "/v1/messages" or path.startswith("/v1/messages/")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _surface_shaped_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        path = request.url.path
        if not path.startswith("/v1/"):
            return await http_exception_handler(request, exc)
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        if _is_messages_path(path):
            body = anthropic_error_body(exc.status_code, message)
        else:
            body = openai_error_body(exc.status_code, message)
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)
