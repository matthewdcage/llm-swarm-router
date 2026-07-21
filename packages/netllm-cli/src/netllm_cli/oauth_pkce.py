"""OpenRouter OAuth PKCE flow (the only provider with sanctioned 3rd-party
user auth — https://openrouter.ai/docs/guides/overview/auth/oauth).

Localhost callback on an ephemeral port; official docs call this out as
explicitly supported for CLI tools.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass

import httpx

OPENROUTER_AUTH_URL = "https://openrouter.ai/auth"
OPENROUTER_TOKEN_URL = "https://openrouter.ai/api/v1/auth/keys"
CALLBACK_TIMEOUT_S = 180.0


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(*, callback_url: str, code_challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "callback_url": callback_url,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{OPENROUTER_AUTH_URL}?{query}"


async def exchange_code_for_key(
    code: str, code_verifier: str, *, client: httpx.AsyncClient | None = None
) -> str:
    """POST the authorization code + verifier for a user-controlled API key."""
    payload = {
        "code": code,
        "code_verifier": code_verifier,
        "code_challenge_method": "S256",
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(OPENROUTER_TOKEN_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    key = data.get("key", "")
    if not key:
        raise PKCEFlowError("OpenRouter did not return a key")
    return key


class PKCEFlowError(Exception):
    pass


@dataclass
class _CallbackResult:
    code: str | None = None
    error: str | None = None


class _CallbackServer(http.server.HTTPServer):
    result: _CallbackResult


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib override signature
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        server: _CallbackServer = self.server  # type: ignore[assignment]
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        server.result = _CallbackResult(code=code, error=error)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = (
            b"<html><body><p>netllm: authorization complete. "
            b"You can close this tab.</p></body></html>"
            if code
            else b"<html><body><p>netllm: authorization failed.</p></body></html>"
        )
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silence default stderr access log


def start_local_callback_server(
    *, timeout_s: float = CALLBACK_TIMEOUT_S
) -> tuple[int, threading.Thread, _CallbackServer]:
    """Start a one-shot localhost HTTP server on a free port.

    Returns (port, thread, server). Caller opens the browser, then calls
    `wait_for_callback(thread, server, timeout_s=...)` to block for the
    authorization code.
    """
    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.result = _CallbackResult()
    server.timeout = timeout_s
    port = server.server_address[1]

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return port, thread, server


def wait_for_callback(
    thread: threading.Thread,
    server: _CallbackServer,
    *,
    timeout_s: float = CALLBACK_TIMEOUT_S,
) -> str:
    """Join the callback server thread and return the authorization code."""
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        raise PKCEFlowError("Timed out waiting for OpenRouter authorization callback")
    if server.result.error:
        raise PKCEFlowError(f"OpenRouter authorization failed: {server.result.error}")
    if not server.result.code:
        raise PKCEFlowError("No authorization code received")
    return server.result.code


def open_browser(url: str) -> None:
    webbrowser.open(url)
