"""Property 15: ``my-bot login`` is available from every step.

Validates: Requirement 6.5.

For every tutorial step ``00-chat-loop`` through ``17-memory`` that has
a ``cli/main.py``, the file must reference both

  * a ``login`` function definition (``def login(``), AND
  * the ``ChatGPTOAuth()`` constructor.

Together these are a structural signal that the ``login`` subcommand
is wired up through the OAuth orchestrator.

Steps without ``cli/main.py`` are skipped.
"""

from __future__ import annotations

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


def _main_path(step: str) -> Path:
    return ROOT / step / "src" / "mybot" / "cli" / "main.py"


@pytest.mark.parametrize("step", STEP_NAMES)
def test_login_subcommand_exists(step: str) -> None:
    path = _main_path(step)
    if not path.exists():
        pytest.skip(f"{step}/src/mybot/cli/main.py not present")

    source = path.read_text(encoding="utf-8")
    assert "def login(" in source, (
        f"{step}/cli/main.py missing `def login(` definition"
    )
    assert "ChatGPTOAuth()" in source, (
        f"{step}/cli/main.py missing `ChatGPTOAuth()` constructor"
    )
