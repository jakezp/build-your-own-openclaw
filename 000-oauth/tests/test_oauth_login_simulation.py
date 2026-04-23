"""End-to-end login flow, mocked.

Drives ``ChatGPTOAuth.login()`` without opening a browser or hitting the
network. Validates that a successful login:

  * writes a Token_Store at the configured path,
  * populates all five expected fields,
  * extracts the account_id from the id_token JWT's namespaced claim,
  * sets POSIX file mode 0600 on supported platforms.

This is the test that proves the full OAuth chain works end-to-end from
the user's perspective.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as _secrets_mod
from unittest.mock import MagicMock

import pytest

from mybot.provider.llm import oauth as oauth_mod
from mybot.provider.llm.oauth import ChatGPTOAuth, TokenStore


# ---- helpers -------------------------------------------------------------


class _FakeHandlerShim:
    """Stand-in BaseHTTPRequestHandler instance.

    The real login Handler.do_GET only reads ``self.path`` and writes
    to ``self.wfile``; we provide no-ops for those so we can invoke
    do_GET directly without running __init__ (which expects a socket).
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
    """Fake ThreadingHTTPServer that delivers a synthetic callback."""

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
    """Unsigned JWT payload carrying the namespaced account claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id}
    }
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload_b64}.fake-signature"


# ---- tests ---------------------------------------------------------------


def test_successful_login_populates_token_store(
    tmp_path_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete login flow writes a valid Token_Store with all fields."""

    tmp = tmp_path_factory.mktemp("login")
    store = TokenStore(tmp / "creds.json")
    assert not store.exists(), "precondition: store must not exist yet"

    # Force the state value to something deterministic so the fake
    # callback can match it. login() calls secrets.token_bytes twice:
    # (1) for the PKCE code_verifier, (2) for the state parameter.
    # We only override the second call.
    state_bytes = b"\x02" * 32
    real_token_bytes = _secrets_mod.token_bytes
    calls = {"n": 0}

    def fake_token_bytes(n: int) -> bytes:
        calls["n"] += 1
        return state_bytes if calls["n"] == 2 else real_token_bytes(n)

    monkeypatch.setattr(oauth_mod.secrets, "token_bytes", fake_token_bytes)

    expected_state = (
        base64.urlsafe_b64encode(state_bytes).rstrip(b"=").decode()
    )
    callback_path = f"/auth/callback?code=TEST_CODE&state={expected_state}"

    # Silence the browser open.
    monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)

    # Fake the callback listener.
    monkeypatch.setattr(
        oauth_mod.http.server,
        "ThreadingHTTPServer",
        _make_fake_server_cls(callback_path),
    )

    # Run the server's serve_forever() synchronously so the callback
    # fires before login() blocks on `done.wait(...)`.
    class SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(oauth_mod.threading, "Thread", SyncThread)

    # Fake the token-exchange HTTP call.
    fake_id_token = _build_fake_id_token("acct_test_42")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json = MagicMock(
        return_value={
            "access_token": "ACCESS_TEST",
            "refresh_token": "REFRESH_TEST",
            "expires_in": 3600,
            "id_token": fake_id_token,
        }
    )
    monkeypatch.setattr(
        oauth_mod.httpx, "post", MagicMock(return_value=mock_resp)
    )

    # Exercise login().
    oauth = ChatGPTOAuth(store=store)
    result = oauth.login()

    # The LoginResult should carry the account_id we embedded in the
    # id_token, and point at the store we gave it.
    assert result.token_store_path == store.path
    assert result.account_id == "acct_test_42"

    # The Token_Store file now exists and parses.
    assert store.exists()
    creds = store.read()

    # All five fields populated.
    assert creds.access_token == "ACCESS_TEST"
    assert creds.refresh_token == "REFRESH_TEST"
    assert creds.id_token == fake_id_token
    assert creds.account_id == "acct_test_42"
    assert creds.expires_at is not None

    # POSIX mode: 0600.
    if os.name == "posix":
        mode = os.stat(store.path).st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
