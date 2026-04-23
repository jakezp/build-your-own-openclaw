# Step 14: Post Message Back

> Your agent speaks first.

## Prerequisites

- Steps 00–13 done.
- At least one channel configured (from step 09 — Telegram, Discord, or WebSocket).

```bash
cd 14-post-message-back
uv sync
```

## Why this step exists

Step 12's cron lets the agent do work on a schedule. It fires at 7am, the agent thinks, the agent produces a reply — and nobody's there to see it. The agent's response ends up in the session history but no channel delivers it. No push notification, no message in the user's chat, nothing.

Sessions triggered by humans get their replies naturally. The user typed something; the `OutboundEvent` flows back to whichever channel brought the `InboundEvent`. But cron-triggered sessions have no "original channel" — cron isn't a user sitting at a terminal. The reply needs a destination.

The fix is a new tool: `post_message`. The agent calls it explicitly. The tool publishes an `OutboundEvent` targeted at the configured default delivery channel. `DeliveryWorker` picks it up and routes it to whichever channel the user designated as their preferred destination.

Now: cron fires, agent researches, agent writes a morning brief, agent calls `post_message("Here's your 7am briefing: ...")`, the user's Telegram chat pings.

## The mental model

Two ways an `OutboundEvent` enters the bus now:

1. **Implicit (existing).** User sends an `InboundEvent` from Telegram. Agent replies. `AgentWorker._emit_response` publishes an `OutboundEvent`. `DeliveryWorker` routes it back to Telegram via `source` tracking.

2. **Explicit (new).** Agent decides on its own to send a message. It calls `post_message("...")`. The tool publishes an `OutboundEvent` with an `AgentEventSource` (not a platform source). `DeliveryWorker` sees there's no user-facing source and falls back to the **default delivery channel** from config.

The user configures their default channel once:

```yaml
default_delivery_source: "telegram:12345"
```

Now any agent-initiated message (cron results, background work, proactive notifications) lands there.

## Key decisions

### Decision 1: a tool, not an automatic side-effect

We could have made cron results auto-deliver to the default channel without the agent doing anything explicit. We didn't.

Why require the agent to call `post_message` explicitly?

- **Intentionality.** The agent decides WHEN to send. Sometimes a cron runs and the right answer is "nothing noteworthy happened, don't bother the user." Auto-delivery would spam.
- **Content control.** The agent decides WHAT to send. The session's final message might be "let me think about this for a moment" — not worth delivering. The agent picks the right summary.
- **Multi-message sessions.** A single cron-triggered session might produce several deliverable findings. The agent can call `post_message` multiple times, each with a different message.

Treating delivery as a tool call gives the agent fine-grained control. At the cost of: the agent has to remember to use it. The agent's prompt (step 13's channel hint) reminds it: "Your response will not be sent to user directly. Use post_message to deliver findings."

### Decision 2: tool only registers if channels are configured

```python
if not config.channels.enabled:
    return None
if not context.channels:
    return None
```

Same graceful-degradation pattern as web tools (step 06). If the user has no channels set up, there's no point offering a tool that delivers via channels. The tool disappears from the schema.

### Decision 3: `AgentEventSource` distinguishes proactive from responsive

When the delivery worker receives an `OutboundEvent`, it needs to know where to send it. Two cases:

- **Responsive reply.** `OutboundEvent.source` is a platform source (`TelegramEventSource`, etc). Deliver to that platform's chat.
- **Proactive post.** `OutboundEvent.source` is an `AgentEventSource`. No chat id. Deliver to `config.default_delivery_source`.

