"""Property 10: Login failures never write a partial Token_Store.

Validates: Requirements 2.3, 2.4.

When ``login()`` fails because of either
  (a) an OAuth ``state`` mismatch, or
  (b) a non-2xx token-exchange response,
the existing Token_Store bytes on disk are byte-identical after the
failure.

The browser + loopback path is driven without a real browser by patching
``http.server.ThreadingHTTPServer``, ``webbrowser.open``, and
``httpx.post`` at module scope. The fake server captures the outer
``Handler`` class and, when ``serve_forever()`` is called, delivers a
synthetic callback that mutates the ``received`` dict the handler writes
to and sets the ``done`` event.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, strategies as st

from mybot.provider.llm import oauth as oauth_mod
from mybot.provider.llm.oauth import (
    ChatGPTOAuth,
    OAuthCredentials,
    TokenStore,
)


def _seed_store(store: TokenStore) -> bytes:
    """Seed a Token_Store with valid prior credentials. Return its bytes."""
    creds = OAuthCredentials(
        access_token="PRIOR_ACCESS",
        refresh_token="PRIOR_REFRESH",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        account_id="acct-prior",
        id_token=None,
    )
    store.write(creds)
    return store.path.read_bytes()


class _FakeHandlerShim:
    """Minimal stand-in for a BaseHTTPRequestHandler instance.

    The real Handler.do_GET uses only ``self_.path``, ``self_.send_response``,
    ``self_.send_header``, ``self_.end_headers``, and ``self_.wfile.write``.
    We supply no-ops for the response side and a ``path`` attribute for the
    request side.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.wfile = MagicMock()

    def send_response(self, code: int) -> None:  # noqa: D401
        pass

    def send_header(self, *args, **kwargs) -> None:  # noqa: D401
        pass

    def end_headers(self) -> None:  # noqa: D401
        pass


def _make_fake_server_cls(callback_path: str):
    """Build a fake ThreadingHTTPServer class that drives a synthetic GET.

    When ``serve_forever()`` is invoked, the fake server constructs a
    shim request object with ``path = callback_path`` and calls the real
    Handler's ``do_GET`` against it. The real ``do_GET`` parses the
    query string, writes into the closure-local ``received`` dict, and
    sets the closure-local ``done`` event -- all exactly as production
    code does.
    """

    class FakeServer:
        server_address = ("127.0.0.1", 12345)

        def __init__(self, addr, handler_cls):
            self._handler_cls = handler_cls

        def serve_forever(self):
            shim = _FakeHandlerShim(callback_path)
            # Call do_GET directly without running __init__ (which would
            # try to read/write a socket).
            self._handler_cls.do_GET(shim)

        def shutdown(self):
            pass

    return FakeServer


def _patch_login_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    callback_path: str,
    token_post_status: int,
    token_post_body: str = "",
    token_post_json: dict | None = None,
) -> MagicMock:
    """Patch webbrowser, ThreadingHTTPServer, threading.Thread, and httpx.post.

    The thread patch replaces the background ``serve_forever`` thread
    with a synchronous call so the callback is delivered before
    ``done.wait()`` runs in the main thread.
    """
    # Silence the browser open.
    monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)

    # Fake server class.
    fake_server_cls = _make_fake_server_cls(callback_path)
    monkeypatch.setattr(
        oauth_mod.http.server, "ThreadingHTTPServer", fake_server_cls
    )

    # Run "the background serve_forever thread" synchronously so that
    # by the time login() reaches `done.wait(...)`, the handler has
    # already set `done` and populated `received`.
    class SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(oauth_mod.threading, "Thread", SyncThread)

    # Fake the token-exchange HTTP call.
    mock_resp = MagicMock()
    mock_resp.status_code = token_post_status
    mock_resp.text = token_post_body
    mock_resp.json = MagicMock(return_value=(token_post_json or {}))
    post_mock = MagicMock(return_value=mock_resp)
    monkeypatch.setattr(oauth_mod.httpx, "post", post_mock)
    return post_mock


def test_login_state_mismatch_preserves_store(
    tmp_path_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State-mismatch login leaves the Token_Store byte-identical."""
    tmp_path = tmp_path_factory.mktemp("login_state_mismatch")
    store = TokenStore(tmp_path / "creds.json")
    prior_bytes = _seed_store(store)

    # Force state mismatch: the callback delivers a forged state that
    # cannot match the random state login() generated internally.
    post_mock = _patch_login_deps(
        monkeypatch,
        callback_path="/auth/callback?code=fake_code&state=WRONG_STATE_VALUE",
        token_post_status=200,  # irrelevant: state check runs first
        token_post_json={},
    )

    oauth = ChatGPTOAuth(store=store)
    with pytest.raises(RuntimeError, match="state mismatch"):
        oauth.login()

    # Token_Store must be byte-identical.
    assert store.path.read_bytes() == prior_bytes
    # The token exchange must NOT have been attempted.
    assert post_mock.call_count == 0


@given(status_code=st.sampled_from([400, 401, 403, 500, 503]))
@settings(max_examples=30, deadline=None)
def test_login_token_exchange_non2xx_preserves_store(
    status_code: int,
    tmp_path_factory,
) -> None:
    """A non-2xx token exchange leaves the Token_Store byte-identical."""
    import base64
    import secrets as _secrets_mod

    tmp_path = tmp_path_factory.mktemp("login_token_exchange")
    store = TokenStore(tmp_path / "creds.json")
    prior_bytes = _seed_store(store)

    # Hypothesis generates inputs repeatedly, so use a scoped MonkeyPatch
    # context that is torn down after each example.
    with pytest.MonkeyPatch.context() as monkeypatch:
        # Force the login()'s state to a deterministic value so the
        # forged callback can match. login() calls secrets.token_bytes
        # twice: (1) code_verifier, (2) state. Intercept the 2nd call.
        state_bytes = b"\x01" * 32
        calls = {"n": 0}
        real_token_bytes = _secrets_mod.token_bytes

        def fake_token_bytes(n: int) -> bytes:
            calls["n"] += 1
            if calls["n"] == 2:
                return state_bytes
            return real_token_bytes(n)

        monkeypatch.setattr(
            oauth_mod.secrets, "token_bytes", fake_token_bytes
        )

        expected_state = (
            base64.urlsafe_b64encode(state_bytes).rstrip(b"=").decode()
        )
        callback_path = f"/auth/callback?code=ok_code&state={expected_state}"

        post_mock = _patch_login_deps(
            monkeypatch,
            callback_path=callback_path,
            token_post_status=status_code,
            token_post_body="opaque body; contents intentionally bounded",
            token_post_json={},
        )

        oauth = ChatGPTOAuth(store=store)
        with pytest.raises(RuntimeError, match="token exchange failed"):
            oauth.login()

        # Token_Store must be byte-identical.
        assert store.path.read_bytes() == prior_bytes
        # The token exchange WAS attempted (but the response was non-2xx).
        assert post_mock.call_count == 1
