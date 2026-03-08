"""Agent and AgentSession for step 00."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from litellm.types.completion import ChatCompletionMessageParam as Message

from src.provider.llm import LLMProvider

if TYPE_CHECKING:
    from src.core.agent_loader import AgentDef
    from src.utils.config import Config


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


@dataclass
class AgentSession:
    """Chat orchestrator - operates on swappable SessionState."""

    agent: "Agent"
    state: SessionState
    started_at: datetime = field(default_factory=datetime.now)

    @property
    def session_id(self) -> str:
        """Delegate to state."""
        return self.state.session_id

    async def chat(self, message: str) -> str:
        """
        Send a message to the LLM and get a response.

        Args:
            message: User message

        Returns:
            Assistant's response text
        """
        user_msg: Message = {"role": "user", "content": message}
        self.state.add_message(user_msg)

        messages = self.state.build_messages()
        response = await self.agent.llm.chat(messages)

        assistant_msg: Message = {"role": "assistant", "content": response}
        self.state.add_message(assistant_msg)

        return response


class Agent:
    """
    A configured agent that creates and manages conversation sessions.

    Agent is a factory for sessions and holds the LLM and config
    that sessions use for chatting.
    """

    def __init__(self, agent_def: "AgentDef", config: "Config") -> None:
        self.agent_def = agent_def
        self.config = config
        self.llm = LLMProvider.from_config(agent_def.llm)

    def new_session(self, session_id: str | None = None) -> AgentSession:
        """
        Create a new conversation session.

        Args:
            session_id: Optional session ID (generated if not provided)

        Returns:
            A new AgentSession instance.
        """
        session_id = session_id or str(uuid.uuid4())

        state = SessionState(
            session_id=session_id,
            agent=self,
            messages=[],
        )

        return AgentSession(agent=self, state=state)
