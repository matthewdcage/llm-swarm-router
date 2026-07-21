"""OpenRouter OAuth PKCE flow — pure functions and local callback server."""

from __future__ import annotations

import base64
import hashlib
import threading
import urllib.request
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from netllm_cli import oauth_pkce


def test_generate_pkce_pair_matches_s256() -> None:
    verifier, challenge = oauth_pkce.generate_pkce_pair()
    assert len(verifier) >= 43
    expected_digest = hashlib.sha256(verifier.encode()).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode()
    assert challenge == expected_challenge
    # No padding, URL-safe alphabet only.
    assert "=" not in verifier
    assert "=" not in challenge


def test_generate_pkce_pair_is_random() -> None:
    v1, c1 = oauth_pkce.generate_pkce_pair()
    v2, c2 = oauth_pkce.generate_pkce_pair()
    assert v1 != v2
    assert c1 != c2


def test_build_authorize_url_includes_required_params() -> None:
    url = oauth_pkce.build_authorize_url(
        callback_url="http://127.0.0.1:54321/callback", code_challenge="abc123"
    )
    assert url.startswith("https://openrouter.ai/auth?")
    assert "callback_url=http%3A%2F%2F127.0.0.1%3A54321%2Fcallback" in url
    assert "code_challenge=abc123" in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_exchange_code_for_key_success() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"key": "sk-or-user-key"}
    mock_client.post = AsyncMock(return_value=mock_response)

    key = await oauth_pkce.exchange_code_for_key(
        "auth-code", "verifier-value", client=mock_client
    )
    assert key == "sk-or-user-key"
    call = mock_client.post.await_args
    assert call.args[0] == oauth_pkce.OPENROUTER_TOKEN_URL
    assert call.kwargs["json"] == {
        "code": "auth-code",
        "code_verifier": "verifier-value",
        "code_challenge_method": "S256",
    }


@pytest.mark.asyncio
async def test_exchange_code_for_key_missing_key_raises() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {}
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(oauth_pkce.PKCEFlowError):
        await oauth_pkce.exchange_code_for_key("code", "verifier", client=mock_client)


def test_local_callback_server_captures_code() -> None:
    port, thread, server = oauth_pkce.start_local_callback_server(timeout_s=5.0)

    def _hit_callback() -> None:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/callback?code=test-auth-code", timeout=3
        )

    hitter = threading.Thread(target=_hit_callback)
    hitter.start()
    hitter.join(timeout=3)

    code = oauth_pkce.wait_for_callback(thread, server, timeout_s=5.0)
    assert code == "test-auth-code"


def test_local_callback_server_captures_error() -> None:
    port, thread, server = oauth_pkce.start_local_callback_server(timeout_s=5.0)

    def _hit_callback() -> None:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/callback?error=access_denied", timeout=3
        )

    hitter = threading.Thread(target=_hit_callback)
    hitter.start()
    hitter.join(timeout=3)

    with pytest.raises(oauth_pkce.PKCEFlowError, match="access_denied"):
        oauth_pkce.wait_for_callback(thread, server, timeout_s=5.0)


def test_wait_for_callback_times_out_when_never_hit() -> None:
    # Server-level timeout outlives the join timeout, so the thread is
    # still genuinely blocked in handle_request() when we give up on it.
    _port, thread, server = oauth_pkce.start_local_callback_server(timeout_s=10.0)
    with pytest.raises(oauth_pkce.PKCEFlowError, match="Timed out"):
        oauth_pkce.wait_for_callback(thread, server, timeout_s=0.3)


def test_open_browser_delegates_to_webbrowser() -> None:
    with patch("netllm_cli.oauth_pkce.webbrowser.open") as mock_open:
        oauth_pkce.open_browser("https://openrouter.ai/auth?x=1")
    mock_open.assert_called_once_with("https://openrouter.ai/auth?x=1")
