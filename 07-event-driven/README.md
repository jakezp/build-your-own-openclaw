# Step 07: Event-Driven Architecture

Replace direct `chat()` calls with event-driven architecture using EventBus and Workers for scalability.

## What We Will Build

```
┌─────────────┐                    ┌──────────────┐
│   CLI       │──InboundEvent────▶│  EventBus    │
│             │                    │              │
│             │◀──OutboundEvent───│              │
└─────────────┘                    └──────┬───────┘
                                          │
                                          ▼
                                   ┌──────────────┐
                                   │ AgentWorker  │
                                   │              │
                                   │ - Executes   │
                                   │   sessions   │
                                   └──────────────┘
```

**Key Components:**
- **EventBus** - Central pub/sub for event distribution
- **Event Types** - InboundEvent, OutboundEvent
- **Workers** - Background tasks that process events
- **AgentWorker** - Handles InboundEvent → executes agent session → emits OutboundEvent

## Key Changes

### 1. Event Types ([src/mybot/core/events.py](src/mybot/core/events.py))

```python
@dataclass
class InboundEvent(Event):
    """Event for external work entering the system."""
    session_id: str
    content: str
    retry_count: int = 0

@dataclass
class OutboundEvent(Event):
    """Event for agent responses."""
    session_id: str
    content: str
    error: str | None = None
```

### 2. EventBus ([src/mybot/core/eventbus.py](src/mybot/core/eventbus.py))

```python
class EventBus(Worker):
    def __init__(self, context):
        self._subscribers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    async def run(self) -> None:
        while True:
            event = await self._queue.get()
            await self._dispatch(event)
```

### 3. AgentWorker ([src/mybot/server/agent_worker.py](src/mybot/server/agent_worker.py))

```python
class AgentWorker(SubscriberWorker):
    def __init__(self, context):
        self.context.eventbus.subscribe(InboundEvent, self.dispatch_event)

    async def dispatch_event(self, event: InboundEvent):
        agent = Agent(agent_def, self.context)
        session = agent.resume_session(event.session_id)
        response = await session.chat(event.content)

        result = OutboundEvent(
            session_id=event.session_id,
            content=response,
        )
        await self.context.eventbus.publish(result)
```

### 4. CLI Uses Events ([src/mybot/cli/chat.py](src/mybot/cli/chat.py))

```python
class ChatLoop:
    def __init__(self, config: Config):
        self.context = SharedContext(config)
        self.workers = [self.context.eventbus, AgentWorker(self.context)]
        self.context.eventbus.subscribe(OutboundEvent, self.handle_outbound_event)

    async def run(self):
        for worker in self.workers:
            worker.start()

        event = InboundEvent(
            session_id=session_id,
            content=user_input,
        )
        await self.context.eventbus.publish(event)

        response = await self.response_queue.get()
        self.display_agent_response(response.content)
```

### 5. SharedContext ([src/mybot/core/context.py](src/mybot/core/context.py))

```python
class SharedContext:
    """Global shared state for the application."""

    def __init__(self, config: Config):
        self.config = config
        self.history_store = HistoryStore.from_config(config)
        self.agent_loader = AgentLoader.from_config(config)
        self.skill_loader = SkillLoader.from_config(config)
        self.command_registry = CommandRegistry.with_builtins()
        self.eventbus = EventBus(self)
```

## How to Run

```bash
cd 07-event-driven
uv run my-bot chat
```

Example interaction:
```
You: What tools do you have?
default: I have access to read, write, and bash tools for file operations
         and command execution, plus web_search and web_read tools for
         internet access.
```

## What's Next

Step 08 will add **Config Hot Reload** - automatically reload configuration when files change without restarting.
