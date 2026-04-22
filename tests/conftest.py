"""pytest root conftest: make 00-chat-loop the canonical step on sys.path."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_STEP_SRC = ROOT / "00-chat-loop" / "src"

# Prepend so our 'mybot' wins against any identically-named package elsewhere
if str(CANONICAL_STEP_SRC) not in sys.path:
    sys.path.insert(0, str(CANONICAL_STEP_SRC))
