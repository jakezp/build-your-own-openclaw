# Step 14: Post Message Back

Agent can now initiate communication via the `post_message` tool.

## Prerequisites

Same as previous steps - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We Built

### Architecture

```
Cron Event
      ↓
AgentSession (with post_message tool)
      ↓
post_message tool called
      ↓
OutboundEvent published to EventBus
      ↓
DeliveryWorker delivers to platform
```

### Key Components

- **post_message_tool**: Factory that creates the tool when channels enabled
- **Agent._build_tools()**: Conditionally registers tool based on source type
- **DeliveryWorker**: Handles OutboundEvent delivery to platforms

## Key Changes

[src/mybot/tools/post_message_tool.py](src/mybot/tools/post_message_tool.py) - NEW: Agent-initiated messaging

```python
def create_post_message_tool(context: "SharedContext") -> BaseTool | None:
    """Factory to create post_message tool."""
    config = context.config

    # Return None if channels not enabled or no channels configured
    if not config.channels.enabled:
        return None

    if not context.channels:
        return None

    @tool(
        name="post_message",
        description="Send a message to the user via the default messaging platform...",
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
        event = OutboundEvent(
            session_id=session.session_id,
            source=AgentEventSource(agent_id=session.agent.agent_def.id),
            content=content,
            timestamp=time.time(),
        )
        await context.eventbus.publish(event)
        return "Message queued for delivery"

    return post_message
```

[src/mybot/core/agent.py](src/mybot/core/agent.py) - Conditional tool registration

```python
def _build_tools(self, include_post_message: bool) -> ToolRegistry:
    """Build a ToolRegistry with tools appropriate for the session."""
    registry = ToolRegistry.with_builtins()

    # ... other tools ...

    if include_post_message:
        post_tool = create_post_message_tool(self.context)
        if post_tool:
            registry.register(post_tool)

    return registry

def new_session(self, source: EventSource, session_id: str | None = None):
    # Only include post_message for cron sources
    include_post_message = source.is_cron
    tools = self._build_tools(include_post_message)
```

## How to Run

```bash
cd 14-post-message-back
uv run my-bot chat

# The post_message tool is available when:
# 1. Channels are enabled in config (channels.enabled: true)
# 2. At least one channel is configured (telegram or discord)
# 3. The session is triggered by a cron job

# Example: Cron job that sends a notification
# In default_workspace/crons/morning-reminder/CRON.md:
# ---
# schedule: "0 9 * * *"
# agent: pickle
# ---
# Send a morning greeting to the user using the post_message tool.
```

## Benefits

1. **Proactive Communication**: Agent can initiate messages (not just respond)
2. **Cron Integration**: Scheduled tasks can notify users of results
3. **Source-Based Registration**: Tool only available when appropriate

## What's Next

[Step 15: Agent Dispatch](../15-agent-dispatch/) - Multi-agent collaboration
