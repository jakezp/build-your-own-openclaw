# Step 17: Memory

> Remember me.

## Prerequisites

- Steps 00–16 done.

```bash
cd 17-memory
uv sync
```

## Why this step exists

Every session is an island. Step 03 gave you persistence within a session — the messages don't evaporate on `quit` — but sessions don't share state.

Consider:

- You told pickle on Monday that you prefer vim over emacs.
- Tuesday, in a new session (maybe from Telegram instead of CLI), you ask pickle "what editor should I use?"
- Pickle has no idea you mentioned vim last time. That was a different session.

Session-local memory is fine for short chats. It fails for any ongoing relationship.

What you need is **cross-session memory**. Something the agent consults to remember what you've told it before. Something that survives channels, restarts, and compaction.

This step adds it. And it does so without any new infrastructure: memory is just markdown files in `workspace/memories/`, managed by a subagent (`cookie`, from the dispatch pattern in step 15). Pickle never touches the filesystem directly — it dispatches cookie, cookie reads/writes memory files, cookie returns a summary.

This is the final step of the main tutorial.

## The mental model

Two agents collaborating, like step 15 set up:

```
   You:        "What editor do I prefer?"
       │
       ▼
   pickle (main agent, talks to you)
       │
       │ subagent_dispatch(agent_id="cookie", task="retrieve: user's editor preference")
       ▼
   cookie (memory agent, talks to filesystem)
       │
       │ read(path="workspace/memories/topics/preferences.md")
       ▼
   cookie replies: "The user prefers vim over emacs (mentioned 2026-04-01)."
       │
       ▼
   pickle replies to you: "You prefer vim. Shall I help set up your .vimrc?"
```

Cookie has:

- A memory agent persona (`default_workspace/agents/cookie/AGENT.md`).
- The filesystem tools from step 01 (`read`, `write`, `edit`, `bash`).
- Its own LLM calls.
- A single job: read, write, and organize markdown files under `workspace/memories/`.

Pickle knows cookie exists (via step 13's layered prompt listing available agents). When pickle thinks memory is involved, it dispatches. When memory should be saved, it dispatches with "store this" instructions.

The memory itself is not a database, not an embedding index, not a graph. Just markdown:

```
workspace/memories/
├── topics/                  # timeless facts
│   ├── preferences.md
│   ├── user-identity.md
│   └── ...
├── projects/                # per-project context
│   ├── my-bot-tutorial.md
│   └── ...
└── daily-notes/             # day-specific events
    ├── 2026-04-21.md
    └── ...
```

Cookie decides which file goes where based on its prompt (see `default_workspace/agents/cookie/AGENT.md`).

## Key decisions

### Decision 1: memory as a subagent, not a built-in tool

Pickle could have its own `save_memory` and `get_memory` tools directly. We chose dispatching cookie instead. Why?

- **Context separation.** Cookie can load a big memory index without polluting pickle's conversation. Pickle sees "cookie says: user prefers vim" — cookie has dozens of turns deciding WHICH memory to return, but pickle never sees that deliberation.
- **Specialization.** Cookie's prompt is entirely about memory structure and organization. Pickle's prompt is entirely about chatting helpfully. Two narrow prompts work better than one bloated prompt.
- **Tunable.** Want to change how memory is organized? Edit `cookie/AGENT.md`. Pickle doesn't care. No code change.

The cost: an extra LLM call per memory operation. That's real. A turn that involves memory takes 2-3 LLM calls instead of 1. We trade latency for clarity.

### Decision 2: markdown, not a database

No SQLite. No embeddings. No vector search. Just markdown files cookie reads with `read`, searches with `bash grep`, and writes with `write`.

Why so primitive?

- **Readable.** You can open `memories/topics/preferences.md` and see exactly what the agent "knows" about you.
- **Editable.** Don't like a memory? Delete it with your editor. The agent picks up the absence next query.
- **Git-diffable.** Commit your memories directory if you want version control.
- **Cheap.** No migration schemas, no indexes to rebuild.

The cost: no semantic search. If you have 500 memory files and ask "what does the agent know about my preferences?", cookie `grep`s its way through them, which gets slow beyond ~100 files. For a tutorial agent serving one user, that's fine. For a production system, you'd add an embedding backend.

### Decision 3: three-axis organization

`topics/`, `projects/`, `daily-notes/`. Why these three?

- **Topics** — timeless, not tied to a specific project or day. "User prefers vim."
- **Projects** — tied to a specific project. "In `my-bot-tutorial` we're using OAuth."
- **Daily notes** — dated, tied to a specific day. "On 2026-04-21 we shipped the OAuth Edition."

This gives the agent reasoning scaffolding. When storing, cookie decides: is this timeless (topic), project-specific (project), or a dated event (daily)? When retrieving, cookie knows where to look.

You could have other axes — `people/`, `places/`, whatever. The three above are a reasonable default. Cookie's prompt picks them.

### Decision 4: cookie's tools are the filesystem

Cookie has:

- `read(path)` — read a file.
- `write(path, content)` — create or overwrite.
- `edit(path, old_text, new_text)` — surgical edit.
- `bash(command)` — for `find` and `grep` searches across many files.

These are the same tools step 01 introduced. Cookie doesn't need anything special — just these four. The tools are scoped via the agent's working directory and the memory root is configured via `config.memories_path`.

The `{{memories_path}}` placeholder in cookie's AGENT.md is substituted at load time — the prompt always knows the correct memory root, even if you change `memories_path` in config.

## Read the code

### 1. `src/mybot/utils/config.py` — one new field

```python
class Config(BaseModel):
    ...
    memories_path: Path = Field(default=Path("memories"))
    ...

    @model_validator(mode="after")
    def resolve_paths(self) -> "Config":
        for field_name in (..., "memories_path", ...):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, self.workspace / path)
        return self
```

The entire code change in this step is this config field. Everything else is markdown:

- `default_workspace/agents/cookie/AGENT.md` — the memory agent's persona and rules.
- `default_workspace/memories/` — the directory cookie reads and writes.

### 2. `default_workspace/agents/cookie/AGENT.md` — the memory agent

```markdown
---
name: Cookie
description: Memory manager for storing, organizing, and retrieving memories
llm:
  temperature: 0.3
---

You are Cookie, the memory manager. You store, organize, and retrieve memories on behalf of Pickle.

## Role

You manage memories on behalf of Pickle, who is the main agent that talks directly to the human user. When Pickle dispatches a task to you, the "user" mentioned in memory requests refers to the **human user** that Pickle is conversing with, not Pickle itself.

## Memory Structure

Memories are stored at `{{memories_path}}` in three axes:

- **topics/** - Timeless facts (preferences, identity, relationships)
- **projects/** - Project-specific context, decisions, progress
- **daily-notes/** - Day-specific events and notes (YYYY-MM-DD.md)

## Operations

### Store
Create or update memory files using `write` tool. Choose appropriate axis based on content type.

### Retrieve
Use `read` tool to fetch specific memories. Use `bash` with `find` or `grep` to search across files.

### Organize
Periodically consolidate related memories, remove duplicates, update outdated information.
```

A low `temperature: 0.3` makes cookie more deterministic — we want consistent memory organization, not creative reorganization every turn.

### 3. `default_workspace/agents/pickle/AGENT.md` — pickle knows about cookie

Pickle's prompt (via step 13's layered build) includes a list of available agents. It sees cookie in that list with its description. When pickle reasons about something memory-related, it recognizes cookie as the right tool.

