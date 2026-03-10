"""Chat CLI command for interactive sessions with slash commands."""

import asyncio
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich import print as rprint
from rich.prompt import Prompt
from rich.text import Text

from src.core.agent import Agent
from src.core.agent_loader import AgentLoader

if TYPE_CHECKING:
    from src.utils.config import Config


class ChatLoop:
    """Interactive chat session with slash commands."""

    def __init__(self, config: "Config", agent_id: str | None = None):
        self.config = config
        self.console = Console()

        # Load agent
        loader = AgentLoader(config)
        agent_id = agent_id or config.default_agent
        self.agent_def = loader.load(agent_id)

        # Create agent and session
        self.agent = Agent(self.agent_def, config)
        self.session = self.agent.new_session()

    def get_user_input(self) -> str:
        """Get user input with styled prompt.

        Returns:
            Trimmed user input
        """
        prompt_text = Text("You", style="cyan")
        user_input = Prompt.ask(prompt_text, console=self.console)
        return user_input.strip()

    def display_agent_response(self, content: str) -> None:
        """Display agent response with styled prefix.

        Args:
            content: Agent response content
        """
        prefix = Text(f"{self.agent.agent_def.id}: ", style="green")

        self.console.print(prefix, end="")
        self.console.print(content)

    async def run(self) -> None:
        """Run the interactive chat loop."""
        rprint(
            Panel(
                Text("Welcome to your-own-bot!", style="bold cyan"),
                title="Chat",
                border_style="cyan",
            )
        )
        rprint("Type '/help' for commands, 'quit' or 'exit' to end.\n")

        try:
            while True:
                user_input = await asyncio.to_thread(self.get_user_input)

                if user_input.lower() in ("quit", "exit", "q"):
                    rprint("\n[bold yellow]Goodbye![/bold yellow]")
                    break

                if not user_input:
                    continue

                try:
                    # Check for slash commands
                    cmd_response = await self.session.command_registry.dispatch(
                        user_input, self.session
                    )
                    if cmd_response is not None:
                        rprint(cmd_response)
                        continue

                    # Normal chat
                    response = await self.session.chat(user_input)
                    self.display_agent_response(response)
                except Exception as e:
                    rprint(f"\n[bold red]Error:[/bold red] {e}\n")

        except (KeyboardInterrupt, EOFError):
            rprint("\n[bold yellow]Goodbye![/bold yellow]")


def chat_command(config: "Config", agent_id: str | None = None) -> None:
    """Start interactive chat session."""
    chat_loop = ChatLoop(config, agent_id=agent_id)
    asyncio.run(chat_loop.run())
