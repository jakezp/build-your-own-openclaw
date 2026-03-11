# Step 09: Channels - Multi-Platform Support

Extend the agent to support multiple messaging platforms (CLI, Telegram, Discord) through a unified channel abstraction.

## What We Will Build

```
┌────────────────────────────────────────────────────────────────────┐
│                            Server                                   │
│                                                                    │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │EventBus     │  │AgentWorker   │  │DeliveryWorker│             │
│  │             │  │              │  │              │             │
│  └─────────────┘  └──────────────┘  └──────────────┘             │
│         ▲                  ▲                  │                    │
│         │                  │                  │                    │
│         │            ┌─────┴─────┐            │                    │
│         │            │ Agent     │            │                    │
│         │            │ Session   │            │                    │
│         │            └───────────┘            │                    │
│         │                                     │                    │
│  ┌──────┴─────────────────────────────────────┴──────┐            │
│  │              ChannelWorker                        │            │
│  │                                                   │            │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐          │            │
│  │  │CLI      │  │Telegram │  │Discord  │          │            │
│  │  │Channel  │  │Channel  │  │Channel  │          │            │
│  │  └─────────┘  └─────────┘  └─────────┘          │            │
│  └──────────────────────────────────────────────────┘            │
│                                                                    │
│  ┌──────────────────────────────────────────────────────┐        │
│  │  RoutingTable                                        │        │
│  │  - Maps sources to agents                            │        │
│  │  - Manages session affinity                          │        │
│  └──────────────────────────────────────────────────────┘        │
└────────────────────────────────────────────────────────────────────┘
```

**Key Components:**
- **EventSource** - Abstract base for platform-specific event sources (CLI, Telegram, Discord)
- **Channel** - Abstract base for messaging platforms with run/reply/stop interface
- **ChannelWorker** - Manages multiple channels and publishes InboundEvents
- **DeliveryWorker** - Subscribes to OutboundEvents and delivers via appropriate channel
- **RoutingTable** - Maps sources to sessions and agents
- **Server** - Orchestrates all workers (EventBus, AgentWorker, ChannelWorker, DeliveryWorker)

## Key Changes

### 1. EventSource and Platform Sources ([src/mybot/core/events.py](src/mybot/core/events.py))

```python
class EventSource(ABC):
    """Abstract base for all event sources."""

    _registry: ClassVar[dict[str, type["EventSource"]]] = {}
    _namespace: ClassVar[str] = ""

    @property
    def platform_name(self) -> str | None:
        if not self.is_platform:
            return None
        return self._namespace.split("-", 1)[1]

    @classmethod
    def from_string(cls, s: str) -> "EventSource":
        """Parse string to EventSource using namespace registry."""
        namespace = s.split(":")[0]
        source_cls = cls._registry.get(namespace)
        return source_cls.from_string(s)


@dataclass
class CliEventSource(EventSource):
    """Source for CLI-originated events."""
    _namespace = "platform-cli"

    def __str__(self) -> str:
        return "platform-cli:cli-user"
```

### 2. Channel Base Class ([src/mybot/channel/base.py](src/mybot/channel/base.py))

```python
class Channel(ABC, Generic[T]):
    """Abstract base for messaging platforms."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass

    @abstractmethod
    async def run(self, on_message: Callable[[str, T], Awaitable[None]]) -> None:
        """Run the channel. Blocks until stop() is called."""
        pass

    @abstractmethod
    async def reply(self, content: str, source: T) -> None:
        """Reply to incoming message."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and cleanup resources."""
        pass
```

### 3. ChannelWorker ([src/mybot/server/channel_worker.py](src/mybot/server/channel_worker.py))

```python
class ChannelWorker(Worker):
    """Ingests messages from platforms, publishes INBOUND events."""

    async def run(self) -> None:
        """Start all channels and process incoming messages."""
        channel_tasks = [
            channel.run(self._create_callback(channel.platform_name))
            for channel in self.channels
        ]
        await asyncio.gather(*channel_tasks)

    def _create_callback(self, platform: str):
        async def callback(message: str, source: EventSource) -> None:
            session_id = self.context.routing_table.get_or_create_session_id(source)

            event = InboundEvent(
                session_id=session_id,
                source=source,
                content=message,
            )
            await self.context.eventbus.publish(event)

        return callback
```

