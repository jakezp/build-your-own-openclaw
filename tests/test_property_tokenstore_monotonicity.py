"""Property 3: Token_Store never regresses.

Validates: Requirements 4.2, 4.4, 4.5, 8.3.

After a refresh attempt, the on-disk Token_Store is either:
  - byte-identical to its prior state (all failure cases), OR
  - contains credentials with strictly greater ``expires_at`` (success).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from hypothesis import given, settings, strategies as st

from mybot.provider.llm.oauth import (
    ChatGPTOAuth,
    OAuthCredentials,
    TokenStore,
)


def _make_mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    text_body: str = "",
):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text_body

    def _raise_for_status() -> None:
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp
            )

    resp.raise_for_status = _raise_for_status
    return resp


def _seed_store(store: TokenStore) -> OAuthCredentials:
    """Seed a store with creds that need refresh (expire in 30s)."""
    creds = OAuthCredentials(
        access_token="OLD_ACCESS",
        refresh_token="OLD_REFRESH",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        account_id="acct-1",
        id_token=None,
    )
    store.write(creds)
    return creds


@given(outcome=st.sampled_from(
    ["success", "invalid_grant", "transient_5xx", "connect_error"]
))
@settings(max_examples=30, deadline=None)
async def test_tokenstore_monotonic_under_refresh_outcomes(
    outcome: str, tmp_path_factory
) -> None:
    tmp_path = tmp_path_factory.mktemp("monotonicity")
    store = TokenStore(tmp_path / "creds.json")
    prior_creds = _seed_store(store)
    prior_bytes = store.path.read_bytes()

    # Configure the mock per outcome.
    post_mock: AsyncMock
    if outcome == "success":
        post_mock = AsyncMock(
            return_value=_make_mock_response(
                200,
                {
                    "access_token": "NEW_ACCESS",
                    "refresh_token": "NEW_REFRESH",
                    "expires_in": 3600,
                },
            )
        )
    elif outcome == "invalid_grant":
        post_mock = AsyncMock(
            return_value=_make_mock_response(
                400, {"error": "invalid_grant"}
            )
        )
    elif outcome == "transient_5xx":
        post_mock = AsyncMock(
            return_value=_make_mock_response(
                503, None, "service unavailable"
            )
        )
    elif outcome == "connect_error":
        post_mock = AsyncMock(side_effect=httpx.ConnectError("fake"))
    else:
        raise AssertionError(f"unknown outcome {outcome}")

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = post_mock
        mock_client_cls.return_value = mock_client

        oauth = ChatGPTOAuth(store=store)

        if outcome == "success":
            token = await oauth.access_token()
            assert token == "NEW_ACCESS"
            # Strictly later expires_at.
            new_creds = store.read()
            assert new_creds.expires_at > prior_creds.expires_at
            # Bytes must have changed.
            assert store.path.read_bytes() != prior_bytes
        else:
            with pytest.raises(Exception):
                await oauth.access_token()
            # Byte-identical to prior.
            assert store.path.read_bytes() == prior_bytes