Pickle doesn't know HOW cookie works. It just knows "dispatch cookie with a memory task."

## Try it out

Make sure both pickle and cookie agents are set up (they ship with the tutorial's default_workspace). Ensure `memories_path` is in your config (or use the default `memories`).

Start the server:

```bash
uv run my-bot serve
```

Tell pickle something worth remembering:

```
You: I'm Zane. I prefer vim over emacs. My favorite color is blue.
pickle: [dispatches cookie to store these facts]
pickle: Got it, Zane. I'll remember.
```

Check the filesystem — you'll see new files:

```bash
ls ../default_workspace/memories/topics/
# something like: user-identity.md, preferences.md
```

Start a fresh session (quit, restart, or use a different channel):

```
You: What editor do I prefer?
pickle: [dispatches cookie to retrieve]
pickle: You prefer vim.
```

Memory across sessions. Cookie did the grunt work; pickle got the summary.

## Exercises

1. **Inspect a memory file.** Open one of the created markdown files. It's plain English, written by cookie. You can edit it by hand.

2. **Delete a memory.** `rm ../default_workspace/memories/topics/preferences.md`. Ask pickle about your preferences. Cookie greps, finds nothing, reports "no memory found."

3. **Watch the dispatch flow.** Run two terminals: `tail -f` on `~/.config/mybot/.history/sessions/` (both pickle's and cookie's sessions). Send pickle a memory task. Watch new messages land in both sessions — pickle's brief summary, cookie's detailed tool-call history.

4. **Scope check.** Remove the `{{memories_path}}` placeholder from cookie's AGENT.md and replace with `/tmp`. Rerun. Cookie now writes memories to `/tmp`, outside your workspace. This is cookie escaping its sandbox — a real risk that a production system would solve with path validation in the filesystem tools themselves.

## What breaks next — you've finished the tutorial

You started with a CLI that printed one reply to one question. You've built an agent that:

- Logs in via OAuth against your ChatGPT subscription.
- Holds conversations with persistent history.
- Uses tools, skills, and web search.
- Manages its own context when histories grow.
- Serves many users across Telegram, Discord, and WebSocket.
- Runs scheduled work via cron.
- Dispatches specialized subagents for memory and other tasks.
- Throttles itself with per-agent concurrency caps.
- Remembers you across sessions via the memory subagent.

Every piece was a single-step addition to a shape you already understood. No magic — just the same session / agent / worker / bus primitives used in different combinations.

From here, make it yours. Rename everything. Build your own agents for your own use cases. Add the features the tutorial left out — speech, images, structured output, multi-account, sandboxing. The foundation is small enough to extend without getting lost.

## What's Next

[Step 18: Customization](../18-customization/) — make it yours. Rename packages, bootstrap from scratch, ship your own tool.
