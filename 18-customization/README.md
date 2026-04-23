# Customization — Making It Yours

You've read the tutorial. You have a working OAuth-backed ChatGPT agent. Now you want it to not look and feel like "the tutorial's bot" — you want it to be *your* bot, with your name, your workspace, your persona, your distribution.

This guide covers five tiers of customization, from "30 seconds, one file" to "start over from scratch with a clean skeleton." Pick your level of commitment.

```
Tier 1 — Persona          — 30 seconds
Tier 2 — Workspace         — 2 minutes
Tier 3 — Model             — instant
Tier 4 — Branding          — 15 minutes (rename everything)
Tier 5 — From scratch      — 1 hour (new project, new namespace)
```

Work your way up. Each tier builds on the previous one being understood, but you don't have to *do* the previous ones first.

---

## Tier 1 — Persona (30 seconds)

The "who is this agent" is controlled by a single markdown file.

```bash
cat default_workspace/agents/pickle/AGENT.md
```

```markdown
---
name: Pickle
description: A friendly cat assistant
allow_skills: true
---

You are Pickle, a cat assistant who loves to help. You are friendly,
playful, and always sign off with a cat-related emoji.
```

Replace the body with whatever persona you want. Rename the `pickle/` directory to match:

```bash
mv default_workspace/agents/pickle default_workspace/agents/lucy
# edit default_workspace/agents/lucy/AGENT.md — set name: Lucy, rewrite body
```

Then update your config to default to the new agent:

```bash
# default_workspace/config.user.yaml
default_agent: lucy
```

Rerun `my-bot chat`. You're now talking to Lucy.

**What's happening under the hood:** `AgentLoader` reads the frontmatter (`name`, `description`, `allow_skills`) and the markdown body (the system prompt). The body becomes `{"role": "system", "content": <body>}` — the first message every turn. The `AGENT.md` file is the persona's source of truth. There is nothing else.

---

## Tier 2 — Workspace (2 minutes)

The "workspace" is the directory holding your config, your agents, your skills, your model allowlist, and your conversation history. By default it's `default_workspace/` at the repo root.

You can point any step at a different workspace with `--workspace`:

```bash
cd 00-chat-loop
uv run my-bot --workspace ~/myproject/agent chat
```

What has to be in that directory for things to work:

```
~/myproject/agent/
├── config.user.yaml         # required — llm.provider, llm.model, default_agent
├── models.yaml              # required — accepted model list (copy from default_workspace/models.yaml)
├── agents/
│   └── <agent-id>/AGENT.md  # one per agent
├── skills/                  # optional
│   └── <skill-id>/SKILL.md
└── .history/                # auto-created by HistoryStore on first run
```

The fastest way to set this up is to copy `default_workspace/` and edit:

```bash
cp -r default_workspace ~/myproject/agent
# edit config.user.yaml, rename agents as you like
cd 00-chat-loop
uv run my-bot --workspace ~/myproject/agent chat
```

**Why have a separate workspace at all?** Because your agent's *state* (config, persona files, skills, history) lives apart from the agent's *code*. You can git-clone a new version of the tutorial's source without touching your workspace. Or share a workspace across multiple versions. Or keep one workspace per project.

Path-sensitive paths inside the workspace (`agents/`, `skills/`, `.history/`) can be overridden in `config.user.yaml`:

```yaml
llm:
  provider: openai
  model: gpt-5.4

default_agent: lucy

# Optional path overrides — defaults are shown
agents_path: agents          # where agent definitions live
skills_path: skills          # where skill definitions live
history_path: .history       # where conversation history is written
```

Relative paths are resolved against the workspace root.

---

## Tier 3 — Model (instant)

The `llm.model` field in `config.user.yaml` picks which ChatGPT model to call. The tutorial ships with `gpt-5.4`; you can choose anything in `default_workspace/models.yaml`'s `allowed` list, or anything matching its `patterns`:

```yaml
# default_workspace/models.yaml
allowed:
  - gpt-5.4
  - gpt-5.4-mini
  - gpt-5.2
  - gpt-5.2-codex
  - gpt-5.3-codex
  - gpt-5.1-codex
  - gpt-5.1-codex-max
  - gpt-5.1-codex-mini

patterns:
  - "*codex*"
```

**Model doesn't exist yet?** Add it to `allowed` and you're done — no Python change needed:

```yaml
allowed:
  - gpt-5.4
  - gpt-5.5-experimental        # add yours here
```

**Model matches a pattern?** Even easier — anything with `codex` in the name works out of the box. Try `llm.model: gpt-5.9-codex-nightly` and it'll validate.

**Per-agent model.** You can override the model on a per-agent basis in that agent's `AGENT.md` frontmatter:

