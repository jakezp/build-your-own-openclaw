# Step 09: Channels

> Your agent shows up on Telegram. And Discord. And the CLI. All at once.

## Prerequisites

- Steps 00–08 done.
- For Telegram: a bot token from [@BotFather](https://t.me/botfather).
- For Discord: a bot token from the [Developer Portal](https://discord.com/developers/applications) + an invited guild.

```bash
cd 09-channels
uv sync
```

## Note on ChatGPT OAuth concurrency

Multiple channels run under a single process here. They share one
`ChatGPTOAuth` instance, which uses an in-process `asyncio.Lock` to
serialize token reads and refreshes. That's sufficient for one process.
For a multi-process setup, see the file-lock discussion in step 16.

The model allowlist (`default_workspace/models.yaml`) is re-read on
every `Config.load()`, so step 08's hot-reload picks up edits there
without a process restart.

## Why this step exists

Your agent is stuck at the terminal. It's a CLI. If you want to talk to it from your phone, from Discord, from a group chat, from anywhere that isn't where you're sitting right now, you're out of luck.

This step adds **channels** — plug-in adapters that bridge external platforms (Telegram, Discord, and more) into the event bus. Each channel:

- **Listens** on its platform for incoming messages.
- **Publishes** them as `InboundEvent`s on the bus.
- **Subscribes** to `OutboundEvent`s and routes them back to the right platform.

The CLI (from step 07) is now just one channel among many. Nothing special about it. The agent has no idea whether a message came from a terminal, a Telegram chat, a Discord DM, or somewhere else. It just sees `InboundEvent(session_id, content)`.

## The mental model

```
┌──────────────┐      InboundEvent      ┌──────────────┐
│  Telegram    │ ─────────────────────► │              │
│  Channel     │ ◄───── OutboundEvent ─ │              │
└──────────────┘                        │              │
                                        │              │
┌──────────────┐      InboundEvent      │  EventBus    │      ┌─────────────┐
│  Discord     │ ─────────────────────► │              │ ───► │ AgentWorker │
│  Channel     │ ◄───── OutboundEvent ─ │              │      └─────────────┘
└──────────────┘                        │              │
                                        │              │
┌──────────────┐      InboundEvent      │              │
│  CLI         │ ─────────────────────► │              │
│  Channel     │ ◄───── OutboundEvent ─ │              │
└──────────────┘                        └──────────────┘
```

Each channel is two things working together:

- A **ChannelWorker** that runs the platform's client (Telegram bot, Discord bot, CLI input loop) and publishes inbound events.
- A **DeliveryWorker** that subscribes to outbound events and routes them back.

A message in Telegram becomes an `InboundEvent`, gets picked up by `AgentWorker`, produces an `OutboundEvent`, and the Telegram channel's delivery handler sends it back to the original chat.

## Key decisions

### Decision 1: each channel owns an `EventSource`

An `InboundEvent` carries `session_id` and `content`. Neither tells you where to reply. A user on Telegram, a user on Discord, a user in CLI — same session_id structure, different delivery paths.

We solve this with `EventSource` — a per-platform dataclass attached to inbound events:

```python
@dataclass
class TelegramEventSource(EventSource):
    chat_id: int
    user_id: int
    ...
```

The channel publishes `InboundEvent(session_id=..., content=..., source=TelegramEventSource(...))`. The agent processes the event and publishes `OutboundEvent(session_id=..., content=...)`. The Telegram `DeliveryWorker` uses the session_id to look up the last known source and reply to the right chat.

### Decision 2: sessions are per-chat, not per-user

A Telegram bot can be in a group chat with many users. A Discord bot can be in a guild with many channels. Each **conversation context** gets its own session.

The session_id derives from the platform's conversation identifier:

- Telegram: `"telegram:{chat_id}"`
- Discord: `"discord:{channel_id}"`
- CLI: `"cli:{hostname}"`

Different platforms, same `session_id` shape. Different users on the same Telegram chat share a session — they're having a group conversation with the agent.

### Decision 3: whitelisting at the channel layer

Open bots get spammed. Every channel has an `is_allowed(source)` method that checks whether the sender is on the channel's whitelist. Configured per-channel in `config.user.yaml`:

```yaml
channels:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chat_ids: [12345, 67890]
  discord:
    enabled: true
    bot_token: ${DISCORD_BOT_TOKEN}
    allowed_user_ids: [987654321]
```

Messages from non-whitelisted sources are silently dropped at the channel layer, before they ever hit the bus.

### Decision 4: outbound delivery with persistence

A user sent you a message. Your agent replied. But the network blipped and Telegram didn't accept the outbound message.

The `DeliveryWorker` persists failed outbound events to disk and retries them when the channel reconnects. The machinery is platform-specific but the pattern is uniform: "outbound events are not fire-and-forget; they have delivery guarantees."

### Decision 5: channels are optional

Same pattern as web tools in step 06. If `channels.telegram.enabled` is missing or false, the Telegram channel doesn't start. If both are disabled, only the CLI channel runs. Graceful degradation.

## Read the code

### 1. `src/mybot/channel/base.py` — the interface

```python
class Channel(ABC, Generic[T]):
    @property
    @abstractmethod
    def platform_name(self) -> str: ...

    @abstractmethod
    async def run(self, on_message: Callable[[str, T], Awaitable[None]]) -> None: ...

    @abstractmethod
    def is_allowed(self, source: T) -> bool: ...

    @abstractmethod
    async def reply(self, content: str, source: T) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
```

Five methods. `run` is the long-running listener loop (blocks until `stop` is called). `reply` sends a message back to the given source. `is_allowed` checks the whitelist. `from_config(config)` is a static method that builds a list of enabled channels.

### 2. `src/mybot/channel/telegram_channel.py` and `discord_channel.py`

Each is ~100 lines. They use the respective library (`python-telegram-bot`, `discord.py`), register a message handler, build the appropriate `EventSource`, and call `on_message(content, source)` — which is the callback the `ChannelWorker` wires to `bus.publish(InboundEvent(...))`.

### 3. `src/mybot/server/channel_worker.py`

Wraps a `Channel` as a `Worker`. `run()` starts the channel's listener with a callback that publishes inbound events.

### 4. `src/mybot/server/delivery_worker.py`

Subscribes to `OutboundEvent`. Maintains a per-session `EventSource` lookup (the last inbound source we saw for that session). When an outbound arrives, looks up the source and calls the channel's `reply(...)`.

### 5. `src/mybot/core/events.py`

`InboundEvent` now has a `source: EventSource | None` field. `EventSource` is a base dataclass subclassed per-platform.

## Try it out

Set up Telegram:

```yaml
# default_workspace/config.user.yaml
channels:
  telegram:
    enabled: true
    bot_token: 1234:your-bot-token
    allowed_chat_ids: [YOUR_CHAT_ID]
```

Start the server:

```bash
uv run my-bot chat      # CLI channel still works
```

In another terminal, open Telegram and message your bot. The message shows up, the agent replies. The SAME conversation is happening in both your terminal and Telegram simultaneously — two channels, one agent, one session-per-chat.

## Exercises

1. **Add a Slack channel.** `pip install slack-sdk`, write a `SlackChannel(Channel)` subclass. Same shape as Telegram. Wire it up via `from_config` and config.

2. **Watch the whitelist reject a message.** Remove your user ID from the allowlist. Send a message. It's silently dropped. Log inside `is_allowed` to see the rejection.

3. **Cross-channel continuity.** Modify `session_id` generation so a user has one session regardless of platform (e.g., hash their email). Send a message from CLI, then reply from Telegram — the conversation continues. Is this always desirable? (Hint: group chat on Telegram.)

4. **Break the delivery.** Force `TelegramChannel.reply()` to raise an exception. Watch the DeliveryWorker persist the failed event. Restore `reply()`. Watch retry.

## What breaks next

Channels give you "the agent meets users via messaging platforms." What about **programmatic access**? Not a human at a terminal, not a human on Telegram — a script that wants to chat with the agent over a protocol.

Step 10 adds a WebSocket server. Any WebSocket client can connect and exchange messages. Same event bus, same session model, different transport.

## What's Next

[Step 10: WebSocket](../10-websocket/) — programmatic access to the agent.
