"""Agent and AgentSession for step 05 with compaction support."""

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

from src.core.commands.registry import CommandRegistry
from src.core.context_guard import ContextGuard
from src.core.history import HistoryStore
from src.core.session_state import SessionState
from src.core.skill_loader import SkillLoader
from src.provider.llm import LLMProvider, LLMToolCall
from src.tools.registry import ToolRegistry
from src.tools.skill_tool import create_skill_tool

if TYPE_CHECKING:
    from src.core.agent_loader import AgentDef
    from src.utils.config import Config


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
        self.history_store = HistoryStore.from_config(config)
        self.command_registry = CommandRegistry.with_builtins()

    def _build_tools(self) -> ToolRegistry:
        """
        Build a ToolRegistry with tools appropriate for the session.

        Returns:
            ToolRegistry with base tools + optional tools
        """
        registry = ToolRegistry.with_builtins()

        # Add skill tool if skills are available
        skill_tool = create_skill_tool(self.skill_loader)
        if skill_tool:
            registry.register(skill_tool)

        return registry

    def _get_token_threshold(self) -> int:
        """Get token threshold based on model's context window."""
        # Default to 80% of 200k context
        return 160000

    def new_session(self, session_id: str | None = None) -> "AgentSession":
        """
        Create a new conversation session.

        Args:
            session_id: Optional session ID (generated if not provided)

        Returns:
            A new AgentSession instance.
        """
        session_id = session_id or str(uuid.uuid4())
        tools = self._build_tools()

        # Create context guard for this session
        context_guard = ContextGuard(
            token_threshold=self._get_token_threshold(),
        )

        state = SessionState(
            session_id=session_id,
            agent=self,
            messages=[],
            history_store=self.history_store,
        )

        session = AgentSession(
            agent=self,
            state=state,
            context_guard=context_guard,
            tools=tools,
            command_registry=self.command_registry,
        )
        self.history_store.create_session(self.agent_def.id, session_id)

        return session


@dataclass
class AgentSession:
    """Chat orchestrator - operates on swappable SessionState."""

    agent: Agent
    state: SessionState
    context_guard: ContextGuard
    tools: ToolRegistry
    command_registry: CommandRegistry
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

        tool_schemas = self.tools.get_tool_schemas()

        while True:
            messages = self.state.build_messages()

            self.state = await self.context_guard.check_and_compact(self.state)

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
        # Extract key arguments
        try:
            args = json.loads(tool_call.arguments)
        except json.JSONDecodeError:
            args = {}

        try:
            result = await self.tools.execute_tool(tool_call.name, session=self, **args)
        except Exception as e:
            result = f"Error executing tool: {e}"

        return result