```markdown
---
name: Lucy
description: A scholarly assistant
allow_skills: true
llm:
  model: gpt-5.3-codex         # Lucy uses Codex; other agents use the default
  temperature: 0.3
---

You are Lucy, a scholarly assistant ...
```

The per-agent `llm` block is merged over the global one — you can override any subset.

---

## Tier 4 — Branding (15 minutes)

You've decided `my-bot` is stupid and you want `blah`. The agent's code should live in `blah`, the CLI binary should be `blah`, the Token_Store should go to `~/.config/blah/`. Nothing with "mybot" in it should be visible to your users.

This is a find-and-replace operation across the codebase. It's mechanical but there are a few gotchas.

**Four things to rename:**

1. The **Python package name** — `mybot` → `blah`. Every file in `src/mybot/` moves to `src/blah/`. Every `from mybot...` becomes `from blah...`. Every `[tool.hatch.build.targets.wheel] packages = ["src/mybot"]` in `pyproject.toml` becomes `["src/blah"]`.

2. The **CLI binary name** — `my-bot` → `blah`. One line in each `pyproject.toml`:
   ```toml
   [project.scripts]
   blah = "blah.cli.main:app"          # was: my-bot = "mybot.cli.main:app"
   ```
   After reinstall, the binary on your PATH is `blah`.

3. The **Token_Store subdirectory** — `~/.config/mybot/` → `~/.config/blah/`. This is hardcoded in `oauth.py`:
   ```python
   def _default_path() -> Path:
       ...
       return base / "mybot" / "chatgpt_oauth.json"   # change to "blah"
   ```
   Do this in every step's `oauth.py`. It stays byte-identical across steps, so P13 (the uniformity test) stays green — but only if you rename in every step.

4. The **repo name and references** — "Build Your Own OpenClaw" — whatever you want. Edit READMEs, guides, RESTORE doc. Not structural.

### The safe way: one step at a time

The tutorial ships with a helper script that mass-renames across all steps. Alternatively, you can use `sed` + a discovery pass.

```bash
# Start at the repo root.
NEW_PKG="blah"           # the new Python package name
NEW_BIN="blah"           # the new CLI binary name (usually same)
NEW_STORE="blah"         # the Token_Store subdir (usually same)

# 1. Rename src/mybot → src/<NEW_PKG> in every step.
for step in 000-oauth 00-chat-loop 01-tools 02-skills 03-persistence \
            04-slash-commands 05-compaction 06-web-tools 07-event-driven \
            08-config-hot-reload 09-channels 10-websocket \
            11-multi-agent-routing 12-cron-heartbeat 13-multi-layer-prompts \
            14-post-message-back 15-agent-dispatch 16-concurrency-control \
            17-memory; do
  [ -d "$step/src/mybot" ] && mv "$step/src/mybot" "$step/src/$NEW_PKG"
done

# 2. Rewrite imports. `from mybot` and `import mybot` → with the new name.
#    macOS:
find . -name "*.py" -not -path "*/.venv/*" -not -path "*/.kiro/*" \
  -exec sed -i '' "s/\bmybot\b/$NEW_PKG/g" {} +
# Linux: same command without the '' after -i

# 3. Rewrite pyproject.toml packages + scripts.
find . -name "pyproject.toml" -not -path "*/.venv/*" \
  -exec sed -i '' "s|\"src/mybot\"|\"src/$NEW_PKG\"|g" {} +
find . -name "pyproject.toml" -not -path "*/.venv/*" \
  -exec sed -i '' "s|my-bot = \"mybot\\.cli\\.main:app\"|$NEW_BIN = \"$NEW_PKG.cli.main:app\"|g" {} +

# 4. Rename the Token_Store subdir. This changes oauth.py.
find . -name "oauth.py" -not -path "*/.venv/*" \
  -exec sed -i '' "s|base / \"mybot\"|base / \"$NEW_STORE\"|g" {} +

# 5. Fresh sync every step.
for step in 000-oauth 00-chat-loop 01-tools ...; do
  uv sync --directory "$step"
done
```

### Gotchas

**The tests.** `tests/` references `mybot.*` everywhere. Run the find-and-replace over `tests/` too. After that, tests should pass.

**The README and docs.** Prose like "my-bot chat" across the READMEs won't auto-rewrite cleanly (it's quoted sometimes, inside code blocks other times). Easier to go through each README by hand.

**Old Token_Stores.** If you renamed the subdirectory from `mybot` to `blah`, your existing login at `~/.config/mybot/chatgpt_oauth.json` is ignored. Either `mv ~/.config/mybot ~/.config/blah`, or re-run `blah login`.

**Step directories.** `00-chat-loop`, `01-tools`, etc. — these names are PART OF THE TUTORIAL'S IDENTITY. Renaming them breaks cross-references in every step README. Probably don't.

### Verify