The `is_agent` flag on `AgentEventSource` (used in step 13's channel hint too) is the distinguisher. Same dataclass does double duty — tells the prompt builder "you're a subagent" and tells the delivery worker "find the default destination."

### Decision 4: `post_message` returns "queued for delivery," not "delivered"

The tool returns as soon as the event is published:

```python
await context.eventbus.publish(event)
return "Message queued for delivery"
```

It doesn't wait for the delivery to succeed. The string "Message queued for delivery" goes back to the agent as the tool result; the agent can't tell whether the actual Telegram send worked.

This is async by design. `DeliveryWorker` handles retries, failures, and persistence. The agent's job is to produce content; the delivery system's job is to deliver. Coupling them would mean the agent waits on a network call every time it sends — slow and fragile.

If you need "did it deliver?" feedback, that's a different pattern (step 16 has more on this).

## Read the code

### 1. `src/mybot/tools/post_message_tool.py` — the factory

```python
def create_post_message_tool(context: "SharedContext") -> BaseTool | None:
    config = context.config

    if not config.channels.enabled:
        return None
    if not context.channels:
        return None

    @tool(
        name="post_message",
        description="Send a message to the user via the default messaging platform. ...",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send to the user",
                }
            },
            "required": ["content"],
        },
    )
    async def post_message(content: str, session: "AgentSession") -> str:
        try:
            event = OutboundEvent(
                session_id=session.session_id,
                source=AgentEventSource(agent_id=session.agent.agent_def.id),
                content=content,
                timestamp=time.time(),
            )
            await context.eventbus.publish(event)
            return "Message queued for delivery"
        except Exception as e:
            return f"Failed to send message: {e}"

    return post_message
```

A factory (step 06 pattern). Skip if unconfigured, return a tool if configured. The tool body: build an `OutboundEvent` with `AgentEventSource`, publish to the bus. `DeliveryWorker` does the rest.

### 2. `DeliveryWorker` updates

(`src/mybot/server/delivery_worker.py` — read it.) The delivery worker handles `OutboundEvent` with two branches:

```python
async def deliver(self, event: OutboundEvent) -> None:
    source = event.source

    if source.is_agent:
        # Proactive post from an agent. Use the default delivery channel.
        default = self.context.config.default_delivery_source
        if not default:
            logger.warning("No default_delivery_source configured; dropping")
            return
        target_source = self._resolve_source_from_string(default)
    else:
        # Responsive reply. Use the source the inbound came from.
        target_source = source

    await self._send_via_channel(target_source, event.content)
```

The critical thing: `target_source` for agent-initiated events is different from the event's own source. The event carries `AgentEventSource("pickle")`; the delivery uses a `TelegramEventSource(chat_id=...)` looked up from config.

### 3. Agent registers the tool

In `_build_tools`:

```python
post_tool = create_post_message_tool(self.context)
if post_tool:
    registry.register(post_tool)
```

Same pattern as web tools, skill tool, subagent tool. Conditional on config.

## Try it out

1. Configure your default delivery channel. Edit `default_workspace/config.user.yaml`:

```yaml
default_delivery_source: "telegram:YOUR_CHAT_ID"
```

2. Make a test cron:

```bash
mkdir -p ../default_workspace/crons/morning-brief
cat > ../default_workspace/crons/morning-brief/CRON.md <<'EOF'
---
name: Morning Brief
description: Daily summary delivery test
agent: pickle
schedule: "*/10 * * * *"
one_off: true
---

Compose a one-sentence greeting for the user. Use post_message to deliver it.
EOF
```

3. Start the server. Within 10 minutes your Telegram (or configured channel) will ping with a one-line greeting. The cron's session ran, pickle called `post_message`, `DeliveryWorker` shipped it to your Telegram.

## Exercises

1. **Trigger post_message manually.** From a chat, tell the agent: `"Send me a message using post_message with content 'hello from agent-initiated delivery'."` Watch Telegram ping.

2. **Multi-message cron.** Write a cron that asks the agent to call `post_message` three times with different snippets. Count the Telegram pings.

3. **Break delivery.** Remove `default_delivery_source` from config. Run the cron. Check logs: `"No default_delivery_source configured; dropping"`. The agent's tool call succeeded; delivery silently dropped. What would you improve about this error handling?

4. **Distinguish your notifications.** Add a prefix `"[scout]"` to the content inside `post_message` based on `session.agent.agent_def.id`. Now you can tell which agent delivered which message.

## What breaks next

One agent does all the work. A big workspace might want specialized agents — one for research, one for writing, one for memory lookups. The main agent should be able to **call** these sub-agents and incorporate their results.

Step 15 adds `dispatch_agent`: a tool one agent uses to invoke another, wait for its reply, and feed it back into the conversation. This is how step 17 (memory) works — pickle asks cookie for facts, cookie looks them up, pickle continues with the context.

## What's Next

[Step 15: Agent Dispatch](../15-agent-dispatch/) — your agent wants friends to work with.
