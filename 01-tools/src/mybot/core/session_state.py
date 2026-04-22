from dataclasses import dataclass
from typing import TYPE_CHECKING, Any



Message = dict[str, Any]
if TYPE_CHECKING:
    from mybot.core.agent import Agent

@dataclass
class SessionState:
    """Pure conversation state container."""

    session_id: str
    agent: "Agent"
    messages: list[Message]

    def add_message(self, message: Message) -> None:
        """Add message to conversation history."""
        self.messages.append(message)

    def build_messages(self) -> list[Message]:
        """Build messages list with system prompt."""
        system_prompt = self.agent.agent_def.agent_md
        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages
