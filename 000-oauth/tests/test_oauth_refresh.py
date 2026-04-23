"""Token-refresh invariants.

Four properties validated here:

  1. **Trigger**: ``access_token()`` calls the token endpoint iff the
     stored credentials are within 60 seconds of expiry.

  2. **Success**: a 2xx refresh atomically replaces the Token_Store with
     credentials whose ``expires_at`` is strictly later.

  3. **Failure-invariance**: on 400/401/5xx, the Token_Store is
     byte-identical to its pre-refresh state (we never leave a partial
     write).

  4. **Refresh-token continuity**: if the server returns a new
     refresh_token, we persist it; if the server omits it, we keep the
     prior one. Never empty.

These are the behaviors the refresh loop was designed to guarantee. If
any of them regressed, the tutorial's "long-session refresh" story would
break.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mybot.provider.llm.oauth import (
    ChatGPTOAuth,
    OAuthCredentials,
    TokenStore,
)


# ---- helpers --------------------------------------------------------------


def _mock_response(status_code: int = 200, body: dict | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    resp.text = ""

    def _raise():
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp
            )

    resp.raise_for_status = _raise
    return resp


def _seed_store(store: TokenStore, *, offset_seconds: int) -> None:
    """Write credentials whose access_token expires at now+offset."""
    creds = OAuthCredentials(
        access_token="OLD_ACCESS",
        refresh_token="OLD_REFRESH",
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=offset_seconds),
        account_id="acct-test",
        id_token=None,
    )
    store.write(creds)


# ---- 1: refresh trigger -------------------------------------------------


@pytest.mark.parametrize(
    "offset_seconds,expected_refresh",
    [
        (-120, True),   # already expired
        (30, True),     # inside margin
        (60, True),     # right on the boundary (<=)
        (61, False),    # just past the margin
        (3600, False),  # fresh
    ],
)
async def test_refresh_triggers_iff_inside_margin(
    offset_seconds: int,
    expected_refresh: bool,
    tmp_path_factory,
) -> None:
    """needs_refresh() is True iff expires_at <= now + 60s."""
    tmp = tmp_path_factory.mktemp("trigger")
    store = TokenStore(tmp / "creds.json")
    _seed_store(store, offset_seconds=offset_seconds)

    ok_payload = {
        "access_token": "NEW_ACCESS",
        "refresh_token": "NEW_REFRESH",
        "expires_in": 3600,
    }

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post = AsyncMock(return_value=_mock_response(200, ok_payload))
        mock_cls.return_value = client

        oauth = ChatGPTOAuth(store=store)
        token = await oauth.access_token()

        if expected_refresh:
            assert client.post.await_count == 1
            assert token == "NEW_ACCESS"
        else:
            assert client.post.await_count == 0
            assert token == "OLD_ACCESS"


# ---- 2+3: success advances expires_at, failure preserves bytes ---------


async def test_success_advances_expires_at(tmp_path_factory) -> None:
    tmp = tmp_path_factory.mktemp("success")
    store = TokenStore(tmp / "creds.json")
    _seed_store(store, offset_seconds=30)

    prior = store.read()
    prior_bytes = store.path.read_bytes()

    payload = {
        "access_token": "NEW_ACCESS",
        "refresh_token": "NEW_REFRESH",
        "expires_in": 3600,
    }
    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post = AsyncMock(return_value=_mock_response(200, payload))
        mock_cls.return_value = client

        oauth = ChatGPTOAuth(store=store)
        token = await oauth.access_token()

    assert token == "NEW_ACCESS"
    new = store.read()
    assert new.expires_at > prior.expires_at
    assert store.path.read_bytes() != prior_bytes


@pytest.mark.parametrize(
    "status,body",
    [
        (400, {"error": "invalid_grant"}),
        (401, {"error": "invalid_grant"}),
        (503, {"error": "unavailable"}),
    ],
)
async def test_failure_preserves_token_store(
    status: int, body: dict, tmp_path_factory
) -> None:
    tmp = tmp_path_factory.mktemp("fail")
    store = TokenStore(tmp / "creds.json")
    _seed_store(store, offset_seconds=30)

    prior_bytes = store.path.read_bytes()

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post = AsyncMock(return_value=_mock_response(status, body))
        mock_cls.return_value = client

        oauth = ChatGPTOAuth(store=store)
        with pytest.raises(Exception):
            await oauth.access_token()

    assert store.path.read_bytes() == prior_bytes, (
        "a failed refresh must leave the Token_Store byte-identical"
    )


# ---- 4: refresh-token continuity ---------------------------------------


@pytest.mark.parametrize(
    "new_refresh",
    [None, "ROTATED_REFRESH"],
)
async def test_refresh_token_continuity(
    new_refresh: str | None, tmp_path_factory
) -> None:
    tmp = tmp_path_factory.mktemp("continuity")
    store = TokenStore(tmp / "creds.json")
    _seed_store(store, offset_seconds=30)

    payload = {"access_token": "NEW_ACCESS", "expires_in": 3600}
    if new_refresh is not None:
        payload["refresh_token"] = new_refresh

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post = AsyncMock(return_value=_mock_response(200, payload))
        mock_cls.return_value = client

        oauth = ChatGPTOAuth(store=store)
        await oauth.access_token()

    on_disk = store.read()
    assert on_disk.refresh_token != "", "refresh_token must never be empty"
    if new_refresh:
        assert on_disk.refresh_token == new_refresh
    else:
        assert on_disk.refresh_token == "OLD_REFRESH"
