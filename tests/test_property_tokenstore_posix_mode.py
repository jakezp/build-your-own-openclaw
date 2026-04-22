"""Property 6: POSIX file mode is 0600.

Validates: Requirements 2.5, 6.6, 8.5.

On POSIX platforms, every TokenStore.write() leaves the on-disk file
with mode bits equal to 0o600.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, strategies as st

from mybot.provider.llm.oauth import OAuthCredentials, TokenStore


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only property")
@given(
    access_token=st.text(min_size=1, max_size=50),
    refresh_token=st.text(min_size=1, max_size=50),
    expires_at_sec=st.integers(min_value=0, max_value=10**10),
)
@settings(max_examples=30, deadline=None)
def test_tokenstore_write_sets_posix_mode_0600(
    access_token: str,
    refresh_token: str,
    expires_at_sec: int,
    tmp_path_factory,
) -> None:
    creds = OAuthCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=datetime.fromtimestamp(expires_at_sec, tz=timezone.utc),
    )

    d = tmp_path_factory.mktemp("posix_mode")
    store = TokenStore(d / "creds.json")
    store.write(creds)

    mode = os.stat(store.path).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
