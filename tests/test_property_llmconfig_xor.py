"""Property 1: LLMConfig rejects forbidden fields and unknown models.

Validates: Requirements 1.1, 1.2, 1.4, 8.1, 8.9.

In the OAuth Edition, ``LLMConfig``/``Config.model_validate`` succeeds iff:

1. ``provider == "openai"``,
2. ``llm.model`` is in the allowlist loaded from ``workspace/models.yaml``
   (or matches one of its glob patterns), AND
3. none of ``api_key``, ``api_base``, ``auth`` is present.

This is the new Property 1, replacing the pre-pivot "exactly one
credential source" (api_key XOR chatgpt_oauth) property.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import ValidationError

from mybot.utils.config import Config, LLMConfig


_FIXED_ALLOWED = ["gpt-5.4", "gpt-5.2", "gpt-5.2-codex"]
_FIXED_PATTERNS = ["*codex*"]


@pytest.fixture
def fixed_workspace(tmp_path: Path) -> Path:
    """Workspace dir with a ``models.yaml`` using a fixed allowlist."""
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump({"allowed": _FIXED_ALLOWED, "patterns": _FIXED_PATTERNS})
    )
    return tmp_path


def _model_is_accepted(model: str) -> bool:
    import fnmatch

    if model in _FIXED_ALLOWED:
        return True
    return any(fnmatch.fnmatchcase(model, p) for p in _FIXED_PATTERNS)


# Hypothesis strategy: random dicts with provider / model plus each forbidden
# field independently present or absent.
_FORBIDDEN_KEYS = ["api_key", "api_base", "auth"]


kwargs_strategy = st.fixed_dictionaries(
    {
        "provider": st.sampled_from(["openai", "anthropic", "grok", ""]),
        "model": st.one_of(
            st.sampled_from(_FIXED_ALLOWED),
            # Some unknown ids.
            st.sampled_from(["gpt-nonsense", "random-model", ""]),
            # Some *codex* pattern-matchers.
            st.sampled_from(["my-codex-exp", "codex-v9"]),
        ),
        "api_key": st.one_of(st.none(), st.just("sk-xxx")),
        "api_base": st.one_of(st.none(), st.just("https://x.example")),
        "auth": st.one_of(st.none(), st.just("chatgpt_oauth")),
    }
)


@given(kwargs=kwargs_strategy)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_config_validates_iff_all_three_conditions_hold(
    kwargs: dict, fixed_workspace: Path
) -> None:
    """Config.model_validate succeeds iff provider==openai AND model
    accepted AND no forbidden field present.
    """
    provider = kwargs["provider"]
    model = kwargs["model"]
    llm_block: dict = {"provider": provider, "model": model}
    for k in _FORBIDDEN_KEYS:
        if kwargs[k] is not None:
            llm_block[k] = kwargs[k]

    has_forbidden = any(k in llm_block for k in _FORBIDDEN_KEYS)
    provider_ok = provider == "openai"
    model_ok = _model_is_accepted(model)

    should_accept = provider_ok and model_ok and not has_forbidden

    payload = {
        "workspace": fixed_workspace,
        "llm": llm_block,
        "default_agent": "assistant",
    }

    if should_accept:
        cfg = Config.model_validate(payload)
        assert cfg.llm.provider == "openai"
        assert cfg.llm.model == model
    else:
        with pytest.raises(ValidationError):
            Config.model_validate(payload)


def test_forbidden_api_key_is_rejected_at_llmconfig_level() -> None:
    """LLMConfig alone (no Config wrapper) must reject api_key."""
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig(provider="openai", model="gpt-5.2", api_key="sk-x")
    assert "api_key" in str(exc_info.value)


def test_forbidden_api_base_is_rejected_at_llmconfig_level() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig(
            provider="openai", model="gpt-5.2", api_base="https://x.example"
        )
    assert "api_base" in str(exc_info.value)


def test_forbidden_auth_is_rejected_at_llmconfig_level() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig(provider="openai", model="gpt-5.2", auth="chatgpt_oauth")
    assert "auth" in str(exc_info.value)


def test_non_openai_provider_rejected_at_llmconfig_level() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig(provider="anthropic", model="gpt-5.2")
    assert "openai" in str(exc_info.value)


def test_unknown_model_rejected_at_config_level(
    fixed_workspace: Path,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Config.model_validate(
            {
                "workspace": fixed_workspace,
                "llm": {"provider": "openai", "model": "gpt-nonsense"},
                "default_agent": "assistant",
            }
        )
    msg = str(exc_info.value)
    assert "gpt-nonsense" in msg
    assert "models.yaml" in msg


def test_adding_a_new_model_to_yaml_makes_it_accepted(
    tmp_path: Path,
) -> None:
    """Req 8.9: Editing models.yaml alone is sufficient to accept a new id.

    No Python source change needed — write a new allowlist with the id,
    then validate. Remove it and the same payload is rejected.
    """
    # Start with allowlist that excludes the target id.
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump({"allowed": ["gpt-5.4"], "patterns": []})
    )
    payload = {
        "workspace": tmp_path,
        "llm": {"provider": "openai", "model": "gpt-5.99-future"},
        "default_agent": "assistant",
    }

    with pytest.raises(ValidationError):
        Config.model_validate(payload)

    # Now add the id to the yaml.
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump({"allowed": ["gpt-5.4", "gpt-5.99-future"], "patterns": []})
    )
    # Same payload validates.
    cfg = Config.model_validate(payload)
    assert cfg.llm.model == "gpt-5.99-future"
