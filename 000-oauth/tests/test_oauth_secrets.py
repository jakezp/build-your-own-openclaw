"""Secrets never surface in error text.

Even when a server maliciously echoes your refresh_token or access_token
back in its response body, the exceptions we raise must never expose
those secrets.

This covers three error legs:

  1. Refresh 400/invalid_grant where the body echoes the refresh_token.
  2. Responses API 500 where the body echoes the access_token.
  3. ``response.error`` SSE event from aggregate_stream — we surface the
     backend's error message but must not fabricate our own leak.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response as _HttpxResponse

from mybot.provider.llm.oauth import (
    ChatGPTOAuth,
    OAuthCredentials,
    TokenStore,
)
from mybot.provider.llm.responses import (
    CHATGPT_RESPONSES_URL,
    ResponsesAPIError,
    ResponsesClient,
    ResponsesRequest,
    SSEEvent,
    aggregate_stream,
)


SECRET = "THIS_IS_A_FAKE_BUT_UNIQUE_SECRET_STRING_40CHARS_X"


def _mock_response(status_code: int, json_body: dict, text_body: str):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = text_body

    def _raise():
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp
            )

    resp.raise_for_status = _raise
    return resp


def _all_exception_text(exc: BaseException) -> str:
    """Collect every piece of text a caller could see from the exception."""
    chunks = [str(exc), repr(exc)]
    cur = exc.__cause__
    while cur is not None:
        chunks.append(str(cur))
        chunks.append(repr(cur))
        cur = cur.__cause__
    cur = exc.__context__
    while cur is not None:
        chunks.append(str(cur))
        chunks.append(repr(cur))
        cur = cur.__context__
    return "\n".join(chunks)


async def test_refresh_invalid_grant_does_not_leak(tmp_path_factory) -> None:
    """A 400 invalid_grant response with secret-echoing body stays safe."""
    tmp = tmp_path_factory.mktemp("leak_400")
    store = TokenStore(tmp / "creds.json")
    creds = OAuthCredentials(
        access_token=f"ACCESS_{SECRET}",
        refresh_token=SECRET,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        account_id="acct-1",
        id_token=None,
    )
    store.write(creds)

    evil_body = SECRET * 10
    evil_json = {"error": "invalid_grant", "debug": SECRET}

    with patch(
        "mybot.provider.llm.oauth.httpx.AsyncClient"
    ) as mock_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post = AsyncMock(
            return_value=_mock_response(400, evil_json, evil_body)
        )
        mock_cls.return_value = client

        oauth = ChatGPTOAuth(store=store)
        try:
            await oauth.access_token()
        except Exception as exc:
            text = _all_exception_text(exc)
            assert SECRET not in text, (
                "secret leaked into refresh error text"
            )
        else:
            raise AssertionError("expected RuntimeError on 400 refresh")


async def test_responses_api_500_does_not_leak_access_token() -> None:
    """Non-2xx response whose body echoes the access_token stays bounded.

    The client truncates body to 500 chars at the raise site. We place
    the secret well past char 500 to confirm the truncation works.
    """
    client = ResponsesClient()
    request = ResponsesRequest(model="m", instructions="i", input=[])
    evil_body = ("x" * 600) + SECRET

    with respx.mock(assert_all_called=True) as mock_router:
        mock_router.post(CHATGPT_RESPONSES_URL).mock(
            return_value=_HttpxResponse(
                500,
                headers={"content-type": "text/plain"},
                content=evil_body.encode(),
            )
        )
        try:
            async for _ in client.stream(
                request, access_token=SECRET, account_id="acct"
            ):
                pass
        except ResponsesAPIError as exc:
            text = "\n".join([str(exc), repr(exc), exc.detail])
            assert SECRET not in text, (
                "access_token leaked into ResponsesAPIError text"
            )
        else:
            raise AssertionError("expected ResponsesAPIError on 500")


async def test_aggregate_stream_response_error_does_not_leak() -> None:
    """The `response.error` SSE event is surfaced without leaking."""

    async def _events():
        yield SSEEvent(
            type="response.output_text.delta", data={"delta": "hi"}
        )
        yield SSEEvent(
            type="response.error",
            data={"error": "backend refused the request"},
        )

    try:
        await aggregate_stream(_events())
    except ResponsesAPIError as exc:
        text = "\n".join([str(exc), repr(exc), exc.detail])
        assert SECRET not in text
    else:
        raise AssertionError("expected ResponsesAPIError")
