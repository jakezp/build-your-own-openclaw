"""Smoke tests for file-level repo invariants.

Each test is a single focused assertion about a concrete file shipped
(or NOT shipped) with the repo:

  * ``default_workspace/config.example.yaml`` contains ``provider``,
    ``model``, ``default_agent``; does NOT contain ``api_key``,
    ``api_base``, or ``auth`` (Req 1.5, 5.6, 7.3).
  * ``default_workspace/models.yaml`` exists and parses as YAML with
    ``allowed`` as a list (Req 1.3).
  * ``PROVIDER_EXAMPLES.md`` is a one-line-ish stub referencing the
    top-level README; does NOT contain a full YAML block with
    credentials (Req 5.4, 7.3).
  * Top-level ``README.md`` title mentions "OAuth Edition" and the
    description mentions "ChatGPT Plus" or "ChatGPT Pro" (Req 5.1).
  * Top-level ``README.md`` has a "Quick Start" section containing
    ``my-bot login`` (Req 5.5).
  * Repo has no residual ``import litellm`` or ``OPENAI_API_KEY``
    references outside ``.kiro/specs/chatgpt-oauth/`` (Req 5.3).
  * Repo-root ``.gitignore`` contains ``**/config.user.yaml`` and
    ``**/config.runtime.yaml`` (Req 5.7).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_EXAMPLE = ROOT / "default_workspace" / "config.example.yaml"
MODELS_YAML = ROOT / "default_workspace" / "models.yaml"
PROVIDER_EXAMPLES = ROOT / "PROVIDER_EXAMPLES.md"
README = ROOT / "README.md"
GITIGNORE = ROOT / ".gitignore"


def _strip_yaml_comments(text: str) -> str:
    """Strip `#` comment lines from a YAML file for content checks."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


# -- config.example.yaml -----------------------------------------------------


def test_config_example_yaml_has_required_oauth_fields() -> None:
    content = CONFIG_EXAMPLE.read_text()
    assert "provider:" in content
    assert "model:" in content
    assert "default_agent:" in content


def test_config_example_yaml_has_no_forbidden_fields_in_payload() -> None:
    """The YAML payload (non-comment lines) must not contain api_key,
    api_base, or auth keys.
    """
    payload_text = _strip_yaml_comments(CONFIG_EXAMPLE.read_text())
    for forbidden in ("api_key", "api_base", "auth:"):
        assert forbidden not in payload_text, (
            f"forbidden field {forbidden!r} found in config.example.yaml"
        )


# -- models.yaml -------------------------------------------------------------


def test_models_yaml_exists_and_has_allowed_list() -> None:
    assert MODELS_YAML.exists(), (
        f"default_workspace/models.yaml must exist (Req 1.3): {MODELS_YAML}"
    )
    data = yaml.safe_load(MODELS_YAML.read_text())
    assert isinstance(data, dict)
    assert "allowed" in data
    assert isinstance(data["allowed"], list)
    assert all(isinstance(x, str) for x in data["allowed"])


# -- PROVIDER_EXAMPLES.md ----------------------------------------------------


def test_provider_examples_is_stub_without_credentials() -> None:
    content = PROVIDER_EXAMPLES.read_text()

    # Stub: must mention README (pointing back there) and should not carry
    # a full provider table.
    assert "README" in content, (
        "PROVIDER_EXAMPLES.md must link back to the top-level README"
    )

    # Must NOT contain YAML snippets with credential fields.
    for forbidden in ("api_key:", "api_base:", "auth:"):
        assert forbidden not in content, (
            f"PROVIDER_EXAMPLES.md still contains {forbidden!r}; it must be "
            f"a stub saying provider configuration is not supported in this "
            f"edition."
        )


# -- README.md ---------------------------------------------------------------


def test_readme_title_mentions_oauth_edition() -> None:
    content = README.read_text()
    # Title is the first ``#`` heading. Match "OAuth Edition" in it.
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    assert match, "README.md missing top-level `#` heading"
    title = match.group(1)
    assert "OAuth Edition" in title, (
        f"README title {title!r} must mention 'OAuth Edition'"
    )


