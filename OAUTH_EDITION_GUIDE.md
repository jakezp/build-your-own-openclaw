# Build Your Own OpenClaw (OAuth Edition) — Standalone Guide

A fresh, self-contained guide for building a ChatGPT-OAuth-backed AI agent from scratch. This guide pulls the essential architecture decisions out of the 18 tutorial steps so you can skim it in one sitting, then dive into whichever step matches the feature you need.

**Prerequisite:** ChatGPT Plus or ChatGPT Pro subscription. No OpenAI API key is required or supported.

## Why This Edition Exists

The original multi-provider tutorial authenticated via `litellm` and supported Anthropic, Gemini, MiniMax, Grok, and others via API keys. This edition replaces that with a single direct path to `chatgpt.com/backend-api/codex/responses` — the same backend OpenAI's Codex CLI uses — authenticated by a one-time browser OAuth flow against your ChatGPT subscription.

Three constraints drove the design:

1. **Backend contract is fixed.** Streaming SSE is mandatory. `store: false` is mandatory. The `ChatGPT-Account-Id` header is required. Only specific `gpt-5.x` and `*codex*` models are accepted.
2. **No backwards compatibility.** `api_key`, `api_base`, and `auth` are rejected at config validation with a hint pointing at `my-bot login`.
3. **Pedagogical goal unchanged.** Each of the 18 steps still fits in a single sitting, and the shape of `LLMConfig` / `LLMProvider` / `cli/main.py` is the same across every step.

## The Five Pieces

```
┌──────────────────┐         ┌──────────────────┐
│ config.user.yaml │         │  models.yaml     │
│  (llm.provider,  │────────►│  allowed + glob  │
│   llm.model)     │         │  patterns        │
└────────┬─────────┘         └──────────────────┘
         │
         ▼
┌──────────────────┐         ┌──────────────────┐
│   LLMConfig      │         │    oauth.py      │
│   (credential-   │         │ PKCE login +     │
│    free)         │         │ refresh loop     │
└────────┬─────────┘         └──────┬───────────┘
         │                          │
         │                          │ access_token()
         │                          │ account_id()
         ▼                          ▼
┌─────────────────────────────────────────────┐
│              LLMProvider.chat               │
│  _translate_messages → ResponsesRequest     │
└────────┬────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│               responses.py                  │
│  httpx.stream → SSE parser → aggregate      │
└────────┬────────────────────────────────────┘
         │
         ▼
  chatgpt.com/backend-api/codex/responses
```

Each piece has a narrow job. The same five files (plus a tool-aware `base.py` variant for steps 01+) ship byte-identical across all 18 steps.

## Piece 1: `models.yaml` — The Accepted Model List

`default_workspace/models.yaml` is a checked-in YAML document that enumerates every model id the ChatGPT subscription backend will accept for Codex. To add a new model, edit this file. No Python change needed.

```yaml
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

A model id is **accepted** iff it is in the `allowed` list OR matches any `patterns` glob (`fnmatch.fnmatchcase`). The pattern is deliberately narrow — `"*codex*"` catches newly-released Codex variants without opening the door to anything else.

## Piece 2: `oauth.py` — One-Time Login + Silent Refresh

`src/mybot/provider/llm/oauth.py` is self-contained and byte-identical across all 18 steps. Three jobs:

1. **`ChatGPTOAuth.login()`** — runs the PKCE S256 browser flow against `https://auth.openai.com/oauth/authorize`, handles the `/auth/callback?code=...&state=...` redirect on `127.0.0.1:1455`, exchanges the code at `https://auth.openai.com/oauth/token`, and writes credentials to the Token_Store with POSIX mode `0600`.

2. **`ChatGPTOAuth.access_token()`** — reads the stored credentials, returns the access token. If `expires_at <= now + 60s`, posts `grant_type=refresh_token` to the token endpoint, atomically writes the new credentials, returns the fresh token. A 400/401 raises a "run `my-bot login` again" error and leaves the Token_Store untouched. An in-process `asyncio.Lock` prevents concurrent `chat()` calls from racing on the on-disk file.

3. **`ChatGPTOAuth.account_id()`** — returns the `chatgpt_account_id` claim from the stored `id_token`. The Responses API requires this as the `ChatGPT-Account-Id` header.

### Token_Store Layout

POSIX: `~/.config/mybot/chatgpt_oauth.json` (or `$XDG_CONFIG_HOME/mybot/...`)
Windows: `%APPDATA%\mybot\chatgpt_oauth.json`

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": "2026-05-01T15:31:21.326740Z",
  "account_id": "acct_...",
  "id_token": "eyJ..."
}
```

File mode is `0600` on POSIX. Writes are atomic (`os.replace` after `chmod` on the temp file), so a failure during write never leaves a partial Token_Store.

## Piece 3: `LLMConfig` — Credential-Free Validation

`src/mybot/utils/config.py` defines a small pydantic model. The class body is byte-identical across all 18 steps:

```python
_FORBIDDEN_LLM_FIELDS = frozenset({"api_key", "api_base", "auth"})


class LLMConfig(BaseModel):
    provider: str
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)

    model_config = {"extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_fields(cls, values):
        if not isinstance(values, dict):
            return values
        present = _FORBIDDEN_LLM_FIELDS.intersection(values.keys())
        if present:
            raise ValueError(
                f"llm config contains forbidden field(s): {', '.join(sorted(present))}. "
                f"The OAuth Edition does not accept api_key/api_base/auth. "
                f"Remove them from config.user.yaml and run `my-bot login` "
                f"once to authenticate."
            )
        return values

    @model_validator(mode="after")
    def provider_must_be_openai(self):
        if self.provider != "openai":
            raise ValueError(
                f"llm.provider must be 'openai' in the OAuth Edition "
                f"(got {self.provider!r})."
            )
        return self
```

Separately, the outer `Config` class runs a `check_model_allowlist` validator that loads `workspace/models.yaml` and rejects an unknown `llm.model` with a message listing the currently-accepted set.

## Piece 4: `responses.py` — The Wire Layer

`src/mybot/provider/llm/responses.py` is byte-identical across all 18 steps. It owns exactly four things:

1. **Pinned constants**:
   ```python
   CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
   CHATGPT_ORIGINATOR = "codex_cli_rs"
   REQUIRED_STREAM = True
   REQUIRED_STORE = False
   ```

2. **`ResponsesRequest`** — a dataclass with `to_body()` that enforces `stream=True` / `store=False`. Callers cannot override these.

3. **`ResponsesClient.stream(request, *, access_token, account_id)`** — async generator. Builds the five required headers (`Authorization: Bearer ...`, `ChatGPT-Account-Id`, `Content-Type: application/json`, `Accept: text/event-stream`, `originator: codex_cli_rs`), POSTs via `httpx.AsyncClient.stream`, yields parsed `SSEEvent`s. On non-2xx, raises `ResponsesAPIError(status, detail)` with the body truncated to 500 chars so any accidental token echo in the error body is bounded.

4. **`aggregate_stream(events)`** — collapses the SSE stream into `AggregatedResponse(content, tool_calls)`. Handles these event types (confirmed against live traffic):

| Event | Purpose |
|---|---|
| `response.output_text.delta` | accumulate streamed text |
| `response.output_text.done` | authoritative final text |
| `response.output_item.added` | register a `function_call` item (name + call_id) |
| `response.function_call_arguments.delta` | accumulate tool-call arguments |
| `response.function_call_arguments.done` | final tool-call arguments string |
| `response.output_item.done` | commit a completed function_call |
| `response.error` | surface a backend error |

Unknown event types are silently ignored for forward compatibility.

## Piece 5: `LLMProvider` — The Translation Layer

`src/mybot/provider/llm/base.py` is where Chat-Completions shape (what the rest of the tutorial passes in) gets translated into Responses API shape (what the backend expects). Two variants:

- **Step 00 (narrow)**: `chat(messages, **kwargs) -> str`
- **Steps 01–17 (tool-aware)**: `chat(messages, tools=None, **kwargs) -> tuple[str, list[LLMToolCall]]`

Both share `__init__`, `from_config`, `_resolve_credential`, and the two translation helpers `_translate_messages` and `_translate_tools`.

### `_translate_messages`

Splits a Chat-Completions `messages` list into `(instructions, input)`:

- `role: system` messages → joined with `\n\n` into the top-level `instructions` string (default: `"You are a helpful assistant."` if empty).
- `role: assistant` with `tool_calls` → an assistant-content message (if non-empty) plus one `{type: "function_call", call_id, name, arguments}` item per tool call.
- `role: tool` → `{type: "function_call_output", call_id, output}`.
- Everything else → `{role, content}` verbatim.

### `_translate_tools`

Flattens Chat-Completions tool schema (`{type: "function", function: {name, description, parameters}}`) into Responses API shape (`{type: "function", name, description, parameters}`). Schemas already in Responses shape pass through.

### Tool-aware `chat()` body (steps 01–17)

```python
async def chat(self, messages, tools=None, **kwargs):
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
        [LLMToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
         for tc in aggregated.tool_calls],
    )
```

## Bootstrap Checklist (New Project from Scratch)

If you want to build a new OAuth-Edition agent from a blank directory:

1. **Copy the four shared files** from any step:
   - `src/mybot/provider/llm/oauth.py`
   - `src/mybot/provider/llm/responses.py`
   - The canonical `LLMConfig` class body into your `src/mybot/utils/config.py`
   - Either step 00's narrow `base.py` or the tool-aware variant from step 01+

2. **Add `default_workspace/models.yaml`** with at least one accepted model id.

3. **Wire up the `login` Typer subcommand** in your CLI:
   ```python
   from mybot.provider.llm.oauth import ChatGPTOAuth

   @app.command("login")
   def login(ctx):
       result = ChatGPTOAuth().login()
       console.print(
           f"[green]Logged in as[/green] {result.account_id or '<unknown>'}\n"
           f"Token store: [cyan]{result.token_store_path}[/cyan]"
       )
   ```

4. **Declare dependencies**: `httpx>=0.27.0`, `pydantic>=2.0.0`, `pyyaml>=6.0`, `typer>=0.9.0`, `rich>=13.0.0`. No `litellm`.

5. **Write `default_workspace/config.user.yaml`** with only `llm.provider: openai`, `llm.model: <id-from-models.yaml>`, `default_agent: <name>`.

6. **Run `my-bot login` once**, then chat.

## Key Invariants (Property-Based Tested)

The repo ships 15 properties validated by Hypothesis + respx. The ones worth remembering:

- **P1** — `LLMConfig` validates iff `provider == "openai"`, `model` is accepted by `models.yaml`, and no forbidden field is present.
- **P2** — `access_token()` refreshes iff `expires_at <= now + 60s`.
- **P3** — On every refresh, the Token_Store either stays byte-identical (failure) or ends with strictly later `expires_at` (success).
- **P6** — `TokenStore.write()` always leaves POSIX mode `0600`.
- **P7** — Every `LLMProvider.chat()` POST has `stream: true`, `store: false`, `Authorization: Bearer ...`, `ChatGPT-Account-Id: ...`, `originator: codex_cli_rs`.
- **P8** — SSE `response.output_text.delta` chunks concatenate to the `response.output_text.done.text`.
- **P9** — Missing/malformed Token_Store always raises with `my-bot login` in the message.
- **P11** — Refresh and Responses-API errors never echo the access_token or refresh_token in their exception text.
- **P13** — `oauth.py` and `responses.py` are byte-identical across all 18 steps (SHA-256 equality).
- **P14** — The `LLMConfig` class body is byte-identical across all 18 steps.
- **P15** — Every step's `cli/main.py` wires up the `login` subcommand via `ChatGPTOAuth()`.

## Directory Map

```
build-your-own-openclaw/
├── 00-chat-loop/        # narrow chat, no tools
├── 01-tools/            # + tool registry + bash/read_file/etc.
├── 02-skills/           # + SKILL.md files
├── 03-persistence/      # + HistoryStore
├── 04-slash-commands/   # + /compact, /context
├── 05-compaction/       # + ContextGuard token-threshold compaction
├── 06-web-tools/        # + web search / web read
├── 07-event-driven/     # event bus + workers
├── 08-config-hot-reload # watchdog-backed hot reload
├── 09-channels/         # Telegram / Discord / CLI channels
├── 10-websocket/        # programmatic WebSocket access
├── 11-multi-agent-routing/
├── 12-cron-heartbeat/
├── 13-multi-layer-prompts/
├── 14-post-message-back/
├── 15-agent-dispatch/
├── 16-concurrency-control/
├── 17-memory/
│
├── default_workspace/
│   ├── config.example.yaml    # copy to config.user.yaml
│   ├── models.yaml            # accepted-model allowlist
│   ├── agents/
│   └── skills/
│
├── tests/                      # shared PBT + smoke suite
├── scripts/rollout_oauth_edition.py  # bulk roller if you iterate on the design
└── README.md                   # top-level Quick Start
```

## Differences From Codex CLI

This tutorial re-implements Codex CLI's subscription-backed flow in Python. Major deviations:

- **Concurrency**: in-process `asyncio.Lock` on `ChatGPTOAuth` instead of an OS-level file lock. Fine for single-process deployments; step 16 discusses future file-lock work.
- **No reasoning/verbosity controls**: the Responses API accepts `reasoning.effort` and `text.verbosity`, but we don't expose them. Pass them through `**kwargs` on `chat()` if you need them.
- **Single account**: one Token_Store per machine. Multi-account switching is future work.
- **Transport**: pure httpx, no Rust-side optimizations or per-request retries.

## What Comes Next

Open `00-chat-loop/README.md` and start reading. Every subsequent step's README assumes you've read the previous one.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No ChatGPT OAuth token found at ...` | Run `cd 00-chat-loop && uv run my-bot login`. |
| `Token store at ... is unreadable` | The file was corrupted or written by a different tool. Run `my-bot login` again; it overwrites atomically. |
| `ChatGPT refresh token rejected` | The refresh grant expired server-side. Run `my-bot login` again. |
| `llm config contains forbidden field(s): api_key` | Remove `api_key`/`api_base`/`auth` from your `config.user.yaml`. Credentials live in the Token_Store only. |
| `llm.model '...' is not accepted` | Pick a value from `default_workspace/models.yaml`'s `allowed` list, or add the new id to that file. |
| Port `1455` is in use | Another ChatGPT OAuth login is in flight somewhere. Stop it and retry. |
| `Responses API HTTP 401` | Your `access_token` is rejected even after refresh. Run `my-bot login` again. |
| `Responses API HTTP 403` | Your ChatGPT account may lack Codex access (requires a Plus/Pro subscription). |
