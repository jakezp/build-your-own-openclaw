"""Integration-style tests for the ChatGPT OAuth login flow.

Validates: Requirements 2.1, 2.6.

A successful ``ChatGPTOAuth.login()`` must
  * write the Token_Store to disk,
  * return a ``LoginResult`` whose ``token_store_path`` matches the
    store's path and whose ``account_id`` is extracted from the fake
    id_token JWT,
  * persist credentials with the correct access/refresh/id_token/account
    fields readable by ``TokenStore.read()``,
  * set file mode ``0600`` on POSIX.

The test drives the PKCE login end to end without opening a real
browser: it patches ``http.server.ThreadingHTTPServer``,
``threading.Thread``, ``httpx.post``, and ``secrets.token_bytes`` using
the same pattern established in ``test_property_login_failure_invariance.py``.
"""

from __future__ import annotations

import base64
import json as _json
import os
import secrets as _secrets_mod
from unittest.mock import MagicMock

import pytest

from mybot.provider.llm import oauth as oauth_mod
from mybot.provider.llm.oauth import ChatGPTOAuth, TokenStore


class _FakeHandlerShim:
    """Stand-in BaseHTTPRequestHandler instance.

    Mirrors the shim from test_property_login_failure_invariance.py.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.wfile = MagicMock()

    def send_response(self, code: int) -> None:
        pass

    def send_header(self, *args, **kwargs) -> None:
        pass

    def end_headers(self) -> None:
        pass


def _make_fake_server_cls(callback_path: str):
    """Build a fake ThreadingHTTPServer that delivers a synthetic callback."""

    class FakeServer:
        server_address = ("127.0.0.1", 12345)

        def __init__(self, addr, handler_cls):
            self._handler_cls = handler_cls

        def serve_forever(self):
            shim = _FakeHandlerShim(callback_path)
            self._handler_cls.do_GET(shim)

        def shutdown(self):
            pass

    return FakeServer


def _build_fake_id_token(account_id: str) -> str:
    """Build an unsigned fake id_token JWT with the required claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id}
    }
    payload_b64 = (
        base64.urlsafe_b64encode(_json.dumps(payload).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload_b64}.signature"


def test_successful_login_writes_store_and_returns_account_id(
    tmp_path_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """login() persists creds and surfaces the account id from id_token."""
    tmp_path = tmp_path_factory.mktemp("login_success")
    store_path = tmp_path / "creds.json"
    store = TokenStore(store_path)

    # Precondition: store must not exist yet.
    assert not store.exists()

    # Patch secrets.token_bytes so the state value is deterministic; this
    # lets the fake callback deliver a matching state.
    state_bytes = b"\x02" * 32
    calls = {"n": 0}
    real_token_bytes = _secrets_mod.token_bytes

    def fake_token_bytes(n: int) -> bytes:
        calls["n"] += 1
        # login() calls token_bytes twice: (1) code_verifier, (2) state.
        if calls["n"] == 2:
            return state_bytes
        return real_token_bytes(n)

    monkeypatch.setattr(oauth_mod.secrets, "token_bytes", fake_token_bytes)

    expected_state = (
        base64.urlsafe_b64encode(state_bytes).rstrip(b"=").decode()
    )
    callback_path = f"/auth/callback?code=SUCCESS_CODE&state={expected_state}"

    # Silence the browser open.
    monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)

    # Fake ThreadingHTTPServer.
    fake_server_cls = _make_fake_server_cls(callback_path)
    monkeypatch.setattr(
        oauth_mod.http.server, "ThreadingHTTPServer", fake_server_cls
    )

    # Run the "background thread" synchronously.
    class SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(oauth_mod.threading, "Thread", SyncThread)

    # Build a fake id_token with the account claim.
    fake_id_token = _build_fake_id_token("acct_integration_42")

    # Fake httpx.post for the token exchange.
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json = MagicMock(
        return_value={
            "access_token": "ACCESS_123",
            "refresh_token": "REFRESH_123",
            "expires_in": 3600,
            "id_token": fake_id_token,
        }
    )
    post_mock = MagicMock(return_value=mock_resp)
    monkeypatch.setattr(oauth_mod.httpx, "post", post_mock)

    # Exercise login().
    oauth = ChatGPTOAuth(store=store)
    result = oauth.login()

    # Result shape.
    assert result.token_store_path == store.path
    assert result.account_id == "acct_integration_42"

    # Store written.
    assert store.exists()
    creds = store.read()
    assert creds.access_token == "ACCESS_123"
    assert creds.refresh_token == "REFRESH_123"
    assert creds.id_token == fake_id_token
    assert creds.account_id == "acct_integration_42"

    # Token exchange was attempted exactly once.
    assert post_mock.call_count == 1

    # POSIX mode check (skip on Windows).
    if os.name == "posix":
        mode = os.stat(store.path).st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"



# --- Responses API end-to-end mocked chat() test ----------------------------


import respx
from httpx import Response as _HttpxResponse

from mybot.provider.llm.base import LLMProvider
from mybot.provider.llm.responses import CHATGPT_RESPONSES_URL


@pytest.mark.asyncio
async def test_login_then_chat_end_to_end_mocked(
    tmp_path_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Login populates the Token_Store; chat() hits the mocked Responses
    API and returns the expected aggregated text.

    Exercises: login flow → Token_Store written → LLMProvider.chat uses
    the stored access_token + account_id → SSE aggregation.
    """
    tmp_path = tmp_path_factory.mktemp("e2e_chat")
    store_path = tmp_path / "creds.json"
    store = TokenStore(store_path)

    # -- Stub the login flow (same pattern as the test above) ------------
    state_bytes = b"\x03" * 32
    calls = {"n": 0}
    real_token_bytes = _secrets_mod.token_bytes

    def fake_token_bytes(n: int) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            return state_bytes
        return real_token_bytes(n)

    monkeypatch.setattr(oauth_mod.secrets, "token_bytes", fake_token_bytes)

    expected_state = (
        base64.urlsafe_b64encode(state_bytes).rstrip(b"=").decode()
    )
    callback_path = f"/auth/callback?code=CODE_E2E&state={expected_state}"

    monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(
        oauth_mod.http.server,
        "ThreadingHTTPServer",
        _make_fake_server_cls(callback_path),
    )

    class SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(oauth_mod.threading, "Thread", SyncThread)

    fake_id_token = _build_fake_id_token("acct_e2e_99")
    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.text = ""
    token_resp.json = MagicMock(
        return_value={
            "access_token": "ACCESS_E2E",
            "refresh_token": "REFRESH_E2E",
            "expires_in": 3600,
            "id_token": fake_id_token,
        }
    )
    monkeypatch.setattr(
        oauth_mod.httpx, "post", MagicMock(return_value=token_resp)
    )

    # -- Run login() ------------------------------------------------------
    ChatGPTOAuth(store=store).login()
    assert store.exists()
    creds = store.read()
    assert creds.access_token == "ACCESS_E2E"
    assert creds.account_id == "acct_e2e_99"

    # -- Run chat() against a mocked Responses API ------------------------
    provider = LLMProvider(model="gpt-5.2")
    # Point the OAuth at our temp store so chat() picks up the creds we
    # just wrote.
    provider._oauth = ChatGPTOAuth(store=store)

    canned_sse = (
        "event: response.output_text.delta\n"
        'data: {"delta":"hel"}\n'
        "\n"
        "event: response.output_text.delta\n"
        'data: {"delta":"lo"}\n'
        "\n"
        "event: response.output_text.done\n"
        'data: {"text":"hello"}\n'
        "\n"
    )

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(CHATGPT_RESPONSES_URL).mock(
            return_value=_HttpxResponse(
                200,
                headers={"content-type": "text/event-stream"},
                content=canned_sse.encode("utf-8"),
            )
        )
        reply = await provider.chat(
            [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hi"},
            ]
        )

    assert reply == "hello"
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer ACCESS_E2E"
    assert req.headers["chatgpt-account-id"] == "acct_e2e_99"
