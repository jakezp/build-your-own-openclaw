"""Property 14: LLMConfig class body is byte-identical across steps.

Validates: Requirement 6.3.

For every tutorial step ``00-chat-loop`` through ``17-memory`` that
exists, the ``class LLMConfig(BaseModel):`` declaration block in
``src/mybot/utils/config.py`` must be textually identical. The Config
class around it is allowed to vary per step (different outer fields), but
LLMConfig itself is frozen.

Extraction rule: the block starts at the line that matches
``class LLMConfig(BaseModel):`` and runs up to (but not including) the
next top-level ``class `` declaration, or EOF, whichever comes first.

Steps that have not yet received the Task 6 rollout are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

STEP_NAMES = [
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

CANONICAL_STEP = "00-chat-loop"

_CLASS_HEADER = "class LLMConfig(BaseModel):"
_NEXT_CLASS_RE = re.compile(r"^class\s", re.MULTILINE)


def _config_path(step: str) -> Path:
    return ROOT / step / "src" / "mybot" / "utils" / "config.py"


def _extract_llmconfig_block(source: str) -> str | None:
    """Return the LLMConfig class body as a string, or None if absent.

    The block starts at the ``class LLMConfig(BaseModel):`` line and runs
    through the next top-level ``class `` (or EOF).
    """
    start = source.find(_CLASS_HEADER)
    if start == -1:
        return None
    # Find the next top-level "class " that starts on a line BEFORE EOF.
    # We search from position start+1 onwards.
    next_match = _NEXT_CLASS_RE.search(source, start + 1)
    end = next_match.start() if next_match else len(source)
    return source[start:end].rstrip() + "\n"


def test_canonical_step_has_llmconfig_block() -> None:
    path = _config_path(CANONICAL_STEP)
    assert path.exists(), f"canonical config.py missing: {path}"
    block = _extract_llmconfig_block(path.read_text(encoding="utf-8"))
    assert block is not None, "canonical LLMConfig block not found"
    # Sanity: the block must contain the two validators the design pins.
    assert "reject_forbidden_fields" in block
    assert "provider_must_be_openai" in block


@pytest.mark.parametrize("step", STEP_NAMES)
def test_llmconfig_block_matches_canonical_when_present(step: str) -> None:
    """Every step's LLMConfig class body must equal step 00's, when present."""
    path = _config_path(step)
    if not path.exists():
        pytest.skip(f"{step}/src/mybot/utils/config.py not present")

    source = path.read_text(encoding="utf-8")
    block = _extract_llmconfig_block(source)
    if block is None:
        pytest.skip(f"{step} has no LLMConfig block (pre-rollout)")

    canonical_source = _config_path(CANONICAL_STEP).read_text(encoding="utf-8")
    canonical_block = _extract_llmconfig_block(canonical_source)
    assert canonical_block is not None

    assert block == canonical_block, (
        f"{step} LLMConfig class body differs from canonical."
    )
