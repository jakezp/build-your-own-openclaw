"""Property 4: Refresh-token continuity.

Validates: Requirement 4.3.

After a successful refresh, the on-disk refresh_token is:
  - the server-returned value, if present and non-empty, OR
  - the prior value, if the server omitted refresh_token.
It is never empty.
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


def _seed_store_needing_refresh(store: TokenStore) -> None:
    creds = OAuthCredentials(
        access_token="OLD_ACCESS",
        refresh_token="OLD_REFRESH_TOKEN",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        account_id="acct-1",
        id_token=None,
    )
    store.write(creds)


@given(
    new_refresh=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
)
@settings(max_examples=50, deadline=None)
async def test_refresh_token_continuity(
    new_refresh: str | None, tmp_path_factory
) -> None:
    tmp_path = tmp_path_factory.mktemp("continuity")
    store = TokenStore(tmp_path / "creds.json")
    _seed_store_needing_refresh(store)

    payload: dict = {
        "access_token": "NEW_ACCESS",
        "expires_in": 3600,
    }
    if new_refresh is not None:
        payload["refresh_token"] = new_refresh

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            return_value=_make_mock_response(200, payload)
        )
        mock_client_cls.return_value = mock_client

        oauth = ChatGPTOAuth(store=store)
        await oauth.access_token()

    on_disk = store.read()

    # Never empty.
    assert on_disk.refresh_token != ""

    if new_refresh:
        # Non-empty server value wins.
        assert on_disk.refresh_token == new_refresh
    else:
        # Omitted or empty: keep prior.
        assert on_disk.refresh_token == "OLD_REFRESH_TOKEN"
