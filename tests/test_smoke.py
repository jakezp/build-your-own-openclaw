"""Smoke test: prove the sys.path wiring and the oauth module import."""


def test_oauth_module_imports() -> None:
    from mybot.provider.llm import oauth

    # Confirm the key symbols exist.
    assert hasattr(oauth, "ChatGPTOAuth")
    assert hasattr(oauth, "OAuthCredentials")
    assert hasattr(oauth, "TokenStore")
    assert oauth.CHATGPT_API_BASE.startswith("https://")
