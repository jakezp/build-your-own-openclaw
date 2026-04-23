# Step 16: Concurrency Control

> Too many pickles running at the same time?

## Prerequisites

- Steps 00–15 done.

```bash
cd 16-concurrency-control
uv sync
```

## Why this step exists

By step 15, a single user turn can spawn a tree of sessions: pickle handles the top, pickle dispatches cookie, cookie dispatches scout, scout calls pickle back for something. Four concurrent sessions from one prompt. And that's one user — scale to ten Telegram users and a cron job firing at the same time and you've got dozens of parallel LLM calls.

Every one of those calls does the same thing:

- Reads the access token (acquiring the `ChatGPTOAuth.asyncio.Lock` — step 000).
- Streams the Responses API SSE.
- Potentially triggers a refresh if the token expired mid-burst.

Two failure modes emerge:

1. **Rate limits.** The ChatGPT subscription backend enforces per-account rate caps. Twenty simultaneous requests hit the cap; some get rejected.
2. **OAuth refresh storms.** If the token expires at a bad moment, many sessions all wait on `_refresh()` at once. The lock serializes them, but the queue wall-clock adds up.

We don't WANT to drop rate — we want to stay under it. We don't WANT to stall — we want to let the token refresh quietly. The answer is **per-agent concurrency caps**: each agent can run at most N sessions at once. Extra sessions queue until a slot opens.

This step is a narrow one, surgically changing `AgentWorker` to add a `semaphore` per agent, plus one new field on `AgentDef`.

## The mental model

Two kinds of locks already exist or appear in this tutorial. Worth distinguishing because people conflate them:

- **`asyncio.Lock` inside `ChatGPTOAuth`** (since step 000): a **correctness** lock. It protects the read-refresh-write cycle on the Token_Store. Without it, two concurrent refreshes race and corrupt the stored credentials. Always held briefly.

- **Per-agent `asyncio.Semaphore` in `AgentWorker`** (this step): a **throttling** lock. It caps how many agent sessions can run concurrently. Held for the entire duration of `exec_session` — potentially seconds per LLM turn.

A semaphore with `value=N` lets at most N coroutines through at once. Beyond N, callers wait. This is exactly the rate-limiting tool you want here.

## Key decisions

### Decision 1: scope the limit to the agent, not the process

Why not one global concurrency cap for the whole process?

Because different agents have different costs. A `pickle` chat turn is cheap — 1-2 LLM calls. A `scout` research turn is expensive — many tool calls, each its own LLM round. A `cookie` memory query is super cheap. One global cap would either let cookies starve scouts (cap too low) or let scouts trample the account (cap too high).

Per-agent:

```python
self._semaphores: dict[str, asyncio.Semaphore] = {}
```

When a session wants to run, we look up (or create) the semaphore for its agent:

```python
sem = self._get_or_create_semaphore(agent_def)
async with sem:
    # ... run the session
```

Different agents have independent budgets.

### Decision 2: `max_concurrency` is per-agent config

A new field on `AgentDef`, loaded from `AGENT.md` frontmatter:

```python
max_concurrency: int = Field(default=1, ge=1)
```

Default is `1` — conservative. If you don't set it, the agent runs strictly one session at a time. Safe but slow.

```markdown
---
name: Cookie
description: Memory specialist
max_concurrency: 5
---
```

Cookie is cheap and can run 5 in parallel. Pickle might set `max_concurrency: 2` because it does long tool loops. Scout might set `max_concurrency: 1` to avoid hammering Brave Search.

The `ge=1` constraint means you can't accidentally disable an agent by setting 0.

### Decision 3: lazy semaphore creation + cleanup

Semaphores are created on first use:

```python
def _get_or_create_semaphore(self, agent_def) -> asyncio.Semaphore:
    if agent_def.id not in self._semaphores:
        self._semaphores[agent_def.id] = asyncio.Semaphore(agent_def.max_concurrency)
    return self._semaphores[agent_def.id]
```

Why lazy? Because most of the time you only use a few agents. Creating a semaphore for every agent in `discover_agents()` would waste memory on agents you never invoke. Lazy creation means "pay as you go."

Cleanup is the counterpart:

```python
def _maybe_cleanup_semaphores(self, agent_def) -> None:
    if agent_def.id not in self._semaphores:
        return
    if not self._semaphores[agent_def.id]._waiters:
        del self._semaphores[agent_def.id]
```

After a session ends, if no other callers are waiting, we drop the semaphore. On the next invocation, it gets recreated. Why bother? Because config changes `max_concurrency` via `AGENT.md` edits (step 08 hot reload). If we kept the old semaphore, the new value wouldn't take effect until restart. Dropping it means the next use rebuilds from the current config.

Downside: `._waiters` is a private attribute of `asyncio.Semaphore`. Depending on it is fragile — a future Python might rename it. We accept that risk; the alternative is wrapping `asyncio.Semaphore` in a custom class that tracks waiters ourselves.

### Decision 4: lock the whole session, not just the LLM call

The `async with sem:` block wraps the entire session processing — including tool calls, history reads, the whole `chat()` loop:

```python
async with sem:
    try:
        agent = Agent(agent_def, self.context)
        ...
        response = await session.chat(event.content)
        ...
    except Exception as e:
        ...
```

Why not just the LLM calls? Because a session might:

- Loop through multiple tool calls, each an LLM turn.
- Dispatch a sub-agent, which holds its own semaphore slot.
- Call `post_message`, which generates another event.

If we released the semaphore between LLM turns, we'd let another session preempt the same agent mid-flight. That would break "this agent is allocated to this session end-to-end" — which is what users expect.

The cost: a long session holds the slot for minutes. If `max_concurrency=1` and a session runs for five minutes, nothing else with that agent can run in that window. Make max_concurrency match the expected load.

## Read the code

### 1. `src/mybot/core/agent_loader.py` — one new field

```python
class AgentDef(BaseModel):
    id: str
    name: str
    description: str = ""
    agent_md: str
    soul_md: str = ""
    llm: LLMConfig
    allow_skills: bool = False
    max_concurrency: int = Field(default=1, ge=1)    # NEW
```

And in `_parse_agent_def`:

```python
return AgentDef(
    ...
    max_concurrency=frontmatter.get("max_concurrency", 1),
)
```

Trivial: frontmatter `max_concurrency: 5` → `agent_def.max_concurrency == 5`.

### 2. `src/mybot/server/agent_worker.py` — the semaphore management

```python
class AgentWorker(SubscriberWorker):
    CLEANUP_THRESHOLD = 5

    def __init__(self, context: "SharedContext"):
        super().__init__(context)
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        ...

    async def exec_session(self, event, agent_def) -> None:
        sem = self._get_or_create_semaphore(agent_def)
        session_id = event.session_id

        async with sem:
            try:
                ...
                response = await session.chat(event.content)
                ...
            except Exception as e:
                ...

        self._maybe_cleanup_semaphores(agent_def)

    def _get_or_create_semaphore(self, agent_def) -> asyncio.Semaphore:
        if agent_def.id not in self._semaphores:
            self._semaphores[agent_def.id] = asyncio.Semaphore(agent_def.max_concurrency)
        return self._semaphores[agent_def.id]

    def _maybe_cleanup_semaphores(self, agent_def) -> None:
        if agent_def.id not in self._semaphores:
            return
        if not self._semaphores[agent_def.id]._waiters:
            del self._semaphores[agent_def.id]
```

Every session acquires the agent's semaphore before running. If the semaphore is full, `async with sem:` blocks until someone else releases. After the session ends, the finally-equivalent cleanup drops empty semaphores.

## Try it out

Edit pickle's AGENT.md to add `max_concurrency: 1`:

```markdown
---
name: Pickle
description: Friendly cat assistant
max_concurrency: 1
---
...
```

Now send two messages to pickle in quick succession from two different channels (or open two WebSocket connections). Add `print(f"[sem] acquired for {agent_def.id}")` before `async with sem:` and `print(f"[sem] released for {agent_def.id}")` after. You'll see sequential execution even though events arrived concurrently.

Change `max_concurrency: 3` — now the two messages process in parallel.

## Exercises

1. **Observe queueing.** Set `max_concurrency: 1` on pickle. Send three messages within a second. The log shows three acquires and three releases in strict sequence. Time them: the third message's total latency is the sum of the first two.

2. **Break the ge=1 constraint.** Edit AGENT.md to `max_concurrency: 0`. Reload. The Config validator raises. What does the log say? (Your agent is temporarily unusable until you fix the YAML — a real cost of strict validators.)

3. **Pick a sweet spot for scout.** If scout makes ~3 tool calls per session, and each tool call takes ~2s, a session is ~6s. If ChatGPT's subscription backend caps you at ~10 rpm on `gpt-5.4`, what `max_concurrency` keeps scout under the cap without starvation?

4. **Observe cleanup.** Run the two-session scenario, then wait a beat and add `print(self._semaphores)` at the top of `dispatch_event`. You should see an empty dict between sessions. Then send a new message — it's repopulated.

## What breaks next

Concurrency is tamed. But the agent's memory is still per-session. Reset a session (or start a fresh one from a different channel) and pickle forgets everything you've told it. "You mentioned you prefer vi over emacs last week" isn't something the agent can recall — that was a different session.

Step 17 adds memory: a `cookie` subagent with filesystem tools pointed at `workspace/memories/`. Pickle consults cookie to remember and recall. Memory is just markdown files.

## What's Next

[Step 17: Memory](../17-memory/) — remember me.
