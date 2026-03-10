# Step 05: Compaction - Handle Long Conversations

Manage context window size with proactive compaction to enable long conversations.

## Prerequisites

Same as Step 00 - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We will Build?

### Architecture

```
User Input → AgentSession.chat() → ContextGuard.check_and_compact()
                                        │
                                        ├─→ Under threshold → LLM call
                                        │
                                        └─→ Over threshold → Truncate tool results
                                                            → Still over? Summarize old messages
                                                            → Then LLM call
```

### Key Components

- **ContextGuard**: Manages context window size with token counting and compaction
- **Token Estimation**: Uses litellm's token_counter for accurate estimates
- **Truncation Strategy**: First truncates large tool results, then summarizes old messages
- **Commands**: `/compact` (manual), `/context` (show usage)

## Key Changes

[src/core/context_guard.py](src/core/context_guard.py) - New file

```python
@dataclass
class ContextGuard:
    token_threshold: int = 160000  # 80% of 200k context

    def estimate_tokens(self, state: SessionState) -> int:
        return token_counter(model=state.agent.agent_def.llm.model, messages=state.build_messages())

    async def check_and_compact(self, state: SessionState) -> SessionState:
        # First: truncate large tool results
        # Then: summarize old messages if still over threshold
```

[src/core/agent.py](src/core/agent.py) - Integration

```python
async def chat(self, message: str) -> str:
    # ... add user message ...

    while True:
        messages = self.state.build_messages()
        # Check and compact before LLM call
        self.state = await self.context_guard.check_and_compact(self.state)
        content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)
```

[src/core/commands/handlers.py](src/core/commands/handlers.py) - New commands

```python
class CompactCommand(Command):
    name = "compact"
    async def execute(self, args: str, session: AgentSession) -> str:
        # Force compaction manually

class ContextCommand(Command):
    name = "context"
    async def execute(self, args: str, session: AgentSession) -> str:
        # Show token usage: "Tokens: 45,000 (28.1% of 160,000 threshold)"
```

## How to Run

```bash
cd 05-compaction
uv run your-own-bot chat

# Check context usage anytime:
# You: /context
# **Messages:** 12
# **Tokens:** 15,420 (9.6% of 160,000 threshold)

# You: /compact
# ✓ Context compacted. 8 messages retained.
```

## What's Next

[Step 06: Web Tools](../06-web-tools/) - Add web search and URL reading capabilities
