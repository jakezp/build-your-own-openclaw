"""Session state container with persistence helpers.

"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from litellm.types.completion import ChatCompletionMessageParam as Message

from src.core.history import HistoryMessage

if TYPE_CHECKING:
    from src.core.agent import Agent
    from src.core.history import HistoryStore


@dataclass
class SessionState:
    """Pure conversation state + persistence."""

    session_id: str
    agent: "Agent"
    messages: list[Message]
    history_store: "HistoryStore"

    def add_message(self, message: Message) -> None:
        """Add message to in-memory list + persist."""
        self.messages.append(message)

        # Save to history if available
        if self.history_store:
            history_msg = HistoryMessage.from_message(message)
            self.history_store.save_message(self.session_id, history_msg)

    def build_messages(self) -> list[Message]:
        """Build messages list with system prompt."""
        system_prompt = self.agent.agent_def.agent_md
        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages
