# Bootstrap Template

A minimal standalone skeleton for starting your own ChatGPT-OAuth agent from scratch, without any of the tutorial's 18 steps.

This is the "I've learned the pattern, now I want to apply it to my own problem" starting point. It sits alongside [`../README.md`](../README.md) (the customization walkthrough).

## What's in here

```
18-customization/bootstrap-template/
├── README.md                # this file
├── pyproject.toml           # minimal deps
├── src/
│   └── yourpkg/             # RENAME THIS to your package name
│       ├── __init__.py
│       ├── cli/main.py      # typer app: login + chat
│       └── provider/llm/
│           ├── __init__.py
│           ├── oauth.py     # byte-identical with the tutorial
│           └── responses.py # byte-identical with the tutorial
└── workspace/
    ├── config.example.yaml
    └── models.yaml
```

Everything agent-specific (sessions, history, tools, skills) is stripped out. You get the minimum to:

1. Log in to ChatGPT via OAuth.
2. Send a chat message.
3. Get a reply.

From here you build whatever you want.

## How to use it

### Option A — Copy it

```bash
cp -r 18-customization/bootstrap-template ~/myproject
cd ~/myproject

# Rename yourpkg → the package name you want.
mv src/yourpkg src/mynewbot

# Rewrite imports.
find . -name "*.py" -exec sed -i '' 's/yourpkg/mynewbot/g' {} +
sed -i '' 's/yourpkg/mynewbot/g' pyproject.toml

# Rename the CLI binary (edit pyproject.toml [project.scripts]).
# Also rename the Token_Store subdir in oauth.py (search for "yourpkg").

# Install and go.
uv sync
uv run mynewbot login
uv run mynewbot chat
```

See [`../README.md`](../README.md) tier 4 for the full rename recipe.

### Option B — Treat it as a library

Import the primitives directly from the tutorial (or from a vendored copy) and write your own everything.

Minimum viable (one file, no package structure):

```python
# my_bot.py
import asyncio

from yourpkg.provider.llm.oauth import ChatGPTOAuth
from yourpkg.provider.llm.responses import (
    ResponsesClient, ResponsesRequest, aggregate_stream,
)


async def ask(question: str) -> str:
    oauth = ChatGPTOAuth()
    access_token = await oauth.access_token()
    account_id = await oauth.account_id()

    client = ResponsesClient()
    request = ResponsesRequest(
        model="gpt-5.4",
        instructions="You are a helpful assistant.",
        input=[{"role": "user", "content": question}],
    )
    events = client.stream(
        request, access_token=access_token, account_id=account_id
    )
    aggregated = await aggregate_stream(events)
    return aggregated.content


if __name__ == "__main__":
    print(asyncio.run(ask("Hello, who are you?")))
```

## What's NOT in here

- **No `SessionState`, `Agent`, `AgentSession`.** The tutorial adds those in step 00. The template's `chat` command is a trivial one-shot — one request, one reply, no history.
- **No tools.** Added in step 01.
- **No skills.** Added in step 02.
- **No history / persistence.** Added in step 03.
- **No LLMConfig class.** We use a hardcoded model in `main.py`.

When you need any of these, steal from the tutorial. The patterns are small and documented.

## Prerequisites

1. `uv` installed.
2. A ChatGPT Plus or Pro subscription.
3. ~10 minutes.

## Customization checklist

- [ ] Rename `src/yourpkg/` → `src/<your-package-name>/`
- [ ] Update `pyproject.toml` name, scripts, and package list
- [ ] Rename `_default_path()`'s Token_Store subdir in `oauth.py` (currently `yourpkg`)
- [ ] Set your default model in `main.py` (currently `gpt-5.4`)
- [ ] Set your default system prompt in `main.py` (currently a generic helpful-assistant message)
- [ ] Copy `workspace/` to wherever you want your local config + token layout to live, and point `--workspace` at it (optional)
- [ ] Push to a git repo if you want others to `uvx` it

## See also

- [`../../000-oauth/README.md`](../../000-oauth/README.md) — everything this template's `oauth.py` and `responses.py` do, explained.
- [`../README.md`](../README.md) — the full customization tiers.
- [`../../OAUTH_EDITION_GUIDE.md`](../../OAUTH_EDITION_GUIDE.md) — architecture overview of the OAuth Edition.
