"""Agent and AgentSession for step 03 with persistence support."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from litellm.types.completion import (
    ChatCompletionMessageParam as Message,
    ChatCompletionMessageToolCallParam,
)

from src.core.history import HistoryMessage, HistoryStore
from src.core.skill_loader import SkillLoader
from src.provider.llm import LLMProvider, LLMToolCall
from src.tools.registry import ToolRegistry
from src.tools.skill_tool import create_skill_tool

if TYPE_CHECKING:
    from src.core.agent_loader import AgentDef
    from src.utils.config import Config


@dataclass
class SessionState:
    """Pure conversation state container."""

    session_id: str
    agent: "Agent"
    messages: list[Message] = field(default_factory=list)
    _history: HistoryStore | None = field(default=None, repr=False)

    def add_message(self, message: Message) -> None:
        """Add message to conversation history."""
        self.messages.append(message)

        # Save to history if available
        if self._history:
            history_msg = HistoryMessage.from_message(message)
            self._history.save_message(self.session_id, history_msg)

    def build_messages(self) -> list[Message]:
        """Build messages list with system prompt."""
        system_prompt = self.agent.agent_def.agent_md
        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages

    @classmethod
    def from_history(
        cls, session_id: str, agent: "Agent", history: HistoryStore
    ) -> "SessionState":
        """Load session state from history."""
        # Get messages from history
        history_messages = history.get_messages(session_id)
        messages = [hm.to_message() for hm in history_messages]

        return cls(
            session_id=session_id,
            agent=agent,
            messages=messages,
            _history=history,
        )


@dataclass
class AgentSession:
    """Chat orchestrator - operates on swappable SessionState."""

    agent: "Agent"
    state: SessionState
    tools: ToolRegistry
    started_at: datetime = field(default_factory=datetime.now)
    history: HistoryStore | None = field(default=None)

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

        tool_schemas = self.tools.get_tool_schemas()

        while True:
            messages = self.state.build_messages()
            content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)

            tool_call_dicts: list[ChatCompletionMessageToolCallParam] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in tool_calls
            ]
            assistant_msg: Message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_call_dicts,
            }
            self.state.add_message(assistant_msg)

            if not tool_calls:
                break

            await self._handle_tool_calls(tool_calls)

            continue

        return content

    async def _handle_tool_calls(
        self,
        tool_calls: list[LLMToolCall],
    ) -> None:
        """
        Handle tool calls from the LLM response.

        Args:
            tool_calls: List of tool calls from LLM response
        """
        tool_call_results = await asyncio.gather(
            *[self._execute_tool_call(tool_call) for tool_call in tool_calls]
        )

        for tool_call, result in zip(tool_calls, tool_call_results):
            tool_msg: Message = {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
            }
            self.state.add_message(tool_msg)

    async def _execute_tool_call(
        self,
        tool_call: LLMToolCall,
    ) -> str:
        """
        Execute a single tool call.

        Args:
            tool_call: Tool call from LLM response

        Returns:
            Tool execution result
        """
        try:
            args = json.loads(tool_call.arguments)
        except json.JSONDecodeError:
            args = {}

        try:
            result = await self.tools.execute_tool(tool_call.name, session=self, **args)
        except Exception as e:
            result = f"Error executing tool: {e}"

        return result


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
        self.skill_loader = SkillLoader.from_config(config)
        self.history = HistoryStore.from_config(config)

    def new_session(self, session_id: str | None = None) -> AgentSession:
        """
        Create a new conversation session.

        Args:
            session_id: Optional session ID (generated if not provided)

        Returns:
            A new AgentSession instance.
        """
        session_id = session_id or str(uuid.uuid4())

        # Create session in history
        self.history.create_session(self.agent_def.id, session_id)

        state = SessionState(
            session_id=session_id,
            agent=self,
            messages=[],
            _history=self.history,
        )

        # Create tool registry with builtins
        tools = ToolRegistry.with_builtins()

        # Add skill tool if skills are available
        skill_tool = create_skill_tool(self.skill_loader)
        if skill_tool:
            tools.register(skill_tool)

        return AgentSession(agent=self, state=state, tools=tools, history=self.history)

    def load_session(self, session_id: str) -> AgentSession:
        """
        Load an existing conversation session from history.

        Args:
            session_id: Session ID to load

        Returns:
            AgentSession with restored conversation history
        """
        state = SessionState.from_history(session_id, self, self.history)

        # Create tool registry with builtins
        tools = ToolRegistry.with_builtins()

        # Add skill tool if skills are available
        skill_tool = create_skill_tool(self.skill_loader)
        if skill_tool:
            tools.register(skill_tool)

        return AgentSession(agent=self, state=state, tools=tools, history=self.history)