#!/usr/bin/env python3
"""Roll out the OAuth Edition changes to steps 01-17.

Applies the six-item per-step checklist from Task 6:
  1. Copy `responses.py` byte-identically from step 00.
  2. Copy `oauth.py` byte-identically from step 00.
  3. Rewrite `LLMConfig` + helpers byte-identically in `utils/config.py`.
  4. Insert `check_model_allowlist` into that step's `Config`.
  5. Rewrite `LLMProvider` (tool-aware variant).
  6. Drop `litellm` from `pyproject.toml`.

Also:
  - Replace `from litellm.types.completion import ... as Message` with
    `Message = dict[str, Any]` alias wherever present.

Run: uv run --directory 00-chat-loop python ../scripts/rollout_oauth_edition.py
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "00-chat-loop"

STEPS = [
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


# ---------------------------------------------------------------------------
# Canonical LLMConfig block (byte-identical across all 18 steps).
# ---------------------------------------------------------------------------

CANONICAL_LLMCONFIG_SECTION = '''\
import fnmatch
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


def _load_models_yaml(workspace: Path) -> tuple[set[str], list[str]]:
    """Return (allowed_ids, glob_patterns) from workspace/models.yaml."""
    path = workspace / "models.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Model allowlist not found at {path}. Copy the one from "
            f"default_workspace/models.yaml into your workspace, or edit it "
            f"to add a newly-released model id."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    allowed = set(data.get("allowed") or [])
    patterns = list(data.get("patterns") or [])
    return allowed, patterns


def _model_is_allowed(model: str, allowed: set[str], patterns: list[str]) -> bool:
    if model in allowed:
        return True
    return any(fnmatch.fnmatchcase(model, p) for p in patterns)


_FORBIDDEN_LLM_FIELDS = frozenset({"api_key", "api_base", "auth"})


class LLMConfig(BaseModel):
    """LLM configuration for the OAuth Edition.

    Only `provider` and `model` are accepted. Credentials live entirely in
    the Token_Store populated by `my-bot login`.
    """

    provider: str
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)

    model_config = {"extra": "allow"}  # we trap forbidden fields below

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_fields(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        present = _FORBIDDEN_LLM_FIELDS.intersection(values.keys())
        if present:
            fields = ", ".join(sorted(present))
            raise ValueError(
                f"llm config contains forbidden field(s): {fields}. "
                f"The OAuth Edition does not accept api_key/api_base/auth. "
                f"Remove them from config.user.yaml and run `my-bot login` "
                f"once to authenticate."
            )
        return values

    @model_validator(mode="after")
    def provider_must_be_openai(self) -> "LLMConfig":
        if self.provider != "openai":
            raise ValueError(
                f"llm.provider must be 'openai' in the OAuth Edition "
                f"(got {self.provider!r}). See README for background."
            )
        return self
'''


CHECK_MODEL_ALLOWLIST_SNIPPET = '''\
    @model_validator(mode="after")
    def check_model_allowlist(self) -> "Config":
        allowed, patterns = _load_models_yaml(self.workspace)
        if not _model_is_allowed(self.llm.model, allowed, patterns):
            raise ValueError(
                f"llm.model '{self.llm.model}' is not accepted by the ChatGPT "
                f"subscription backend. Currently accepted: "
                f"{sorted(allowed)} plus patterns {patterns}. "
                f"Add the new id to {self.workspace / 'models.yaml'}."
            )
        return self

'''


# Tool-aware LLMProvider for steps 01-17.
TOOL_AWARE_BASE_PY = '''\
"""Responses-API-backed LLM provider (tool-aware variant)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from mybot.provider.llm.oauth import ChatGPTOAuth
from mybot.provider.llm.responses import (
    ResponsesClient,
    ResponsesRequest,
    aggregate_stream,
)

if TYPE_CHECKING:
    from mybot.utils.config import LLMConfig


@dataclass
class LLMToolCall:
    """A tool/function call from the LLM."""

    id: str
    name: str
    arguments: str  # JSON string


