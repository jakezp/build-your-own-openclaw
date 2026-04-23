# Step 15: Agent Dispatch

> Your agent wants friends to work with.

## Prerequisites

- Steps 00–14 done.
- At least two agents configured in `default_workspace/agents/` (e.g. `pickle` and `cookie`).

```bash
cd 15-agent-dispatch
uv sync
```

## Why this step exists

The main agent (say, `pickle`) has seen tools (step 01), skills (step 02), web search (step 06), a big bag of tricks. But every tool call is `pickle` doing the work itself.

What if you want pickle to ask ANOTHER AGENT to help?

- Pickle is a general assistant. For anything memory-related, it should consult `cookie` — the memory specialist.
- Pickle is working on a writing task. For research, it dispatches `scout` — the web-aware researcher.
- A one-off cron triggers a `summarizer` agent to produce a daily report; the summarizer itself asks `cookie` for context first.

One agent talking to another isn't the same as one agent using a tool. A sub-agent has its own persona, its own tools, its own view of the world. It runs its own LLM call. It's a **recursive session**.

This step adds `subagent_dispatch` — a tool the parent agent uses to spawn a sub-session. The sub-session runs end-to-end (including its own tool calls if needed) and returns a result the parent sees like a normal tool output.

## The mental model

A sub-agent call is an async event round-trip:

```
  pickle                                         cookie
     │                                              │
     │  1. calls subagent_dispatch("cookie", ...)   │
     │                                              │
     │  2. publishes DispatchEvent                  │
     │───────────►  EventBus  ─────────────────────►│
     │                                              │
     │                                              │  3. new session,
     │                                              │     cookie processes
     │                                              │     the task
     │                                              │
     │  4. publishes DispatchResultEvent            │
     │◄───────────  EventBus  ──────────────────────│
     │                                              │
     │  5. tool returns with the result             │
     │                                              │
```

Key shape: the `subagent_dispatch` tool is async and **waits** for a matching `DispatchResultEvent` before returning. That wait is implemented with an `asyncio.Future` the tool subscribes to the bus for, filtered by `session_id`.

From pickle's perspective, it's one tool call. From the system's perspective, it's two sessions running concurrently — pickle's outer session paused on the `await`, cookie's sub-session doing the work.

## Key decisions

### Decision 1: the sub-agent runs as a first-class session

Cookie's sub-session goes through `AgentWorker` exactly like a user-triggered session. It has its own history (persisted), its own tool calls (with their own loop), its own compaction if needed.

Why not just... call cookie's LLM directly? Because the whole point is that cookie has agency. Maybe cookie needs its tools. Maybe it needs to think for multiple turns. Maybe it dispatches its OWN sub-agent. All of that works because the sub-session IS a session.

The cost: recursion complexity. A sub-agent could in theory dispatch its parent's agent, creating a loop. We don't guard against that here. Step 16 adds concurrency limits that put a natural ceiling on runaway recursion.

### Decision 2: dynamic tool schema

`create_subagent_dispatch_tool(current_agent_id, context)` builds the tool's schema at call site. The schema knows:

- Which agents are available (via `agent_loader.discover_agents()`).
- Which ones aren't the CURRENT agent (you can't dispatch yourself — that would be a direct loop).

```python
dispatchable_agents = [a for a in available_agents if a.id != current_agent_id]
```

The tool description embeds the list of available agents using XML tags so the model sees it as structured data:

```
<available_agents>
  <agent id="cookie">Memory specialist. Knows what the user has told you before.</agent>
  <agent id="scout">Research specialist. Has web_search and web_read.</agent>
</available_agents>
```

Same trick as the skill tool (step 02).

### Decision 3: future-based bridge between async patterns

The `subagent_dispatch` tool is an async coroutine. Inside, it creates an `asyncio.Future`, subscribes a handler to `DispatchResultEvent`, publishes a `DispatchEvent`, and `await`s the future.

```python
loop = asyncio.get_running_loop()
result_future: asyncio.Future[str] = loop.create_future()

async def handle_result(event: DispatchResultEvent) -> None:
    if event.session_id == session_id:
        if not result_future.done():
            if event.error:
                result_future.set_exception(Exception(event.error))
            else:
                result_future.set_result(event.content)

shared_context.eventbus.subscribe(DispatchResultEvent, handle_result)
try:
    await shared_context.eventbus.publish(event)
    response = await result_future
finally:
    shared_context.eventbus.unsubscribe(handle_result)
```

This is the bridge pattern — turning an async event-publish/subscribe roundtrip into a single `await`. The handler sets the future; the caller awaits the future. When a matching result event arrives, the future resolves. Anything else goes to other subscribers.

The `finally` unsubscribe matters. Without it, every dispatch leaves a dangling handler on the bus — slow leak.

### Decision 4: sub-agent gets its own session, not the parent's

```python
agent = Agent(agent_def, shared_context)
agent_source = AgentEventSource(agent_id=current_agent_id)
agent_session = agent.new_session(agent_source)
```

The sub-agent creates a fresh session. Not the parent's. Two consequences:

- **Isolation.** Cookie's history doesn't pollute pickle's history. Cookie might do 20 LLM turns to answer a question; pickle sees only the final one-line answer.
- **Identity for routing.** The source of sub-agent events is `AgentEventSource("pickle")` — the PARENT's id. This is what step 13's channel hint uses ("You are running as a dispatched subagent. Your response will be sent to main agent.").

