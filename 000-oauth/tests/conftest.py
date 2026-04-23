"""Make this step's `mybot` importable — but only when pytest was
invoked from within this step.

This conftest is carefully scoped so it doesn't shadow the canonical
step's `mybot` when the main `tests/` suite runs in the same invocation.
The check: only prepend to sys.path if ``000-oauth`` is the rootdir
pytest decided on.

Run standalone from this directory:

    uv sync
    uv run pytest tests/

Or from the repo root:

    uv run --directory 000-oauth pytest tests/

Do NOT mix this directory's tests/ with the main repo-root tests/ in a
single pytest invocation — two conftests racing to prepend sys.path
will produce confusing ModuleNotFoundError messages.
"""

import sys
from pathlib import Path

STEP_ROOT = Path(__file__).resolve().parent.parent
STEP_SRC = STEP_ROOT / "src"

# Prepend only if this step is the test invocation's root. This keeps
# us from shadowing 00-chat-loop's richer `mybot` during mixed runs.
import os
_cwd = Path(os.getcwd()).resolve()
if _cwd == STEP_ROOT or STEP_ROOT in _cwd.parents:
    if str(STEP_SRC) not in sys.path:
        sys.path.insert(0, str(STEP_SRC))
