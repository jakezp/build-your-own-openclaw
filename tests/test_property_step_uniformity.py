"""Property 13: Shared modules are byte-identical across steps.

Validates: Requirements 6.1, 6.2, 8.8.

For every tutorial step ``00-chat-loop`` through ``17-memory`` that
exists, the SHA-256 of

  * ``src/mybot/provider/llm/oauth.py``
  * ``src/mybot/provider/llm/responses.py``

must be identical across all present steps. This test is partially
green (only step 00) until the Task 6 rollout completes, and fully
green after Task 6.5.

Steps that have not yet received the copy are SKIPPED (not failed) so
this test keeps the canonical step's invariants honest without blocking
the rollout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

STEP_NAMES = [
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

CANONICAL_STEP = "00-chat-loop"

SHARED_FILES = [
    ("src", "mybot", "provider", "llm", "oauth.py"),
    ("src", "mybot", "provider", "llm", "responses.py"),
]


def _path(step: str, parts: tuple[str, ...]) -> Path:
    return ROOT / step / Path(*parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("parts", SHARED_FILES, ids=lambda p: p[-1])
def test_canonical_step_has_shared_file(parts: tuple[str, ...]) -> None:
    """Step 00 must always have the shared file (sets the canonical hash)."""
    path = _path(CANONICAL_STEP, parts)
    assert path.exists(), f"canonical shared file missing: {path}"
    assert _sha256(path)  # well-defined


@pytest.mark.parametrize("step", STEP_NAMES)
@pytest.mark.parametrize("parts", SHARED_FILES, ids=lambda p: p[-1])
def test_shared_file_matches_canonical_when_present(
    step: str, parts: tuple[str, ...]
) -> None:
    """Every step that has the shared file must match step 00's hash.

    Steps that have not yet received the Task 6 rollout are skipped.
    """
    path = _path(step, parts)
    if not path.exists():
        pytest.skip(f"{step}/{Path(*parts)} not yet rolled out")

    canonical = _path(CANONICAL_STEP, parts)
    assert canonical.exists(), f"canonical missing: {canonical}"

    expected = _sha256(canonical)
    actual = _sha256(path)
    assert actual == expected, (
        f"{path} SHA-256 {actual} does not match canonical {expected}"
    )
