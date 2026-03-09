from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from src.core.agent import Agent

@dataclass
class SessionState:
    """Pure conversation state container."""

    session_id: str
    agent: "Agent"
    messages: list[Message] = field(default_factory=list)

    def add_message(self, message: Message) -> None:
        """Add message to conversation history."""
        self.messages.append(message)

    def build_messages(self) -> list[Message]:
        """Build messages list with system prompt."""
        system_prompt = self.agent.agent_def.agent_md
        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages
