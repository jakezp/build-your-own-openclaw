# Step 03: Persistence

> Your agent remembers you between runs.

## Prerequisites

- [`000-oauth/`](../000-oauth/), [`00-chat-loop/`](../00-chat-loop/), [`01-tools/`](../01-tools/), [`02-skills/`](../02-skills/) done.

```bash
cd 03-persistence
uv sync
```

## Why this step exists

Every conversation so far has evaporated on `quit`. You tell pickle your name, you close the CLI, you reopen it, you're a stranger again. Everything in memory; nothing on disk.

That's fine for a toy. Real agents need **durable memory**. Not "remember everything I've ever said" (that's step 17) — just "the conversation I was having yesterday is still there when I open the CLI today."

This step introduces a `HistoryStore`: a small JSONL-backed database of sessions and messages. Every message you send, every assistant reply, every tool call is persisted the instant it's added to the in-memory state. Close the CLI; the conversation lives on.

## The mental model

Two files per session:

```
.history/
├── index.jsonl              # one line per session: id, agent_id, title, counts, timestamps
└── sessions/
    ├── <uuid>.jsonl         # one line per message
    ├── <uuid>.jsonl
    └── <uuid>.jsonl
```

The `index.jsonl` is a lightweight catalog — "here are the sessions that exist, sorted by most-recently-used." When you want a list of past sessions (step 04 adds `/resume` for this), you read the index, not every session file.

Each session's file is a stream of messages, one per line. Append-only. Writing is trivial (just `open(...).write(...)`). Reading is a parse-one-line-per-message loop.

JSONL — JSON Lines — is not one giant JSON blob. It's line-oriented JSON: each line is a self-contained JSON object. Why JSONL over a single JSON array?

- **Append is `O(1)`.** `open("a")` and write a line. No parsing the existing file, no re-serializing.
- **Concurrent reads are safe.** Another process can `tail -f` your session file. A JSON array would need to be rewritten whole on every append.
- **Corruption is local.** If one line is malformed (process crash mid-write), the rest of the file is still readable. We skip bad lines in `get_messages()` rather than blowing up.
- **It's `grep`-able.** `grep '"role":"user"' sessions/<uuid>.jsonl` just works.

## Key decisions

### Decision 1: persistence is a concern of `SessionState`

In step 00, `SessionState` was a pure container — add a message to an in-memory list. In step 03, `SessionState.add_message()` does one more thing: also write to the `HistoryStore`.

Why put persistence there instead of in `AgentSession`? Because persistence is about the *state*, not the orchestration. If you later build a `SessionState` subclass that persists to Redis or SQLite, it slots into `AgentSession` unchanged.

This is the Liskov thing: "anywhere a `SessionState` is expected, any subclass of `SessionState` must work." Step 03 respects it.

### Decision 2: write on every message, not on quit

We could have held all the messages in memory and flushed them to disk only when the session ends. That'd be faster. But:

- If the process crashes, you lose the whole session.
- If the model gets stuck in a long tool loop, you can't `tail -f` the session file to see what's happening.

Writing on every message is slower but safer and more debuggable. The cost is one file-append per turn, which is rounding error next to the LLM call.

### Decision 3: auto-title from the first user message

When a session is first created, `title` is `None`. The first `{"role": "user", ...}` message you send sets the title to the first 50 characters of that message. Why?

Because when you list your past sessions (`/resume` in step 04), a wall of UUIDs is useless. A title of "How do I set up PostgreSQL?" tells you what the session was about at a glance.

It's a small touch, but the "index of past sessions must be human-browseable" invariant drives a lot of the rest of this step's design.

### Decision 4: `HistoryMessage` is a pydantic model, not a raw dict

The in-memory message format is a plain `dict[str, Any]` — we've been using it since step 00. For persistence we introduce `HistoryMessage`, a pydantic model with explicit fields:

```python
class HistoryMessage(BaseModel):
    timestamp: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
```

Two wins from the model:

- **Write-time validation.** If the in-memory message has an unexpected role (misspelling, `"usr"` instead of `"user"`), pydantic catches it at `from_message()` time, not at read time.
- **Schema documentation.** Someone reading the history files can look at the model and know what every field means.

The translation from plain dict to `HistoryMessage` happens in `from_message()`. The reverse (`to_message()`) turns a persisted message back into the plain dict shape the LLM wants.

## Read the code

### 1. `src/mybot/core/history.py` — the store

Four pieces:

**`_session_path()`** — where a session's messages live on disk:

```python
def _session_path(self, session_id: str) -> Path:
    return self.sessions_path / f"{session_id}.jsonl"
```

**`create_session()`** — called once when a session starts. Writes one line to `index.jsonl`, creates an empty `<uuid>.jsonl` for messages.

**`save_message()`** — called on every message. Appends to `<uuid>.jsonl`, reads the index, updates the session's counts and timestamp, rewrites the index sorted by `updated_at` descending (so the most-recently-used session floats to the top).

```python
def save_message(self, session_id: str, message: HistoryMessage) -> None:
    sessions = self._read_index()
    idx = self._find_session_index(sessions, session_id)
    if idx < 0:
        raise ValueError(f"Session not found: {session_id}")

    session = sessions[idx]

    # Append message to session file
    session_file = self._session_path(session_id)
    with open(session_file, "a") as f:
        f.write(message.model_dump_json() + "\n")

    # Update index (counts, timestamp, title)
    session.message_count += 1
    session.updated_at = _now_iso()
    if session.title is None and message.role == "user":
        session.title = message.content[:50]

    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    self._write_index(sessions)
```

Note the cost model: session append is O(1), but the index rewrite is O(N) where N is the number of sessions. For most users N is in the tens. A smarter implementation would batch index writes or use a proper database. For the tutorial, simple wins.

**`get_messages()`** — called when you resume a session (step 04). Reads the session file line-by-line, returns the list of `HistoryMessage`s. Malformed lines are skipped silently — corruption in one line doesn't kill the rest.

### 2. `src/mybot/core/session_state.py` — the change

One line of new work. `add_message()` now writes through:

```python
def add_message(self, message: Message) -> None:
    self.messages.append(message)                          # step 00 behavior
    history_msg = HistoryMessage.from_message(message)     # NEW
    self.history_store.save_message(self.session_id, history_msg)  # NEW
```

`build_messages()` is unchanged — it still just returns system prompt + in-memory list. We never re-read from disk during a live session; the in-memory list is the source of truth *during* a session.

### 3. `src/mybot/core/agent.py` — wire in the store

```python
def __init__(self, agent_def, config):
    ...
    self.history_store = HistoryStore.from_config(config)

def new_session(self, session_id=None):
    session_id = session_id or str(uuid.uuid4())
    ...
    state = SessionState(
        session_id=session_id,
        agent=self,
        messages=[],
        history_store=self.history_store,
    )
    session = AgentSession(agent=self, state=state, tools=tools)
    self.history_store.create_session(self.agent_def.id, session_id)
    return session
```

Agent holds the `HistoryStore`. Each new session gets a row in `index.jsonl` and a `state` that knows how to persist.

The rest of `AgentSession.chat()` is unchanged from step 02. Persistence happens inside `state.add_message()`, transparently.

## Try it out

```bash
uv run my-bot chat

You: my name is Zane
pickle: Nice to meet you, Zane!
You: quit
```

Then peek at what was written:

```bash
ls ../default_workspace/.history/sessions/
# one .jsonl file, named by UUID

cat ../default_workspace/.history/sessions/<uuid>.jsonl
# one JSON object per line — your conversation

cat ../default_workspace/.history/index.jsonl
# the session index, with your session's title "my name is Zane"
```

Step 04 will add a `/resume` slash command to pick up an existing session. For now, the important thing is: your words survived the CLI exit.

## Exercises

1. **Follow a session on disk.** In one terminal, run `my-bot chat`. In another, `tail -f ../default_workspace/.history/sessions/<session-id>.jsonl`. Type messages in the first terminal; watch them appear in the second. This is why JSONL matters.

2. **Corrupt a history file.** Pick a session file. Append garbage to a line, mid-conversation. `my-bot chat` (in step 04 once resume is added) will skip the bad line and keep going. The store is tolerant by design.

3. **Measure index growth.** Create ~20 sessions (send one message each). Time how long `save_message()` takes on the 20th session. It's still fast — but notice the O(N) index rewrite. What would you change to fix it?

4. **Read the history programmatically.**
   ```python
   from mybot.core.history import HistoryStore
   from pathlib import Path
   store = HistoryStore(Path("../default_workspace/.history"))
   for s in store.list_sessions():
       print(s.title, s.message_count)
   ```
   This is the same API step 04's `/resume` uses.

## What breaks next

You can now pick up where you left off — if you know the session ID. You don't have a way to list your past sessions or switch between them interactively. That's what step 04 adds: **slash commands** like `/resume`, `/list`, `/context`.

## What's Next

[Step 04: Slash Commands](../04-slash-commands/) — direct user control over sessions.