### 4. DeliveryWorker ([src/mybot/server/delivery_worker.py](src/mybot/server/delivery_worker.py))

```python
class DeliveryWorker(SubscriberWorker):
    """Delivers outbound messages to platforms."""

    async def handle_event(self, event: OutboundEvent) -> None:
        """Handle an outbound message event."""
        session_info = self._get_session_source(event.session_id)
        source = self._get_delivery_source(session_info)

        if source and source.platform_name:
            channel = self._get_channel(source.platform_name)
            if channel:
                await channel.reply(event.content, source)

        self.context.eventbus.ack(event)
```

### 5. RoutingTable ([src/mybot/core/routing.py](src/mybot/core/routing.py))

```python
@dataclass
class RoutingTable:
    """Routes sources to agents using regex bindings."""

    def get_or_create_session_id(self, source: EventSource) -> str:
        """Get existing or create new session_id for source."""
        source_str = str(source)

        # Check for existing session (affinity)
        source_session = self._context.config.sources.get(source_str)
        if source_session:
            return source_session.session_id

        # Create new session
        agent_id = self.resolve(source_str)
        agent_def = self._context.agent_loader.load(agent_id)
        agent = Agent(agent_def, self._context)
        session = agent.new_session(source)

        # Cache session
        self._context.config.set_runtime(
            f"sources.{source_str}", SourceSessionConfig(session_id=session.session_id)
        )

        return session.session_id
```

### 6. Server ([src/mybot/server/server.py](src/mybot/server/server.py))

```python
class Server:
    """Orchestrates workers with queue-based communication."""

    def _setup_workers(self) -> None:
        self.workers = [
            self.context.eventbus,
            AgentWorker(self.context),
            DeliveryWorker(self.context),
        ]

        if self.context.config.channels.enabled:
            self.workers.append(ChannelWorker(self.context))

    async def run(self) -> None:
        self._setup_workers()
        self._start_workers()
        await self._monitor_workers()
```

### 7. Server CLI Command ([src/mybot/cli/server.py](src/mybot/cli/server.py))

```bash
uv run my-bot server
```

Starts the 24/7 server mode with all workers running concurrently.

## How to Run

**CLI Mode (interactive chat):**
```bash
cd 09-channels
uv run my-bot chat
```

**Server Mode (24/7 with channels):**
```bash
cd 09-channels
uv run my-bot server
```

The server mode starts all workers and monitors for crashes with automatic restart.

## Architecture Notes

**Event Flow:**
1. User sends message via platform (CLI, Telegram, Discord)
2. Channel receives message and creates EventSource
3. ChannelWorker creates/picks session via RoutingTable
4. ChannelWorker publishes InboundEvent to EventBus
5. AgentWorker processes event and generates response
6. AgentWorker publishes OutboundEvent to EventBus
7. DeliveryWorker receives OutboundEvent
8. DeliveryWorker looks up session's source and sends via appropriate channel

**Session Affinity:**
- Each EventSource (e.g., "platform-telegram:123:456") maps to one session
- First message creates session, subsequent messages reuse it
- Session ID cached in config.runtime.yaml
- Enables persistent conversations across restarts

**Multi-Agent Routing:**
- RoutingTable matches sources to agents via regex patterns
- Enables different agents for different platforms/users
- Falls back to default_agent if no match

## What's Next

Step 10 will add **WebSocket UI** - real-time web interface for interacting with agents.

```python
class DeliveryWorker(SubscriberWorker):
    """Delivers outbound messages to platforms."""

    async def handle_event(self, event: OutboundEvent) -> None:
        """Handle an outbound message event."""
        session_info = self._get_session_source(event.session_id)
        source = self._get_delivery_source(session_info)

        if source and source.platform_name:
            channel = self._get_channel(source.platform_name)
            if channel:
                await channel.reply(event.content, source)

        self.context.eventbus.ack(event)
```
