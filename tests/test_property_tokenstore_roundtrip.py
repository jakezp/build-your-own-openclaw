"""Property 5: Token_Store round-trip.

Validates: Requirement 8.4.

For all valid OAuthCredentials instances, writing then reading the
TokenStore yields a value equal to the original.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given, settings, strategies as st

from mybot.provider.llm.oauth import OAuthCredentials, TokenStore


@given(
    access_token=st.text(min_size=1, max_size=100),
    refresh_token=st.text(min_size=1, max_size=100),
    expires_at_sec=st.integers(min_value=0, max_value=10**10),
    account_id=st.one_of(st.none(), st.text(min_size=1, max_size=40)),
    id_token=st.one_of(st.none(), st.text(min_size=1, max_size=400)),
)
@settings(max_examples=50, deadline=None)
def test_tokenstore_roundtrip(
    access_token: str,
    refresh_token: str,
    expires_at_sec: int,
    account_id: str | None,
    id_token: str | None,
    tmp_path_factory,
) -> None:
    expires_at = datetime.fromtimestamp(expires_at_sec, tz=timezone.utc)
    original = OAuthCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        account_id=account_id,
        id_token=id_token,
    )

    d = tmp_path_factory.mktemp("roundtrip")
    store = TokenStore(d / "creds.json")
    store.write(original)
    recovered = store.read()

    assert recovered == original
