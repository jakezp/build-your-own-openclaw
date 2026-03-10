# Step 04: Slash Commands - User Control

Add slash commands to control the conversation and inspect agent state.

## Prerequisites

Same as Step 00 - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We will Build?

### Architecture

```
User Input → ChatLoop → CommandRegistry.dispatch() ─→ Command → Response
                           │
                           └─→ (not a command) → AgentSession.chat() → LLM
```

### Key Components

- **Command**: Base class for slash commands (async execute method)
- **CommandRegistry**: Registers and dispatches commands
- **Commands**: `/help`, `/skills`, `/session`

## Key Changes

[src/core/commands/base.py](src/core/commands/base.py) - New file

```python
class Command(ABC):
    """Base class for slash commands."""

    name: str
    aliases: list[str] = []
    description: str = ""

    @abstractmethod
    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute the command and return response string."""
        pass
```

[src/core/commands/registry.py](src/core/commands/registry.py) - New file

```python
class CommandRegistry:
    def register(self, cmd: Command) -> None:
        """Register a command and its aliases."""

    async def dispatch(self, input: str, session: "AgentSession") -> str | None:
        """Parse and execute a slash command. Returns None if not a command."""
```

[src/cli/chat.py](src/cli/chat.py) - Add command dispatch

```python
# Check for slash commands
cmd_response = await self.session.command_registry.dispatch(
    user_input, self.session
)
if cmd_response is not None:
    self.console.print(cmd_response)
    continue

# Normal chat
response = await self.session.chat(user_input)
```

## How to Run

```bash
cd 04-slash-commands
uv run my-bot chat

# Try the commands:
# You: /help
# **Available Commands:**
# /help, /? - Show available commands
# /skills - List all skills or show skill details
# /session - Show current session details

# You: /session
# **Session ID:** `abc123...`
# **Agent:** Pickle (pickle)
# **Created:** 2026-03-08T12:00:00
# **Messages:** 0
```

## What's Next

[Step 05: Compaction](../05-compaction/) - Handle long conversations with context management