The sub-agent's session has `parent_session_id` for audit / debugging. You can trace "this cookie session was spawned by pickle session X."

### Decision 5: the tool returns JSON, not prose

```python
result = {"result": response, "session_id": session_id}
return json.dumps(result)
```

Why a JSON blob? Because the parent agent's next turn sees this as the tool's output. Structured output means the parent can reason about the sub-session — "cookie said X, which came from session abc123." If the user later asks "what did cookie say yesterday?" the parent has the session id to look up.

The tradeoff: the parent has to parse JSON in its head. For recent models this is fine; for smaller models it's a tax.

## Read the code

### 1. `src/mybot/tools/subagent_tool.py` — the factory

```python
def create_subagent_dispatch_tool(
    current_agent_id: str,
    context: "SharedContext",
) -> BaseTool | None:
    available_agents = context.agent_loader.discover_agents()
    dispatchable_agents = [a for a in available_agents if a.id != current_agent_id]

    if not dispatchable_agents:
        return None

    # Build dynamic description listing available agents
    agents_desc = "<available_agents>\n"
    for agent_def in dispatchable_agents:
        agents_desc += f'  <agent id="{agent_def.id}">{agent_def.description}</agent>\n'
    agents_desc += "</available_agents>"

    dispatchable_ids = [a.id for a in dispatchable_agents]

    @tool(
        name="subagent_dispatch",
        description=f"Dispatch a task to a specialized subagent.\n{agents_desc}",
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "enum": dispatchable_ids, ...},
                "task": {"type": "string", ...},
                "context": {"type": "string", ...},
            },
            "required": ["agent_id", "task"],
        },
    )
    async def subagent_dispatch(agent_id, task, session, context=""):
        # ... create session, publish DispatchEvent, await DispatchResultEvent
```

The `enum` on `agent_id` keeps the model from calling a nonexistent agent.

### 2. `DispatchEvent` and `DispatchResultEvent`

New event types in `src/mybot/core/events.py`:

```python
@dataclass
class DispatchEvent(Event):
    parent_session_id: str | None = None
    retry_count: int = 0

@dataclass
class DispatchResultEvent(Event):
    error: str | None = None
```

`DispatchEvent` has the same shape as `InboundEvent` — retry-safe, session-scoped — plus `parent_session_id` for tracing. `DispatchResultEvent` mirrors `OutboundEvent` — a response to a dispatched event.

### 3. `AgentWorker` subscribes to both

```python
self.context.eventbus.subscribe(InboundEvent, self.dispatch_event)
self.context.eventbus.subscribe(DispatchEvent, self.dispatch_event)
```

Same handler, two event types. The handler's `_emit_response` branches on the event type:

```python
if isinstance(event, DispatchEvent):
    result_event = DispatchResultEvent(session_id=event.session_id, ...)
else:
    result_event = OutboundEvent(session_id=event.session_id, ...)
```

A dispatch gets a result event (captured by the awaiting tool); an inbound gets an outbound event (delivered to a channel).

## Try it out

Make sure `cookie` is set up (it usually ships with the tutorial):

```bash
ls ../default_workspace/agents/cookie/AGENT.md
```

Chat with pickle, ask it to consult cookie:

```
You: Ask the cookie agent to remind me what my favorite color is.
pickle: [calls subagent_dispatch(agent_id="cookie", task="What is the user's favorite color?")]
pickle: [receives cookie's reply]
pickle: Cookie says your favorite color is blue — based on something you mentioned last week.
```

Check the history store for both sessions. Two session files exist — pickle's and cookie's.

## Exercises

1. **Trace the dispatch.** Add `print(f"[dispatch] {agent_id}: {task[:40]}")` at the top of `subagent_dispatch`. Ask pickle to consult cookie. Watch the dispatch happen, then (optionally) cookie dispatching ITSELF to another agent if you've set up a third.

2. **Enforce a timeout.** Wrap the `await result_future` in `asyncio.wait_for(..., timeout=30)`. A sub-agent that takes too long now fails instead of hanging. What does the parent see in that case? How would you improve the error message?

3. **Break self-dispatch.** The `dispatchable_agents` filter excludes `current_agent_id`. Remove that filter. Try to get pickle to dispatch pickle. Watch what happens. Why is the filter there?

4. **Build a pipeline.** Configure three agents: `researcher` (web search), `writer` (drafts), `editor` (polish). Pickle orchestrates: dispatch researcher, dispatch writer with researcher's result, dispatch editor with writer's result. Then return the final to the user. Small writing agency in one chat.

## What breaks next

Multi-agent dispatch means recursion. A single user message can spawn pickle → cookie → scout → cookie — four concurrent sub-sessions. Ten users chatting to pickle all at once means dozens of parallel LLM calls, all needing access tokens, all touching the same Token_Store.

Without limits, you're hammering the LLM endpoint and the OAuth refresh. Step 16 adds per-agent concurrency caps — each agent runs at most N sessions at once. The extras queue.

## What's Next

[Step 16: Concurrency Control](../16-concurrency-control/) — too many pickles running at the same time?