def test_readme_description_mentions_chatgpt_plus_or_pro() -> None:
    content = README.read_text()
    assert "ChatGPT Plus" in content or "ChatGPT Pro" in content, (
        "README must describe the tutorial as targeting ChatGPT Plus/Pro "
        "subscriptions"
    )


def test_readme_has_quick_start_with_my_bot_login() -> None:
    content = README.read_text()
    # Find the Quick Start section heading (any level).
    m = re.search(r"^#+\s+Quick\s+Start", content, re.MULTILINE | re.IGNORECASE)
    assert m, "README missing a 'Quick Start' section"
    # The section body must contain `my-bot login`.
    body = content[m.end():]
    # Cut at the next heading of the same or higher level (heuristic: next
    # ``#`` line). Inspect the first ~3000 chars.
    body_snippet = body[:3000]
    assert "my-bot login" in body_snippet, (
        "Quick Start section must reference `my-bot login`"
    )


# -- litellm / OPENAI_API_KEY purge -----------------------------------------


_CODE_SCAN_ROOTS = [
    "000-oauth",
    "00-chat-loop",
    "01-tools",
    "02-skills",
    "03-persistence",
    "04-slash-commands",
    "05-compaction",
    "06-web-tools",
    "07-event-driven",
    "08-config-hot-reload",
    "09-channels",
    "10-websocket",
    "11-multi-agent-routing",
    "12-cron-heartbeat",
    "13-multi-layer-prompts",
    "14-post-message-back",
    "15-agent-dispatch",
    "16-concurrency-control",
    "17-memory",
]


def _scan_for_term(term: str, extensions: tuple[str, ...]) -> list[Path]:
    """Return paths under each step's src/ that contain ``term``."""
    hits: list[Path] = []
    for step in _CODE_SCAN_ROOTS:
        src = ROOT / step / "src"
        if not src.exists():
            continue
        for path in src.rglob("*"):
            if path.is_dir():
                continue
            if path.suffix not in extensions:
                continue
            # Skip compiled / cache artifacts.
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if term in text:
                hits.append(path)
    return hits


def test_no_import_litellm_in_step_source() -> None:
    """No step's src/ may contain an actual `import litellm` or
    `from litellm ... import` statement at the start of a line.

    Docstrings and comments that mention "litellm" in prose are permitted
    (they're historical context, not code dependencies).
    """
    import re as _re

    hits: list[Path] = []
    for p in _scan_for_term("litellm", (".py",)):
        text = p.read_text(encoding="utf-8", errors="replace")
        if _re.search(r"^(\s*)(import\s+litellm|from\s+litellm[\s\.])", text, _re.MULTILINE):
            hits.append(p)
    assert not hits, (
        "files still import litellm: "
        + ", ".join(str(p.relative_to(ROOT)) for p in hits)
    )


def test_no_openai_api_key_env_read_in_step_source() -> None:
    """No step's src/ may read the OPENAI_API_KEY env variable."""
    hits = _scan_for_term("OPENAI_API_KEY", (".py",))
    assert not hits, (
        "files still reference OPENAI_API_KEY: "
        + ", ".join(str(p.relative_to(ROOT)) for p in hits)
    )


# -- .gitignore --------------------------------------------------------------


def test_gitignore_covers_config_user_and_runtime() -> None:
    content = GITIGNORE.read_text()
    assert "**/config.user.yaml" in content, (
        ".gitignore must contain `**/config.user.yaml`"
    )
    assert "**/config.runtime.yaml" in content, (
        ".gitignore must contain `**/config.runtime.yaml`"
    )


def test_gitignore_does_not_ignore_models_yaml() -> None:
    """default_workspace/models.yaml must be checked in."""
    content = GITIGNORE.read_text()
    # Any obvious pattern that would match models.yaml.
    bad = [
        "default_workspace/models.yaml",
        "models.yaml",
        "**/models.yaml",
    ]
    for pat in bad:
        assert pat not in content, (
            f".gitignore must NOT ignore {pat!r} — models.yaml is "
            f"checked in as part of the default workspace."
        )