def _translate_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split Chat-Completions messages into (instructions, Responses input).

    The Responses API takes a top-level ``instructions`` string plus an
    ``input`` list of role/content items and function_call / function_call_output
    items. Chat-Completions packs the system prompt as the first message and
    encodes tool calls inline on assistant messages, so we translate:

    * ``role: system`` messages → concatenated into ``instructions``.
    * ``role: assistant`` with ``tool_calls`` → one plain assistant message
      (if content non-empty) plus one ``function_call`` item per tool call.
    * ``role: tool`` → ``function_call_output`` item keyed by ``tool_call_id``.
    * Everything else (user, assistant without tool calls) → ``{role, content}``.
    """
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            content = m.get("content") or ""
            if isinstance(content, str):
                instructions_parts.append(content)
            continue
        if role == "assistant" and m.get("tool_calls"):
            content = m.get("content") or ""
            if content:
                input_items.append({"role": "assistant", "content": content})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", ""),
                    }
                )
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": m.get("content", ""),
                }
            )
            continue
        # user / assistant-without-tool-calls
        input_items.append(
            {
                "role": role or "user",
                "content": m.get("content") or "",
            }
        )
    instructions = (
        "\\n\\n".join(p for p in instructions_parts if p)
        or "You are a helpful assistant."
    )
    return instructions, input_items


def _translate_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Chat-Completions tool schema → Responses API tool schema.

    Chat-Completions nests the function definition under a ``function`` key;
    Responses flattens it. Schemas already in Responses shape (no ``function``
    key) pass through untouched.
    """
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            out.append(
                {
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        else:
            out.append(t)
    return out


class LLMProvider:
    """Responses-API client for the ChatGPT subscription backend."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._settings = kwargs
        self._oauth = ChatGPTOAuth()
        self._client = ResponsesClient()

    @classmethod
    def from_config(cls, config: "LLMConfig") -> "LLMProvider":
        return cls(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    async def _resolve_credential(self) -> tuple[str, str]:
        """Return (access_token, account_id) from the shared Token_Store."""
        token = await self._oauth.access_token()
        account_id = await self._oauth.account_id()
        return token, account_id

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> tuple[str, list[LLMToolCall]]:
        """Send a chat turn to the Responses API and return (content, tool_calls)."""
        access_token, account_id = await self._resolve_credential()
        instructions, input_items = _translate_messages(messages)
        resp_tools = _translate_tools(tools)
        request = ResponsesRequest(
            model=self.model,
            instructions=instructions,
            input=input_items,
            tools=resp_tools,
        )
        events = self._client.stream(
            request,
            access_token=access_token,
            account_id=account_id,
        )
        aggregated = await aggregate_stream(events)
        return (
            aggregated.content,
            [
                LLMToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
                for tc in aggregated.tool_calls
            ],
        )
'''


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, flush=True)


def copy_shared_files(step_dir: Path) -> None:
    """Copy oauth.py and responses.py byte-identically from step 00."""
    for fname in ("oauth.py", "responses.py"):
        src = CANONICAL / "src" / "mybot" / "provider" / "llm" / fname
        dst = step_dir / "src" / "mybot" / "provider" / "llm" / fname
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        # Preserve bytes exactly — no line-ending normalization.
        assert dst.read_bytes() == src.read_bytes()


def rewrite_config_py(step_dir: Path) -> None:
    """Replace imports/helpers/LLMConfig in config.py without disturbing
    auxiliary classes or third-party imports that may live between or
    around the LLMConfig section.

    Strategy:
      1. Replace ONLY the `class LLMConfig(BaseModel):` block with the
         canonical version.
      2. Inject `_load_models_yaml`, `_model_is_allowed`, and
         `_FORBIDDEN_LLM_FIELDS` as module-level helpers if missing.
      3. Ensure the top-of-file `from typing import ...` has `Any`;
         ensure `import fnmatch` is present if the helpers were injected.
      4. Delete the old `api_base_must_be_url` and
         `exactly_one_credential_source` pre-pivot validators if they
         survived a prior rollout attempt (idempotence).
      5. Insert `check_model_allowlist` into the `Config` class if absent.

    This preserves every unrelated import (``watchdog``,
    ``BraveWebSearchConfig``, etc.) and every auxiliary class.
    """
    path = step_dir / "src" / "mybot" / "utils" / "config.py"
    source = path.read_text(encoding="utf-8")

    # ---- Step 1: replace LLMConfig class block with canonical -----------

    m_llm = re.search(r"^class\s+LLMConfig\(BaseModel\):", source, re.MULTILINE)
    if not m_llm:
        raise RuntimeError(f"{path}: no `class LLMConfig(BaseModel):` found")
    llm_start = m_llm.start()
    next_class = re.search(r"\nclass\s+\w+\(", source[llm_start + 1 :])
    llm_end = (
        llm_start + 1 + next_class.start() + 1 if next_class else len(source)
    )

    canonical_class = _CANONICAL_LLMCONFIG_CLASS.strip() + "\n\n\n"
    source = source[:llm_start] + canonical_class + source[llm_end:]

    # ---- Step 2: inject helpers if missing ------------------------------

    if "_load_models_yaml" not in source:
        # Insert right before the `class LLMConfig(BaseModel):` declaration
        # and after the last import line (after the final blank line of the
        # import block).
        helpers_block = _HELPERS_BLOCK.rstrip() + "\n\n\n"
        m2 = re.search(r"^class\s+LLMConfig\(BaseModel\):", source, re.MULTILINE)
        pos = m2.start() if m2 else 0
        source = source[:pos] + helpers_block + source[pos:]

    # ---- Step 3: ensure typing / fnmatch imports ------------------------

    if "import fnmatch" not in source:
        # Add `import fnmatch` near the top, before `from pathlib import`.
        source = re.sub(
            r"^(from pathlib import Path)",
            r"import fnmatch\n\1",
            source,
            count=1,
            flags=re.MULTILINE,
        )

    # Ensure `from typing import` includes `Any`.
    m3 = re.search(r"^from typing import ([^\n]+)$", source, re.MULTILINE)
    if m3:
        existing = [s.strip() for s in m3.group(1).split(",")]
        if "Any" not in existing:
            existing.insert(0, "Any")
            new_line = "from typing import " + ", ".join(existing)
            source = source.replace(m3.group(0), new_line, 1)
    else:
        # No `from typing import` — add one.
        source = re.sub(
            r"^(from pathlib import Path)",
            r"\1\nfrom typing import Any",
            source,
            count=1,
            flags=re.MULTILINE,
        )

    # Ensure pydantic import has BaseModel, Field, model_validator (drop
    # field_validator if no longer referenced).
    m4 = re.search(r"^from pydantic import ([^\n]+)$", source, re.MULTILINE)
    if m4:
        current = [s.strip() for s in m4.group(1).split(",")]
        # Required by canonical LLMConfig.
        for sym in ("BaseModel", "Field", "model_validator"):
            if sym not in current:
                current.append(sym)
        # Drop field_validator if not referenced.
        if "field_validator" in current and "@field_validator" not in source:
            current.remove("field_validator")
        new_line = "from pydantic import " + ", ".join(current)
        source = source.replace(m4.group(0), new_line, 1)

    # ---- Step 4: insert check_model_allowlist in Config -----------------

    if "check_model_allowlist" not in source:
        insert_match = re.search(
            r"(\n    @classmethod\n    def load\()", source
        )
        if insert_match:
            insert_pos = insert_match.start() + 1
            source = (
                source[:insert_pos]
                + CHECK_MODEL_ALLOWLIST_SNIPPET
                + source[insert_pos:]
            )
        else:
            raise RuntimeError(
                f"{path}: no `def load(` insertion point for check_model_allowlist"
            )

    path.write_text(source, encoding="utf-8")


_HELPERS_BLOCK = '''\
def _load_models_yaml(workspace: Path) -> tuple[set[str], list[str]]:
    """Return (allowed_ids, glob_patterns) from workspace/models.yaml."""
    path = workspace / "models.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Model allowlist not found at {path}. Copy the one from "
            f"default_workspace/models.yaml into your workspace, or edit it "
            f"to add a newly-released model id."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    allowed = set(data.get("allowed") or [])
    patterns = list(data.get("patterns") or [])
    return allowed, patterns


def _model_is_allowed(model: str, allowed: set[str], patterns: list[str]) -> bool:
    if model in allowed:
        return True
    return any(fnmatch.fnmatchcase(model, p) for p in patterns)


_FORBIDDEN_LLM_FIELDS = frozenset({"api_key", "api_base", "auth"})
'''


_CANONICAL_LLMCONFIG_CLASS = '''\
class LLMConfig(BaseModel):
    """LLM configuration for the OAuth Edition.

    Only `provider` and `model` are accepted. Credentials live entirely in
    the Token_Store populated by `my-bot login`.
    """

    provider: str
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)

    model_config = {"extra": "allow"}  # we trap forbidden fields below

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_fields(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        present = _FORBIDDEN_LLM_FIELDS.intersection(values.keys())
        if present:
            fields = ", ".join(sorted(present))
            raise ValueError(
                f"llm config contains forbidden field(s): {fields}. "
                f"The OAuth Edition does not accept api_key/api_base/auth. "
                f"Remove them from config.user.yaml and run `my-bot login` "
                f"once to authenticate."
            )
        return values

    @model_validator(mode="after")
    def provider_must_be_openai(self) -> "LLMConfig":
        if self.provider != "openai":
            raise ValueError(
                f"llm.provider must be 'openai' in the OAuth Edition "
                f"(got {self.provider!r}). See README for background."
            )
        return self
'''


def rewrite_base_py(step_dir: Path) -> None:
    """Overwrite base.py with the tool-aware variant."""
    path = step_dir / "src" / "mybot" / "provider" / "llm" / "base.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TOOL_AWARE_BASE_PY, encoding="utf-8")


def drop_litellm_from_pyproject(step_dir: Path) -> None:
    """Remove `litellm>=...` entries from pyproject.toml and ensure httpx is listed."""
    path = step_dir / "pyproject.toml"
    source = path.read_text(encoding="utf-8")
    # Remove any line whose stripped form starts with `"litellm`.
    new_lines = []
    for line in source.splitlines():
        if re.match(r'^\s*"litellm[^"]*",?\s*$', line):
            continue
        new_lines.append(line)
    new_source = "\n".join(new_lines)
    if source.endswith("\n"):
        new_source += "\n"

    # Ensure `httpx>=0.27.0` is present in [project] dependencies.
    if '"httpx' not in new_source:
        # Insert before the closing `]` of the dependencies block.
        new_source = re.sub(
            r'(dependencies\s*=\s*\[[^\]]*?)(\n\])',
            r'\1\n    "httpx>=0.27.0",\2',
            new_source,
            count=1,
            flags=re.DOTALL,
        )

    path.write_text(new_source, encoding="utf-8")


def purge_litellm_in_source(step_dir: Path) -> list[Path]:
    """Replace `from litellm...` imports used for Message aliasing.

    Returns the list of modified files.
    """
    src = step_dir / "src"
    modified: list[Path] = []
    if not src.exists():
        return modified
    for py in src.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        if "litellm" not in text:
            continue

        orig = text

        # Case 1: session_state.py / agent.py pattern:
        #   from litellm.types.completion import ChatCompletionMessageParam as Message
        # Replace with a local Message = dict[str, Any] alias.
        text = re.sub(
            r"from\s+litellm\.types\.completion\s+import\s+ChatCompletionMessageParam\s+as\s+Message\s*\n",
            "",
            text,
        )

        # Case 2: multi-import from litellm.types.completion:
        #   from litellm.types.completion import (
        #       ChatCompletionMessageParam as Message,
        #       ChatCompletionMessageToolCallParam,
        #   )
        # Replace that block with no-op; we'll substitute aliases below.
        text = re.sub(
            r"from\s+litellm\.types\.completion\s+import\s*\([^)]*\)\s*\n",
            "",
            text,
            flags=re.DOTALL,
        )

        # If `ChatCompletionMessageToolCallParam` is still used as a type
        # annotation (only), replace it with `dict[str, Any]` inline.
        text = re.sub(
            r"\blist\[ChatCompletionMessageToolCallParam\]",
            "list[dict[str, Any]]",
            text,
        )
        text = re.sub(
            r"\bChatCompletionMessageToolCallParam\b",
            "dict",
            text,
        )

        # Case 3: `from litellm import ...`
        text = re.sub(r"^from\s+litellm[^\n]*\n", "", text, flags=re.MULTILINE)

        # Case 4: `import litellm` alone
        text = re.sub(r"^import\s+litellm[^\n]*\n", "", text, flags=re.MULTILINE)

        # Ensure `Message = dict[str, Any]` alias exists if any identifier
        # named `Message` is referenced (it's used in type annotations inside
        # function bodies, which Python evaluates at call time).
        has_alias = bool(
            re.search(r"^Message\s*=\s*dict\[", text, re.MULTILINE)
        )
        if re.search(r"\bMessage\b", text) and not has_alias:
            # Add alias after the last top-level import.
            # Find the first non-import, non-blank line.
            lines = text.splitlines(keepends=True)
            insert_idx = 0
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if (
                    stripped.startswith("import ")
                    or stripped.startswith("from ")
                    or stripped.startswith("#")
                    or not stripped
                    or stripped.startswith('"""')
                ):
                    insert_idx = i + 1
                    continue
                break
            lines.insert(insert_idx, "\n\nMessage = dict[str, Any]\n")
            text = "".join(lines)

        # Ensure `Any` is imported if referenced.
        if "dict[str, Any]" in text and re.search(r"from typing import[^\n]*\bAny\b", text) is None:
            # If there is a `from typing import ...` line, append `Any` to it.
            m2 = re.search(r"^from typing import ([^\n]+)$", text, re.MULTILINE)
            if m2:
                existing = m2.group(1)
                if "Any" not in existing.split(","):
                    new_import = "from typing import " + existing.rstrip() + ", Any"
                    text = text.replace(m2.group(0), new_import, 1)
            else:
                # Insert a fresh typing import at the top, after docstring.
                lines = text.splitlines(keepends=True)
                insert_idx = 0
                if lines and lines[0].startswith('"""'):
                    # Skip past the module docstring.
                    for i in range(1, len(lines)):
                        if '"""' in lines[i]:
                            insert_idx = i + 1
                            break
                lines.insert(insert_idx, "from typing import Any\n")
                text = "".join(lines)

        if text != orig:
            py.write_text(text, encoding="utf-8")
            modified.append(py)
    return modified


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def add_login_subcommand(step_dir: Path) -> bool:
    """Add the `login` Typer subcommand to step's cli/main.py if missing.

    Returns True if the file was modified, False if already present.
    """
    path = step_dir / "src" / "mybot" / "cli" / "main.py"
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    if "def login(" in source and "ChatGPTOAuth()" in source:
        return False

    # Add the import if missing.
    if "from mybot.provider.llm.oauth import ChatGPTOAuth" not in source:
        # Insert after the last `from mybot` import.
        match = None
        for m in re.finditer(r"^from mybot[^\n]*$", source, re.MULTILINE):
            match = m
        if match:
            insert_pos = match.end()
            source = (
                source[:insert_pos]
                + "\nfrom mybot.provider.llm.oauth import ChatGPTOAuth"
                + source[insert_pos:]
            )
        else:
            # Insert after typer imports.
            m2 = re.search(r"^import typer[^\n]*$", source, re.MULTILINE)
            if m2:
                source = (
                    source[: m2.end()]
                    + "\nfrom mybot.provider.llm.oauth import ChatGPTOAuth"
                    + source[m2.end() :]
                )
            else:
                raise RuntimeError(
                    f"{path}: could not find insertion point for oauth import"
                )

    # Append the login subcommand before `if __name__ == "__main__":`.
    login_block = '''

@app.command("login")
def login(ctx: typer.Context) -> None:
    """Run one-time ChatGPT OAuth login and write the token store."""
    result = ChatGPTOAuth().login()
    console.print(
        f"[green]Logged in as[/green] {result.account_id or '<unknown>'}\\n"
        f"Token store: [cyan]{result.token_store_path}[/cyan]"
    )

'''
    # Insert before `if __name__ == "__main__":` if present; otherwise at EOF.
    m3 = re.search(r'^if __name__ == "__main__":', source, re.MULTILINE)
    if m3:
        source = source[: m3.start()] + login_block + "\n" + source[m3.start() :]
    else:
        source = source.rstrip() + "\n" + login_block

    path.write_text(source, encoding="utf-8")
    return True


def rollout_step(step: str) -> None:
    step_dir = ROOT / step
    if not step_dir.exists():
        log(f"[skip] {step}: directory missing")
        return
    log(f"[rollout] {step}")

    copy_shared_files(step_dir)
    rewrite_config_py(step_dir)
    rewrite_base_py(step_dir)
    drop_litellm_from_pyproject(step_dir)
    modified = purge_litellm_in_source(step_dir)
    added_login = add_login_subcommand(step_dir)

    log(f"  + shared files copied")
    log(f"  + LLMConfig rewritten (config.py)")
    log(f"  + base.py rewritten (tool-aware)")
    log(f"  + litellm dropped from pyproject.toml")
    if modified:
        log(
            f"  + litellm imports purged from: "
            + ", ".join(str(p.relative_to(step_dir)) for p in modified)
        )
    if added_login:
        log(f"  + login subcommand added to cli/main.py")


def main() -> int:
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = STEPS
    for step in targets:
        rollout_step(step)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
