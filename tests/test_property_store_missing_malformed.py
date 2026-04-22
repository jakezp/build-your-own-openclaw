"""Property 9: Missing / malformed Token_Store produces actionable errors.

Validates: Requirements 3.1, 3.2.

When the Token_Store is
  (a) missing,
  (b) present but contains non-JSON bytes, or
  (c) present but contains a JSON dict missing required fields,
then ``ChatGPTOAuth.access_token()`` must raise a ``RuntimeError`` whose
message contains ``my-bot login`` (and, for malformed cases, the store
path).
"""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given, settings, strategies as st

from mybot.provider.llm.oauth import ChatGPTOAuth, TokenStore


REQUIRED_FIELDS = {"access_token", "refresh_token", "expires_at"}


async def test_missing_store_raises(tmp_path_factory) -> None:
    """A non-existent Token_Store triggers a RuntimeError pointing to login."""
    tmp_path = tmp_path_factory.mktemp("missing_store")
    store = TokenStore(tmp_path / "no-such-file.json")
    oauth = ChatGPTOAuth(store=store)

    with pytest.raises(RuntimeError) as exc_info:
        await oauth.access_token()

    msg = str(exc_info.value)
    assert "my-bot login" in msg
    assert str(store.path) in msg


@given(random_bytes=st.binary(min_size=1, max_size=200))
@settings(max_examples=30, deadline=None)
async def test_non_json_bytes_raise(
    random_bytes: bytes, tmp_path_factory
) -> None:
    """Random bytes that can't be parsed as creds raise with actionable text."""
    tmp_path = tmp_path_factory.mktemp("non_json")
    store_path = tmp_path / "creds.json"
    store_path.write_bytes(random_bytes)
    store = TokenStore(store_path)

    oauth = ChatGPTOAuth(store=store)
    with pytest.raises(RuntimeError) as exc_info:
        await oauth.access_token()

    msg = str(exc_info.value)
    assert "my-bot login" in msg
    assert str(store.path) in msg


@given(
    partial_dict=st.dictionaries(
        keys=st.sampled_from(
            ["access_token", "refresh_token", "expires_at", "foo"]
        ),
        values=st.one_of(st.text(max_size=20), st.integers()),
        max_size=3,
    )
)
@settings(max_examples=30, deadline=None)
async def test_missing_fields_json_raise(
    partial_dict: dict, tmp_path_factory
) -> None:
    """JSON dicts missing required fields raise with actionable text."""
    # Only consider dicts that are missing at least one required field.
    assume(not REQUIRED_FIELDS.issubset(partial_dict.keys()))

    tmp_path = tmp_path_factory.mktemp("missing_fields")
    store_path = tmp_path / "creds.json"
    store_path.write_text(json.dumps(partial_dict), encoding="utf-8")
    store = TokenStore(store_path)

    oauth = ChatGPTOAuth(store=store)
    with pytest.raises(RuntimeError) as exc_info:
        await oauth.access_token()

    msg = str(exc_info.value)
    assert "my-bot login" in msg
    assert str(store.path) in msg
