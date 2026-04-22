"""Property 7: Responses-API request shape is correct.

Validates: Requirements 3.4, 8.6.

For every valid ``LLMConfig`` + valid ``OAuthCredentials`` + arbitrary
``messages``, the request ``LLMProvider.chat()`` POSTs must have:

  * URL == ``https://chatgpt.com/backend-api/codex/responses``
  * Authorization header == ``Bearer <access_token>``
  * ``ChatGPT-Account-Id`` header == ``<account_id>``
  * ``originator`` header == ``codex_cli_rs``
  * ``Accept`` header == ``text/event-stream``
  * body["stream"] is True
  * body["store"] is False
  * body["model"] == config.model
  * body["instructions"] is a non-empty string
  * body["input"] is a list (may be empty)

A separate direct-client test covers the ``tools`` field shape (Responses
API flat form: no nested ``function`` key) using ``ResponsesRequest`` —
step 00's ``chat()`` does not accept tools (Task 6 rolls the tool-aware
variant to steps 01–17).

We use ``respx`` to intercept the POST and feed back a canned SSE body
ending in ``response.output_text.done``. Hypothesis generates the
message shapes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
from hypothesis import HealthCheck, given, settings, strategies as st
from httpx import Response

from mybot.provider.llm.base import LLMProvider, _translate_tools
from mybot.provider.llm.oauth import ChatGPTOAuth, OAuthCredentials, TokenStore
from mybot.provider.llm.responses import (
    CHATGPT_RESPONSES_URL,
    ResponsesRequest,
)


# --- strategies -------------------------------------------------------------

_ACCEPTED_MODELS = ["gpt-5.4", "gpt-5.2", "gpt-5.2-codex", "my-codex-exp"]

_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    min_size=1,
    max_size=40,
)

# Strict ASCII, no whitespace/control — matches real OAuth tokens and
# account ids, and passes HTTP header validation.
_ascii_token = st.text(
    alphabet=(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789-_.~"
    ),
    min_size=1,
    max_size=40,
)


def _message_strategies() -> st.SearchStrategy[list[dict]]:
    """Random Chat-Completions message lists with a mix of roles."""
    system_msg = st.fixed_dictionaries(
        {"role": st.just("system"), "content": _text}
    )
    user_msg = st.fixed_dictionaries(
        {"role": st.just("user"), "content": _text}
    )
    assistant_plain = st.fixed_dictionaries(
        {"role": st.just("assistant"), "content": _text}
    )
    assistant_with_tool_calls = st.fixed_dictionaries(
        {
            "role": st.just("assistant"),
            "content": st.just(""),
            "tool_calls": st.lists(
                st.fixed_dictionaries(
                    {
                        "id": _text,
                        "type": st.just("function"),
                        "function": st.fixed_dictionaries(
                            {
                                "name": _text,
                                "arguments": st.just('{"x":1}'),
                            }
                        ),
                    }
                ),
                min_size=1,
                max_size=2,
            ),
        }
    )
    tool_msg = st.fixed_dictionaries(
        {
            "role": st.just("tool"),
            "tool_call_id": _text,
            "content": _text,
        }
    )
    tail = st.lists(
        st.one_of(user_msg, assistant_plain, assistant_with_tool_calls, tool_msg),
        min_size=1,
        max_size=5,
    )
    return st.lists(system_msg, min_size=0, max_size=1).flatmap(
        lambda sys_list: tail.map(lambda t: sys_list + t)
    )


# A canned, minimal SSE body ending with a done event.
_CANNED_SSE = (
    "event: response.output_text.delta\n"
    'data: {"delta":"hi"}\n'
    "\n"
    "event: response.output_text.done\n"
    'data: {"text":"hi"}\n'
    "\n"
)


def _seed_store(store: TokenStore, access_token: str, account_id: str) -> None:
    """Seed a Token_Store with credentials that do not need refresh."""
    creds = OAuthCredentials(
        access_token=access_token,
        refresh_token="irrelevant",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        account_id=account_id,
        id_token=None,
    )
    store.write(creds)


@pytest.mark.asyncio
@given(
    model=st.sampled_from(_ACCEPTED_MODELS),
    access_token=_ascii_token,
    account_id=_ascii_token,
    messages=_message_strategies(),
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_responses_api_request_shape(
    model: str,
    access_token: str,
    account_id: str,
    messages: list[dict],
    tmp_path_factory,
) -> None:
    """Every LLMProvider.chat() POST matches the pinned request shape."""
    tmp_path = tmp_path_factory.mktemp("p7")
    store = TokenStore(tmp_path / "creds.json")
    _seed_store(store, access_token, account_id)

    provider = LLMProvider(model=model)
    provider._oauth = ChatGPTOAuth(store=store)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(CHATGPT_RESPONSES_URL).mock(
            return_value=Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_CANNED_SSE.encode("utf-8"),
            )
        )
        result = await provider.chat(messages)

    assert result == "hi"
    assert route.called
    captured = route.calls.last.request

    assert str(captured.url) == CHATGPT_RESPONSES_URL

    headers = captured.headers
    assert headers["authorization"] == f"Bearer {access_token}"
    assert headers["chatgpt-account-id"] == account_id
    assert headers["originator"] == "codex_cli_rs"
    assert headers["accept"] == "text/event-stream"
    assert headers["content-type"] == "application/json"

    body = json.loads(captured.content)
    assert body["stream"] is True
    assert body["store"] is False
    assert body["model"] == model
    assert isinstance(body["instructions"], str)
    assert body["instructions"] != ""
    assert isinstance(body["input"], list)

    # Step 00's chat() does not accept tools, so the body must never
    # carry a ``tools`` field.
    assert "tools" not in body


# -- tool-schema translation shape (tested on the helper directly) ----------

_TOOL_SCHEMAS = st.lists(
    st.fixed_dictionaries(
        {
            "type": st.just("function"),
            "function": st.fixed_dictionaries(
                {
                    "name": _text,
                    "description": _text,
                    "parameters": st.just({"type": "object", "properties": {}}),
                }
            ),
        }
    ),
    min_size=1,
    max_size=3,
)


@given(tools=_TOOL_SCHEMAS)
@settings(max_examples=30, deadline=None)
def test_translate_tools_to_responses_shape(tools: list[dict]) -> None:
    """``_translate_tools`` flattens Chat-Completions tool schema to
    Responses API shape (no nested ``function`` key).
    """
    out = _translate_tools(tools)
    assert out is not None
    for t in out:
        assert t["type"] == "function"
        assert "function" not in t
        assert "name" in t
        assert "description" in t
        assert "parameters" in t


def test_translate_tools_none_and_empty_return_none() -> None:
    assert _translate_tools(None) is None
    assert _translate_tools([]) is None


def test_request_body_with_tools_shape() -> None:
    """ResponsesRequest.to_body() includes ``tools`` iff present, and the
    Responses-API flat shape is preserved.
    """
    req_no_tools = ResponsesRequest(
        model="m", instructions="i", input=[]
    )
    assert "tools" not in req_no_tools.to_body()

    flat_tools = [
        {
            "type": "function",
            "name": "t",
            "description": "d",
            "parameters": {"type": "object"},
        }
    ]
    req_with_tools = ResponsesRequest(
        model="m", instructions="i", input=[], tools=flat_tools
    )
    body = req_with_tools.to_body()
    assert body["tools"] == flat_tools
