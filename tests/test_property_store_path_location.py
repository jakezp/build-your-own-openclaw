"""Property 12: Token_Store path is outside the repo.

Validates: Requirement 7.1.

``TokenStore.default_path()`` must never resolve inside any step
directory (``NN-*``) or ``default_workspace/``, regardless of how the
environment variables that drive the resolver (``HOME``,
``XDG_CONFIG_HOME``, ``APPDATA``) are configured.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from mybot.provider.llm.oauth import TokenStore


REPO_ROOT = Path(__file__).resolve().parent.parent
_STEP_DIR_RE = re.compile(r"^\d\d-.*$")


def _forbidden_subtrees() -> list[Path]:
    """The directories the token store must NOT live inside."""
    forbidden: list[Path] = []
    for child in REPO_ROOT.iterdir():
        if not child.is_dir():
            continue
        if _STEP_DIR_RE.match(child.name) or child.name == "default_workspace":
            forbidden.append(child.resolve())
    return forbidden


@given(
    home=st.sampled_from(["/home/test", "/tmp/testhome", "/var/empty"]),
    xdg=st.one_of(st.none(), st.sampled_from(["/tmp/xdg", "/etc/xdg"])),
    appdata=st.one_of(
        st.none(),
        st.sampled_from(["C:/Users/test/AppData", "D:/appdata"]),
    ),
)
@settings(max_examples=20, deadline=None)
def test_default_path_outside_repo(
    home: str,
    xdg: str | None,
    appdata: str | None,
) -> None:
    """default_path() never resolves inside any NN-* or default_workspace dir."""
    forbidden = _forbidden_subtrees()

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", home)

        if xdg is None:
            mp.delenv("XDG_CONFIG_HOME", raising=False)
        else:
            mp.setenv("XDG_CONFIG_HOME", xdg)

        if appdata is None:
            mp.delenv("APPDATA", raising=False)
        else:
            mp.setenv("APPDATA", appdata)

        path = TokenStore.default_path()

    # Resolve to an absolute path without requiring the file to exist.
    resolved = Path(os.path.abspath(str(path)))
    for forbidden_dir in forbidden:
        assert not str(resolved).startswith(str(forbidden_dir) + os.sep), (
            f"default_path {resolved} leaked under forbidden dir "
            f"{forbidden_dir}"
        )
        assert resolved != forbidden_dir, (
            f"default_path {resolved} equals forbidden dir "
            f"{forbidden_dir}"
        )