After the rename, run the full test suite and do an end-to-end check:

```bash
uv run --directory 00-chat-loop pytest ../tests/ ../000-oauth/tests/
# Expect: 142 passed, 1 skipped

cd 000-oauth
uv sync
uv run blah login        # your new CLI
uv run blah chat         # or in 00-chat-loop, whatever step you're testing
```

If tests pass and you can log in + chat, the rebrand is complete.

---

## Tier 5 — From scratch (1 hour)

You want to start a completely new project, not based on the tutorial's 18-step scaffold, but using the OAuth + Responses API primitives. This is the "I've learned the pattern, now I want to apply it to my own problem" level.

**Two approaches:**

1. **Copy `999-bootstrap-template/`** — a minimal standalone skeleton. That directory is [here](./999-bootstrap-template/). Copy it wherever you want, rename per Tier 4, start building.

2. **Write your own from scratch** using `000-oauth` as a library. Copy `oauth.py` and `responses.py` into your project; you now have login + SSE. Everything else (agent, session, tools) is yours to design. Minimum viable: a `chat()` function that takes a message, sends a Responses API request, returns the reply. ~50 lines.

### Minimal viable agent (all the code)

If you want a working chat client in one file:

```python
# minimal_bot.py
import asyncio

from yourpkg.provider.llm.oauth import ChatGPTOAuth
from yourpkg.provider.llm.responses import (
    ResponsesClient, ResponsesRequest, aggregate_stream,
)


async def chat_once(user_message: str) -> str:
    oauth = ChatGPTOAuth()
    access_token = await oauth.access_token()
    account_id = await oauth.account_id()

    client = ResponsesClient()
    request = ResponsesRequest(
        model="gpt-5.4",
        instructions="You are a helpful assistant.",
        input=[{"role": "user", "content": user_message}],
    )
    events = client.stream(
        request, access_token=access_token, account_id=account_id
    )
    aggregated = await aggregate_stream(events)
    return aggregated.content


if __name__ == "__main__":
    print(asyncio.run(chat_once("Hello, who are you?")))
```

That's a complete, working chat client. No history, no loop, no tools. Just one question in, one answer out. Everything you've learned in the tutorial is how to grow from this seed into something useful.

### Publish as a `uvx`-installable tool

Once your project is shaped up, you can make it runnable from anywhere on your machine (and anyone else's) without cloning:

1. Push your project to a public git repo (GitHub, GitLab).
2. Run from the same machine or a new one:
   ```bash
   uvx --from git+https://github.com/you/yourbot.git yourbot login
   uvx --from git+https://github.com/you/yourbot.git yourbot chat
   ```
   `uvx` clones, installs, caches, runs. No `git clone`, no `uv sync`.
3. If you want a stable alias:
   ```bash
   uv tool install --from git+https://github.com/you/yourbot.git yourbot
   yourbot chat   # yourbot is now on PATH globally
   ```

### Publishing to PyPI

To make `pip install yourbot` work, you need to ship to PyPI:

```bash
# Build a wheel
cd your-project
uv build

# Publish
uv publish
```

First time publishing requires a PyPI account + token. The heavy lifting is choosing a unique package name (`yourbot` is probably taken — check on pypi.org first).

After publishing, others install with:

```bash
pip install yourbot
yourbot login
yourbot chat
```

---

## Troubleshooting after customization

| Symptom | Likely cause | Fix |
|---|---|---|
| `No ChatGPT OAuth token found` after rebrand | You renamed the Token_Store subdir but didn't migrate the old file | `mv ~/.config/mybot ~/.config/blah` or re-run `blah login` |
| `ImportError: No module named mybot` | Rename missed a file | `grep -r "from mybot\|import mybot" .` to find stragglers |
| Tests fail after rebrand | `tests/` still references `mybot` | Run the same `sed` pass on `tests/` |
| P13 byte-identity test fails | Rename was done in some steps, not others | Confirm `oauth.py` and `responses.py` are byte-identical across all steps — use `sha256sum */src/mybot/provider/llm/oauth.py` |
| `uvx` says "command not found" | Your `pyproject.toml` `[project.scripts]` name doesn't match what you typed | Inspect `pyproject.toml`, make sure the left side of `=` is your binary name |

---

## What stays fixed no matter what you rename

- **The ChatGPT OAuth client_id.** `app_EMoamEEZ73f0CkXaXp7hrann`. This belongs to Codex CLI and can't be changed without breaking the OAuth flow.
- **The callback port 1455.** The OpenAI OAuth registration requires it.
- **The Responses API URL.** `https://chatgpt.com/backend-api/codex/responses`. Server-side contract.
- **The `originator: codex_cli_rs` header.** Same reason.

Your renames are cosmetic changes to the Python layer. The OAuth protocol and the Responses API are fixed external contracts.
