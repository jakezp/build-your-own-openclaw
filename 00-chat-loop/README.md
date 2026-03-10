# Step 00: Just a Chat Loop

Build a minimal chat bot that can have a conversation using an LLM.

## Prerequisites

Copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key for different providers
```

## What We will Build?

### Architecture

<!-- TODO: Diagram -->
```
User Input → ChatLoop → AgentSession → Agent → LLM → Response
                              ↑
                         Message History
```

### Key Components

- **ChatLoop**: Handles user input and displays responses
- **AgentSession**: Manages conversation state and message history, LLM always see the full history.
- **Agent**: Coordinates between session and LLM
- **Message**: Simple `{role, content}` pairs stored in history


## Key Changes

[src/cli/chat.py](src/cli/chat.py)

```python
class ChatLoop:
    def __init__(self, config: Config, agent_id: str | None = None):
        self.config = config
        self.console = Console()

        loader = AgentLoader(config)
        agent_id = agent_id or config.default_agent
        self.agent_def = loader.load(agent_id)

        self.agent = Agent(self.agent_def, config)
        self.session = self.agent.new_session()

    async def run(self) -> None:
        """Run the interactive chat loop."""
        self.console.print(
            Panel(
                Text("Welcome to my-bot!", style="bold cyan"),
                title="Chat",
                border_style="cyan",
            )
        )
        self.console.print("Type 'quit' or 'exit' to end the session.\n")

        try:
            while True:
                user_input = await asyncio.to_thread(self.get_user_input)

                if user_input.lower() in ("quit", "exit", "q"):
                    self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
                    break

                if not user_input:
                    continue

                try:
                    response = await self.session.chat(user_input)
                    self.display_agent_response(response)
                except Exception as e:
                    self.console.print(f"\n[bold red]Error:[/bold red] {e}\n")

        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
```

[src/core/agent.py](src/core/agent.py)

``` python
@dataclass
class AgentSession:
    agent: Agent
    state: SessionState
    started_at: datetime = field(default_factory=datetime.now)

    async def chat(self, message: str) -> str:
        user_msg: Message = {"role": "user", "content": message}
        self.state.add_message(user_msg)

        messages = self.state.build_messages()
        response = await self.agent.llm.chat(messages)

        assistant_msg: Message = {"role": "assistant", "content": response}
        self.state.add_message(assistant_msg)

        return response
```

## Notes

The file structure is complicated intentionally to avoid big refaction in future steps.

## How to Run

```bash
cd 00-chat-loop
uv run my-bot chat

# Type 'quit' or 'exit' to end the session.

# You: Hello, who is this?
# pickle: Meow! Hello there! I'm Pickle, your friendly cat assistant. 🐾
# You: I am Zane, Nice to meet you.
# pickle: Nice to meet you, Zane! *purrs happily* 🐱
```

## What's Next

[Step 01: Tools](../01-tools/) - Give your agent the ability to take actions