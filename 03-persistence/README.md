# Step 03: Persistence - Remember Conversations

Save and restore conversation history so the agent remembers past interactions.

## Prerequisites

Same as Step 00 - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We will Build?

### Architecture

```
User Input → ChatLoop → AgentSession → Agent → LLM
                              ↓              ↑
                         ToolRegistry ← Tool Calls
                              ↓
                         HistoryStore
                              ↓
                     .sessions/sessions/{id}.jsonl
```

### Key Components

- **HistoryStore**: JSONL file-based storage for sessions and messages
- **HistorySession**: Session metadata (id, agent_id, title, message_count)
- **HistoryMessage**: Message format with conversion to/from litellm format
- **Session recovery**: Auto-recover last session on startup

## Key Changes

[src/core/history.py](src/core/history.py) - New file

```python
class HistoryStore:
    """JSONL file-based history storage."""

    def create_session(self, agent_id: str, session_id: str) -> dict:
        """Create a new conversation session."""

    def save_message(self, session_id: str, message: HistoryMessage) -> None:
        """Save a message to history."""

    def get_messages(self, session_id: str) -> list[HistoryMessage]:
        """Get all messages for a session."""
```

[src/core/agent.py](src/core/agent.py) - Modified

```python
class Agent:
    def new_session(self, session_id: str | None = None) -> AgentSession:
        """Create a new session and save to history."""

    def load_session(self, session_id: str) -> AgentSession:
        """Load an existing session from history."""

    def get_last_session(self) -> str | None:
        """Get the most recent session ID."""
```

## How to Run

```bash
cd 03-persistence
uv run your-own-bot chat

# Each run starts a new session
# Messages are saved to .sessions/ directory
```

## Storage Structure

```
.sessions/
├── index.jsonl              # Session metadata
└── sessions/
    └── {session_id}.jsonl   # Messages (one file per session)
```

## What's Next

[Step 04: Compaction](../04-compaction/) - Handle long conversations with context management
