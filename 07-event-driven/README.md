# Step 07: Event-Driven Architecture

> Refactor the agent around an event bus so it can serve more than one input stream.

## Prerequisites

- Steps 00–06 done.

```bash
cd 07-event-driven
uv sync
```

## Why this step exists

Every step so far has been a single-threaded, single-user CLI. `input()` blocks until you type something, the agent handles it, the loop comes back for the next message. One user, one terminal, one session.

That shape breaks the moment you want:

- **Multiple input sources.** A CLI user, a Telegram bot, a Discord DM, a WebSocket client — each producing events concurrently.
- **Multiple output destinations.** Agent responses need to be routed back to whoever asked, which might be a different "platform" per session.
- **Background work.** Cron jobs, scheduled pings, proactive notifications — work the agent does without a human prompt.
- **Decoupling.** The CLI shouldn't have to know how Telegram works. Telegram shouldn't have to know how the LLM works.

The fix is an **event-driven architecture**. A central `EventBus` carries typed events between decoupled `Worker`s. The CLI, the LLM dispatcher, and (in later steps) each channel integration are all workers subscribing to or publishing events on the bus.

This step introduces the bus, the worker base class, two typed events (`InboundEvent` / `OutboundEvent`), and refactors the agent to run as an `AgentWorker`. It's a structural step — functionally, the CLI still chats the same way, but now it does so through the bus.

## The mental model

```
           InboundEvent                OutboundEvent
  ┌──────┐    ─────►    ┌──────────┐    ─────►    ┌──────────────┐
  │ CLI  │              │ EventBus │              │ Delivery     │
  │ (Ch) │    ◄─────    │          │    ◄─────    │ Worker (Ch)  │
  └──────┘              └────┬─────┘              └──────────────┘
                             │
                             ▼
                       ┌─────────────┐
                       │ AgentWorker │  ← subscribes to InboundEvent,
                       └─────────────┘    publishes OutboundEvent
```

- The **CLI** (in this step, the only inbound source) reads a user message and publishes an `InboundEvent`.
- The **EventBus** queues it and fans it out to subscribers.
- The **AgentWorker** subscribes to `InboundEvent`, spawns an async task to process it, and publishes an `OutboundEvent` with the response.
- The **CLI's delivery handler** subscribes to `OutboundEvent` and prints to the terminal.

In step 08, hot-reload becomes a worker. In step 09, each channel (Telegram, Discord) becomes a pair of (inbound-publisher, outbound-handler) workers. In step 12, cron becomes a worker that publishes inbound events on a schedule. The bus is the integration seam.

## Key decisions

### Decision 1: typed events, not dicts

Every event is a dataclass:

```python
@dataclass
class Event:
    session_id: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class InboundEvent(Event):
    retry_count: int = 0


@dataclass
class OutboundEvent(Event):
    error: str | None = None
```

Why not just dicts? Because the bus dispatches by **type**:

```python
bus.subscribe(InboundEvent, worker.handle)
```

The subscription is keyed on the class. A handler for `InboundEvent` gets only inbound events. A dict-based bus would need string keys ("inbound", "outbound") and error-prone string matching. Type-based dispatch is checked at class definition time — mistakes show up as compile errors or at subscribe time, not as silent dropped events.

### Decision 2: subscriber-only workers

Look at `Worker` vs `SubscriberWorker`:

```python
class Worker(ABC):
    @abstractmethod
    async def run(self) -> None: ...

class SubscriberWorker(Worker):
    async def run(self) -> None:
        try:
            await asyncio.Future()   # block forever
        except asyncio.CancelledError:
            pass
```

`EventBus` is a `Worker` — it has an active `run()` loop that pulls events from its queue and dispatches them. `AgentWorker` is a `SubscriberWorker` — it doesn't have an active loop; it just subscribes to events in `__init__` and reacts when they arrive.

The distinction matters for orchestration: every worker must have a `run()` method so the main process can track it (is it running? has it crashed?). But many workers are purely reactive and don't need their own loop. `SubscriberWorker.run()` is a convenient "wait forever for cancellation" base.

### Decision 3: retry with `dataclasses.replace`

If the agent fails to process an `InboundEvent` (LLM timeout, tool crash, etc), `AgentWorker` can republish the event with an incremented retry count:

```python
retry_event = replace(event, retry_count=event.retry_count + 1, content=".")
await self.context.eventbus.publish(retry_event)
```

Three retries cap it (`MAX_RETRIES = 3`), then the agent gives up and emits an `OutboundEvent` with `error=<message>`. The cap prevents infinite loops.

`dataclasses.replace` is the clean way to make a modified copy of a frozen-style dataclass without mutating the original. It's idiomatic.

### Decision 4: `SharedContext` as the composition root

All the long-lived singletons (config, stores, loaders, the bus itself) live on a single `SharedContext` object:

```python
class SharedContext:
    config: Config
    history_store: HistoryStore
    agent_loader: AgentLoader
    skill_loader: SkillLoader
    command_registry: CommandRegistry
    eventbus: EventBus
```

Every worker gets a reference to this context in its constructor. Every event dispatch has access to everything it might need. There's exactly one instance per process.

This is the Python version of a dependency-injection container. It's not fancy — just an object with instance attributes — but it means no hidden globals. Every worker can trace every dependency to the `SharedContext` it was given.

### Decision 5: in-memory queue, no durability (yet)

`EventBus._queue` is an `asyncio.Queue`. If the process crashes mid-event, the queue evaporates. That's a real risk for anything important (a user's message, a retry attempt).

We accept this cost in step 07 because durability is a big feature that deserves its own step. Step 09's channels add durability at the per-channel level (an undelivered OutboundEvent gets stored for later delivery). Step 16 introduces broader concurrency and backpressure concerns. Step 07 is the foundation; later steps harden it.

## Read the code

### 1. `src/mybot/core/eventbus.py` — the dispatcher

Three methods matter:

```python
def subscribe(self, event_class: type[E], handler) -> None:
    self._subscribers[event_class].append(handler)

async def publish(self, event: Event) -> None:
    await self._queue.put(event)

async def run(self) -> None:
    while True:
        event = await self._queue.get()
        try:
            await self._dispatch(event)
        finally:
            self._queue.task_done()
```

`publish` is non-blocking (returns as soon as the event is queued). `run` is the active loop — it pulls from the queue, calls `_dispatch` which fans out to all subscribers via `asyncio.gather`. Errors in one handler don't stop the others.

### 2. `src/mybot/server/worker.py` — the lifecycle base

Every worker has `start()`, `is_running()`, `has_crashed()`, `stop()`. The main process orchestrates them: start them all at boot, monitor for crashes, stop them cleanly on shutdown. This is the shape step 07–17 build on.

### 3. `src/mybot/server/agent_worker.py` — the agent-as-worker

`AgentWorker` subscribes to `InboundEvent` in its constructor. When one arrives, it:

1. Looks up which agent owns the session (from `history_store.get_session_info`).
2. Loads the agent definition.
3. Spawns a **separate task** (`asyncio.create_task(self.exec_session(...))`) to process it.
4. Returns immediately — the bus isn't blocked while the agent thinks.

The separate-task trick means multiple `InboundEvent`s can be in flight concurrently. One user's long chat doesn't block another user's short question.

Inside `exec_session`: same logic as the old `AgentSession.chat()`, but publishes an `OutboundEvent` when done.

### 4. `src/mybot/core/context.py` — the composition root

The `SharedContext` builds itself from a `Config`. In the main entrypoint (not shown, but in `cli/chat.py`), you construct a `SharedContext`, start the `EventBus`, start the `AgentWorker` (which subscribes during init), start the CLI's delivery handler, and then read from stdin in a loop — publishing `InboundEvent`s as the user types.

## Try it out

```bash
uv run my-bot chat
```

The experience is unchanged. You type, you see replies. Behind the scenes, every message is a round trip through the event bus.

To see it at work, add a log line. In `agent_worker.py` at the start of `dispatch_event`:

```python
print(f"[BUS] InboundEvent received: session={event.session_id}, content={event.content[:50]!r}")
```

And in the CLI's outbound handler (look in `cli/chat.py`), add:

```python
print(f"[BUS] OutboundEvent received: content={event.content[:50]!r}")
```

Now each turn produces two log lines bracketing the LLM call.

## Exercises

1. **Add a logger worker.** Make a `LoggerWorker(SubscriberWorker)` that subscribes to both `InboundEvent` and `OutboundEvent` and logs every message to a file. No other code changes needed — that's the win of the bus.

2. **Trigger a synthetic inbound event.** After starting the server, from a Python REPL, `await context.eventbus.publish(InboundEvent(session_id="...", content="hello"))`. Watch the agent respond as if you'd typed it.

3. **Break retry.** Make `exec_session` raise an exception unconditionally. Send a message. Watch the retry loop fire three times, then see the final error event. The `content="."` on retry is a design quirk — think about why (the original content might be the trigger for the failure).

4. **Check a crashed worker.** Make the bus's `run()` raise a `ValueError` at startup. The `has_crashed()` / `get_exception()` methods on the worker let a supervisor notice. Write a tiny supervisor function that watches workers and logs crashes.

## What breaks next

You have an event bus, but the config still has to be loaded at startup. Edit `config.user.yaml` in a running process and nothing updates — you restart the CLI to pick up changes.

Step 08 adds hot reload: a `watchdog`-based worker watches the config file, reloads on change, republishes relevant state into the `SharedContext`. The bus is what makes this clean — config-change events go through the same machinery as every other event.

## What's Next

[Step 08: Config Hot Reload](../08-config-hot-reload/) — edit config without restart.
