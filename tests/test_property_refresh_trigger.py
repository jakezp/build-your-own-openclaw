"""Property 2: Refresh is triggered exactly when needed.

Validates: Requirements 4.1, 8.2.

ChatGPTOAuth.access_token() must hit the token endpoint iff the stored
credentials' expires_at is within the 60s refresh safety margin, i.e.,
``expires_at <= now + 60s``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from hypothesis import given, settings, strategies as st

from mybot.provider.llm.oauth import (
    ChatGPTOAuth,
    OAuthCredentials,
    TokenStore,
)


def _make_mock_response(status_code: int = 200, json_body: dict | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = ""

    def _raise_for_status() -> None:
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp
            )

    resp.raise_for_status = _raise_for_status
    return resp


def _seed_store(store: TokenStore, expires_at: datetime) -> None:
    creds = OAuthCredentials(
        access_token="OLD_ACCESS",
        refresh_token="OLD_REFRESH",
        expires_at=expires_at,
        account_id="acct-1",
        id_token=None,
    )
    store.write(creds)


@given(offset_seconds=st.integers(min_value=-3600, max_value=7200))
@settings(max_examples=30, deadline=None)
async def test_refresh_triggered_iff_within_margin(
    offset_seconds: int, tmp_path_factory
) -> None:
    """mock.post is called iff expires_at <= now + 60s."""
    tmp_path = tmp_path_factory.mktemp("refresh_trigger")
    store = TokenStore(tmp_path / "creds.json")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=offset_seconds)
    _seed_store(store, expires_at)

    # Successful refresh response so access_token() does not crash.
    refresh_payload = {
        "access_token": "NEW_ACCESS",
        "refresh_token": "NEW_REFRESH",
        "expires_in": 3600,
    }

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            return_value=_make_mock_response(200, refresh_payload)
        )
        mock_client_cls.return_value = mock_client

        oauth = ChatGPTOAuth(store=store)
        token = await oauth.access_token()

        expected_refresh = offset_seconds <= 60
        if expected_refresh:
            assert mock_client.post.await_count == 1, (
                f"expected refresh for offset={offset_seconds}s"
            )
            assert token == "NEW_ACCESS"
        else:
            assert mock_client.post.await_count == 0, (
                f"did not expect refresh for offset={offset_seconds}s"
            )
            assert token == "OLD_ACCESS"
